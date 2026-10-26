"""Operational paths: the worker's jobs, analytics from rollups, alerts, key limits and the
decision stream's crash recovery."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import orjson
import pytest

from smartrouter.benchmarks.router import QUEUE_KEY
from smartrouter.decisions.recorder import GROUP, STREAM_KEY
from smartrouter.worker import Worker
from tests.integration.conftest import Api, World, chat

pytestmark = pytest.mark.integration


def _worker(api: Api) -> Worker:
    return Worker(api.app.state.container)


async def test_rollups_feed_timeseries_and_breakdowns(api: Api, world: World) -> None:
    for prompt in ("Classify as spam or ham: WIN NOW", "Solve 3x + 7 = 22", "Hello there"):
        assert (await api.gateway(world.key, chat(prompt, "auto:cost"))).status_code == 200
    await api.drain()
    # Rollups cover closed hours; push the decisions an hour back so they are included.
    engine = api.app.state.container.db.engine
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE routing_decisions DISABLE TRIGGER USER")
        await conn.exec_driver_sql(
            "UPDATE routing_decisions SET created_at = created_at - interval '1 hour'"
        )
        await conn.exec_driver_sql("ALTER TABLE routing_decisions ENABLE TRIGGER USER")
    assert await _worker(api).rollups() >= 1
    series = await api.ok("GET", "/api/v1/analytics/timeseries?range=24h")
    assert sum(p["requests"] for p in series) == 3
    by_task = await api.ok("GET", "/api/v1/analytics/breakdown?by=task&range=24h")
    assert {r["key"] for r in by_task} == {"classification", "math", "chat"}
    for dim in ("model", "policy", "project", "status"):
        rows = await api.ok("GET", f"/api/v1/analytics/breakdown?by={dim}&range=7d")
        assert sum(r["requests"] for r in rows) == 3, dim
    overview = await api.ok(
        "GET", f"/api/v1/analytics/overview?range=7d&project_id={world.project_id}"
    )
    assert overview["totals"]["requests"] == 3


async def test_decision_log_filters_and_pagination(api: Api, world: World) -> None:
    for _ in range(3):
        await api.gateway(world.key, chat("Hello there", "auto:latency"))
    await api.fault("mock-fast", "error", count=1)
    await api.gateway(world.key, chat("Hello there", "auto:latency"))
    await api.drain()
    assert len((await api.ok("GET", "/api/v1/decisions?fallback=true"))["items"]) == 1
    assert len((await api.ok("GET", "/api/v1/decisions?policy=latency"))["items"]) == 4
    assert len((await api.ok("GET", "/api/v1/decisions?model=gpt-fast"))["items"]) == 3
    page = await api.ok("GET", "/api/v1/decisions?limit=2")
    nxt = await api.ok("GET", f"/api/v1/decisions?limit=2&cursor={page['next_cursor']}")
    assert len(page["items"]) == 2 and len(nxt["items"]) == 2
    assert {d["request_id"] for d in page["items"]}.isdisjoint(
        d["request_id"] for d in nxt["items"]
    )
    assert (await api.client.get("/api/v1/decisions/req_missing")).status_code == 404


async def test_circuit_open_raises_one_alert_that_can_be_acknowledged(
    api: Api, world: World
) -> None:
    await api.fault("mock-fast", "error", count=6)
    for _ in range(6):
        await api.gateway(world.key, chat("Hello there", "auto:latency"))
    await asyncio.sleep(0.2)  # the alert is written off the request path
    alerts = await api.ok("GET", "/api/v1/alerts")
    assert alerts["unacknowledged"] == 1
    assert "gpt-fast" in alerts["items"][0]["message"]
    await api.ok("POST", f"/api/v1/alerts/{alerts['items'][0]['id']}/ack")
    assert (await api.ok("GET", "/api/v1/alerts"))["unacknowledged"] == 0
    everything = await api.ok("GET", "/api/v1/alerts?include_acknowledged=true")
    assert len(everything["items"]) == 1
    await api.ok("POST", "/api/v1/alerts/ack-all")


async def test_probe_counts_an_unreachable_endpoint_against_its_models(
    api: Api, world: World
) -> None:
    await api.ok(
        "PATCH",
        f"/api/v1/providers/{world.endpoints['local']}",
        {"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-test-only-123456"},
    )
    worker = _worker(api)
    for _ in range(3):
        assert await worker.probe() == 4
    states = {h["model"]: h["state"] for h in await api.ok("GET", "/api/v1/health/models")}
    assert states["llama-local"] == "open" and states["gpt-fast"] == "closed"


async def test_probe_closes_a_half_open_circuit(api: Api, world: World) -> None:
    await api.fault("mock-fast", "error", count=3)
    for _ in range(3):
        await api.gateway(world.key, chat("Hello there", "auto:latency"))
    await asyncio.sleep(1.2)
    await _worker(api).probe()
    states = {h["model"]: h["state"] for h in await api.ok("GET", "/api/v1/health/models")}
    assert states["gpt-fast"] == "closed"


async def test_a_crashed_consumer_is_reclaimed_without_duplicates(api: Api, world: World) -> None:
    r = await api.gateway(world.key, chat("Hello there"))
    redis = api.app.state.container.redis
    dead, alive = _worker(api), _worker(api)
    await dead.ensure_group()
    # The dead consumer reads the entry and never acks it.
    await redis.xreadgroup(GROUP, "dead-consumer", {STREAM_KEY: ">"}, count=10)
    assert await alive.drain() == 0
    assert await alive.reclaim(min_idle_ms=0) == 1
    assert await alive.reclaim(min_idle_ms=0) == 0
    page = await api.ok("GET", "/api/v1/decisions")
    assert [d["request_id"] for d in page["items"]] == [r.headers["x-smartrouter-request-id"]]


async def test_decisions_are_written_directly_when_the_stream_is_unavailable(
    api: Api, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import RedisError

    redis = api.app.state.container.redis

    async def broken(*_a: object, **_k: object) -> None:
        raise RedisError("stream down")

    monkeypatch.setattr(redis, "xadd", broken)
    r = await api.gateway(world.key, chat("Hello there"))
    assert r.status_code == 200
    monkeypatch.undo()
    page = await api.ok("GET", "/api/v1/decisions")
    assert page["items"][0]["request_id"] == r.headers["x-smartrouter-request-id"]


async def test_worker_runs_queued_benchmarks(api: Api, world: World) -> None:
    queued = await api.ok(
        "POST", "/api/v1/benchmarks/runs", {"models": ["gpt-fast"], "policies": []}, status=202
    )
    worker = _worker(api)
    task = asyncio.create_task(worker.benchmark_loop())
    try:
        for _ in range(100):
            run = await api.ok("GET", f"/api/v1/benchmarks/runs/{queued['id']}")
            if run["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
    finally:
        worker.stop()
        await task
    assert run["status"] == "done", run["error"]
    assert list(run["summary"]["models"]) == ["gpt-fast"]
    assert await api.app.state.container.redis.llen(QUEUE_KEY) == 0
    runs = await api.ok("GET", "/api/v1/benchmarks/runs")
    assert runs[0]["id"] == queued["id"]
    suite = await api.ok("GET", "/api/v1/benchmarks/suite")
    assert len(suite) >= 160


async def test_a_failing_benchmark_is_marked_failed(api: Api, world: World) -> None:
    queued = await api.ok(
        "POST", "/api/v1/benchmarks/runs", {"models": ["nope"], "policies": []}, status=202
    )
    container = api.app.state.container
    job = orjson.loads(await container.redis.lpop(QUEUE_KEY))
    from smartrouter.benchmarks.runner import BenchmarkRunner

    run_id = uuid.UUID(job["run_id"])
    with pytest.raises(ValueError, match="unknown models"):
        await BenchmarkRunner(container).run(run_id, ["nope"], [])
    run = await api.ok("GET", f"/api/v1/benchmarks/runs/{queued['id']}")
    assert run["status"] == "failed" and "unknown models" in run["error"]


async def test_key_rpm_limit(api: Api, world: World) -> None:
    await api.ok("PATCH", f"/api/v1/keys/{world.key_id}", {"rpm_limit": 2})
    codes = [(await api.gateway(world.key, chat("Hello there"))).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = await api.gateway(world.key, chat("Hello there"))
    assert r.json()["error"]["code"] == "rpm_limit" and r.headers["retry-after"]


async def test_revoked_and_expired_keys_are_refused(api: Api, world: World) -> None:
    expired = await api.ok(
        "POST",
        "/api/v1/keys",
        {
            "project_id": world.project_id,
            "name": "old",
            "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        status=201,
    )
    r = await api.gateway(expired["key"], chat())
    assert r.status_code == 401 and r.json()["error"]["code"] == "api_key_expired"
    await api.ok("POST", f"/api/v1/keys/{world.key_id}/revoke")
    r = await api.gateway(world.key, chat())
    assert r.status_code == 401 and r.json()["error"]["code"] == "api_key_revoked"
    bad = await api.gateway("tsr_nope_nope", chat())
    assert bad.status_code == 401
    missing = await api.client.post("/v1/chat/completions", json=chat())
    assert missing.status_code == 401
