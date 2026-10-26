"""Background jobs. Run any number of workers: the decision stream is shared through a
consumer group, and periodic jobs take a Redis lease so one instance runs each per interval.

Jobs:
  decision_drain   write buffered routing decisions to Postgres (every worker)
  rollups          aggregate closed hours into decision_rollups_hourly (benchmark traffic excluded)
  health_probe     check each endpoint's model listing; failures count against its models,
                   success lets a cooled-down circuit close
  benchmarks       execute queued benchmark runs
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import orjson
from redis.exceptions import RedisError
from sqlalchemy import func, select, text, update

from smartrouter.alerts.service import circuit_opened
from smartrouter.benchmarks.models import BenchmarkRun
from smartrouter.benchmarks.router import QUEUE_KEY
from smartrouter.benchmarks.runner import BenchmarkRunner
from smartrouter.core.config import Settings, get_settings
from smartrouter.core.container import Container
from smartrouter.core.logs import configure_logging
from smartrouter.core.tracing import configure_tracing
from smartrouter.decisions.models import RoutingDecision
from smartrouter.decisions.recorder import GROUP, STREAM_KEY, DecisionRecorder, decode
from smartrouter.gateway.catalog import load_catalog
from smartrouter.health.tracker import HealthTracker

log = logging.getLogger("smartrouter.worker")
Entry = tuple[bytes, dict[bytes, bytes]]

ROLLUP_SQL = text(
    """
    INSERT INTO decision_rollups_hourly AS r (
      hour, project_id, policy_name, task, final_model, status, requests, fallbacks, cost_nusd,
      baseline_cost_nusd, latency_ms_sum, input_tokens, output_tokens)
    SELECT date_trunc('hour', d.created_at),
           coalesce(d.project_id, '00000000-0000-0000-0000-000000000000'::uuid),
           coalesce(d.policy_name, 'passthrough'), d.task, coalesce(d.final_model, ''), d.status,
           count(*), sum(CASE WHEN d.attempts > 1 THEN 1 ELSE 0 END), sum(d.actual_cost_nusd),
           sum(CASE WHEN d.status = 'SUCCESS' THEN d.baseline_cost_nusd ELSE 0 END),
           sum(d.latency_ms), sum(d.input_tokens), sum(d.output_tokens)
      FROM routing_decisions d
     WHERE d.created_at >= :start AND d.created_at < :end AND d.source <> 'benchmark'
     GROUP BY 1, 2, 3, 4, 5, 6
    ON CONFLICT (hour, project_id, policy_name, task, final_model, status)
    DO UPDATE SET requests = excluded.requests, fallbacks = excluded.fallbacks,
      cost_nusd = excluded.cost_nusd, baseline_cost_nusd = excluded.baseline_cost_nusd,
      latency_ms_sum = excluded.latency_ms_sum, input_tokens = excluded.input_tokens,
      output_tokens = excluded.output_tokens
    """
)


class Worker:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.settings = container.settings
        self._stop = asyncio.Event()
        self.consumer = f"{socket.gethostname()}-{os.getpid()}"
        self.recorder = DecisionRecorder(container.db.sessionmaker)

    def stop(self) -> None:
        self._stop.set()

    async def _lease(self, job: str, seconds: int) -> bool:
        try:
            return bool(await self.c.redis.set(f"sr:worker:lease:{job}", b"1", nx=True, ex=seconds))
        except RedisError:
            return False

    async def run_job(self, name: str, interval_s: int, fn: Callable[[], Awaitable[int]]) -> None:
        while not self._stop.is_set():
            if await self._lease(name, max(1, interval_s - 1)):
                try:
                    count = await fn()
                    if count:
                        log.info("job done", extra={"job": name, "count": count})
                except Exception:
                    log.exception("job failed", extra={"job": name})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval_s)

    async def run(self) -> None:
        jobs = [
            ("rollups", 60, self.rollups),
            ("health_probe", self.settings.health_probe_interval_s, self.probe),
        ]
        log.info(
            "worker started",
            extra={"jobs": ["decision_drain", "benchmarks", *(j[0] for j in jobs)]},
        )
        await asyncio.gather(
            self.decision_loop(), self.benchmark_loop(), *(self.run_job(*j) for j in jobs)
        )

    # ── Decision stream ─────────────────────────────────────────────────────

    async def ensure_group(self) -> None:
        try:
            await self.c.redis.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
        except RedisError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def drain(self, *, block_ms: int = 0, count: int = 500) -> int:
        await self.ensure_group()
        resp = await self.c.redis.xreadgroup(
            GROUP, self.consumer, {STREAM_KEY: ">"}, count=count, block=block_ms or None
        )
        batches = cast(list[tuple[bytes, list[Entry]]], resp or [])
        return await self._commit([e for _, batch in batches for e in batch])

    async def reclaim(self, min_idle_ms: int = 60_000) -> int:
        await self.ensure_group()
        result = await self.c.redis.xautoclaim(
            STREAM_KEY, GROUP, self.consumer, min_idle_time=min_idle_ms, start_id="0-0", count=500
        )
        return await self._commit(cast(list[Entry], result[1]) if result else [])

    async def _commit(self, entries: list[Entry]) -> int:
        if not entries:
            return 0
        records = [decode(f[b"r"]) for _, f in entries if f and b"r" in f]
        await self.recorder.write(records)
        ids = [i for i, _ in entries]
        pipe = self.c.redis.pipeline(transaction=False)
        pipe.xack(STREAM_KEY, GROUP, *ids)
        pipe.xdel(STREAM_KEY, *ids)
        await pipe.execute()
        return len(records)

    async def decision_loop(self) -> None:
        last_reclaim = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_reclaim > 30:
                    await self.reclaim()
                    last_reclaim = time.monotonic()
                await self.drain(block_ms=1000)
            except Exception:
                log.exception("decision drain failed; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=1)

    # ── Periodic jobs ───────────────────────────────────────────────────────

    async def rollups(self) -> int:
        open_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        async with self.c.db.sessionmaker() as session:
            first = await session.scalar(select(func.min(RoutingDecision.created_at)))
            if first is None:
                return 0
            last = await session.scalar(text("SELECT max(hour) FROM decision_rollups_hourly"))
            hour = ((last - timedelta(hours=2)) if last else first).replace(
                minute=0, second=0, microsecond=0
            )
            n = 0
            while hour < open_hour:
                await session.execute(ROLLUP_SQL, {"start": hour, "end": hour + timedelta(hours=1)})
                hour += timedelta(hours=1)
                n += 1
            await session.commit()
        return n

    async def probe(self) -> int:
        """Check each endpoint's model listing. A failure counts against every model it
        serves; a success lets a circuit whose cooldown has passed close again."""
        async with self.c.db.sessionmaker() as session:
            catalog = await load_catalog(session, self.c.sealer)
        tracker = HealthTracker(self.c.redis, self.settings.breaker)
        snapshot = await tracker.snapshot(sorted(catalog.models))
        probed = 0
        for ep in catalog.endpoints.values():
            aliases = [a for a, m in catalog.models.items() if m.endpoint_id == ep.id]
            key = catalog.endpoint_keys.get(ep.id)
            base = ep.base_url.rstrip("/")
            headers: dict[str, str] = {}
            if ep.kind == "anthropic":
                url = base + ("/models" if base.endswith("/v1") else "/v1/models")
                headers["anthropic-version"] = "2023-06-01"
                if key:
                    headers["x-api-key"] = key
            else:
                url = base + "/models"
                if key:
                    headers["authorization"] = f"Bearer {key}"
            try:
                resp = await self.c.http.get(url, headers=headers, timeout=5)
                ok = resp.status_code < 500 and resp.status_code not in (401, 403)
            except httpx.HTTPError:
                ok = False
            for alias in aliases:
                state = snapshot.get(alias)
                if not ok:
                    if await tracker.record(alias, "error", -1) == "open":
                        await circuit_opened(self.c.db.sessionmaker, alias, "probe")
                elif state and state.state == "half_open":
                    await tracker.record(alias, "ok", -1)
            probed += 1
        return probed

    async def fail_stale_runs(self) -> int:
        """Runs left "running" by a worker that died mid-run never finish on their own."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.benchmark_stale_after_s)
        async with self.c.db.sessionmaker() as session, session.begin():
            result = await session.execute(
                update(BenchmarkRun)
                .where(BenchmarkRun.status == "running", BenchmarkRun.started_at < cutoff)
                .values(
                    status="failed",
                    error="The worker running this run stopped before it finished. Start it again.",
                    finished_at=datetime.now(UTC),
                )
            )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def benchmark_loop(self) -> None:
        runner = BenchmarkRunner(self.c)
        last_sweep = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_sweep > 60:
                with contextlib.suppress(Exception):
                    await self.fail_stale_runs()
                last_sweep = time.monotonic()
            try:
                item = await self.c.redis.blpop([QUEUE_KEY], timeout=2)
            except RedisError:
                await asyncio.sleep(2)
                continue
            if not item:
                continue
            job = orjson.loads(item[1])
            try:
                await runner.run(uuid.UUID(job["run_id"]), job.get("models"), job.get("policies"))
            except Exception:
                log.exception("benchmark run failed", extra={"run_id": job.get("run_id")})


async def main(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    configure_tracing(settings, service="smartrouter-worker")
    container = Container.build(settings)
    worker = Worker(container)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await container.close()
