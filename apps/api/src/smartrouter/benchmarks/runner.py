"""Runs the benchmark suite: first every model directly, then every policy through the real
routing pipeline.

Phase 1 (models): each model answers every case; the pass rate per task and difficulty band
becomes the router's quality table (``quality_scores``).
Phase 2 (policies): each policy routes every case through ``GatewayService`` exactly as
production traffic would; we record which model it picked, whether the answer passed, and what
it cost. Routing is cross-fitted: a case is routed with quality scores, and with a measure of
how often the analyzer misjudges difficulty, taken only from the other half of the suite, so
the router never ranks models by the very cases it is graded on.

The summary compares each policy with the baseline (the most expensive model, what a team
uses without a router): cost saved and quality change, the two numbers that matter.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from smartrouter.benchmarks.models import BenchmarkResult, BenchmarkRun
from smartrouter.benchmarks.suite import Case, Suite, grade, load_suite
from smartrouter.core.container import Container
from smartrouter.core.errors import AppError
from smartrouter.core.money import nusd_to_usd
from smartrouter.gateway.catalog import Catalog, load_catalog
from smartrouter.gateway.service import GatewayService, RequestContext, new_request_id
from smartrouter.pricing.engine import cost
from smartrouter.providers.adapters.base import (
    UpstreamHTTPError,
    UpstreamNotSentError,
    UpstreamUncertainError,
)
from smartrouter.routing.analyzer import analyze
from smartrouter.routing.models import QualityScore

log = logging.getLogger("smartrouter.benchmarks")
CONCURRENCY = 16
ANSWER_EXCERPT = 400
FOLDS = 2


@dataclass(slots=True)
class Outcome:
    case: Case
    passed: bool
    cost_nusd: int
    latency_ms: float
    model: str
    answer: str = ""


@dataclass(slots=True)
class Agg:
    cases: int = 0
    passed: int = 0
    cost: int = 0
    latency: float = 0.0
    mix: Counter[str] = field(default_factory=Counter)

    def add(self, o: Outcome) -> None:
        self.cases += 1
        self.passed += int(o.passed)
        self.cost += o.cost_nusd
        self.latency += o.latency_ms
        self.mix[o.model] += 1

    @property
    def score(self) -> float:
        return self.passed / self.cases if self.cases else 0.0


def _answer(body: dict[str, Any]) -> str:
    return str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


class BenchmarkRunner:
    def __init__(self, container: Container, suite: Suite | None = None) -> None:
        self.c = container
        self.suite = suite or load_suite()

    async def _catalog(self) -> Catalog:
        async with self.c.db.sessionmaker() as session:
            return await load_catalog(session, self.c.sealer)

    async def _model_case(
        self, catalog: Catalog, alias: str, case: Case, sem: asyncio.Semaphore
    ) -> Outcome:
        m = catalog.models[alias]
        ep = catalog.endpoints[m.endpoint_id]
        body: dict[str, Any] = {
            "model": alias,
            "messages": case.messages(),
            "temperature": 0,
            "max_tokens": 600,
        }
        if ep.simulated:
            body["metadata"] = case.oracle()
        svc = GatewayService(self.c, catalog)
        target, kind = svc._target(m)
        async with sem:
            t0 = time.perf_counter()
            try:
                result = await self.c.adapters.get(kind).chat(target, body)
            except (UpstreamHTTPError, UpstreamNotSentError, UpstreamUncertainError) as exc:
                log.warning(
                    "benchmark call failed",
                    extra={"model": alias, "case": case.id, "error": str(exc)},
                )
                return Outcome(case, False, 0, (time.perf_counter() - t0) * 1000, alias)
        ms = (time.perf_counter() - t0) * 1000
        spent = cost(m.view.price, result.usage.input_tokens, result.usage.output_tokens)
        text = _answer(result.body)
        return Outcome(case, grade(case, text), spent, ms, alias, text[:ANSWER_EXCERPT])

    async def _policy_case(
        self, catalog: Catalog, policy: str, case: Case, sem: asyncio.Semaphore
    ) -> Outcome:
        svc = GatewayService(self.c, catalog, live_health=False)
        body: dict[str, Any] = {
            "model": f"auto:{policy}",
            "messages": case.messages(),
            "temperature": 0,
            "max_tokens": 600,
            "metadata": case.oracle(),  # stripped by the gateway for non-simulated endpoints
        }
        ctx = RequestContext(
            request_id=new_request_id(), project=None, key=None, source="benchmark"
        )
        async with sem:
            t0 = time.perf_counter()
            try:
                resp = await svc.chat(ctx, body)
            except AppError:
                return Outcome(case, False, 0, (time.perf_counter() - t0) * 1000, "failed")
        ms = (time.perf_counter() - t0) * 1000
        data = orjson.loads(bytes(resp.body))
        chosen = resp.headers.get("x-smartrouter-model", "")
        m = catalog.models.get(chosen)
        usage = data.get("usage") or {}
        spent = (
            cost(
                m.view.price,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
            if m
            else 0
        )
        text = _answer(data)
        return Outcome(case, grade(case, text), spent, ms, chosen, text[:ANSWER_EXCERPT])

    async def run(
        self, run_id: uuid.UUID, models: list[str] | None, policies: list[str] | None
    ) -> dict[str, Any]:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with self.c.db.sessionmaker() as session, session.begin():
            await session.execute(
                update(BenchmarkRun)
                .where(BenchmarkRun.id == run_id)
                .values(status="running", started_at=datetime.now(UTC))
            )
        try:
            catalog = await self._catalog()
            model_list = models or sorted(catalog.models)
            unknown = [m for m in model_list if m not in catalog.models]
            if unknown:
                raise ValueError(f"unknown models: {', '.join(unknown)}")

            # Phase 1: models.
            outcomes = await asyncio.gather(
                *(
                    self._model_case(catalog, a, c, sem)
                    for a in model_list
                    for c in self.suite.cases
                )
            )
            per_model: dict[str, dict[tuple[str, str], Agg]] = defaultdict(lambda: defaultdict(Agg))
            for o in outcomes:
                per_model[o.model][(o.case.task, o.case.band)].add(o)
            await self._store(run_id, "model", per_model)
            await self._update_quality(run_id, per_model)
            await self.c.config_cache.invalidate()

            # Phase 2: policies, cross-fitted (see the module docstring).
            catalog = await self._catalog()
            fold_of = {c.id: i % FOLDS for i, c in enumerate(self.suite.cases)}
            fold_catalogs = {
                f: replace(
                    catalog,
                    quality=self._quality_without(outcomes, fold_of, f),
                    calibration=self._calibration(
                        [c for c in self.suite.cases if fold_of[c.id] != f]
                    ),
                )
                for f in range(FOLDS)
            }
            policy_list = policies if policies is not None else sorted(catalog.policies)
            per_policy: dict[str, dict[tuple[str, str], Agg]] = defaultdict(
                lambda: defaultdict(Agg)
            )
            p_outcomes: list[Outcome] = []
            if policy_list:
                p_outcomes = await asyncio.gather(
                    *(
                        self._policy_case(fold_catalogs[fold_of[c.id]], p, c, sem)
                        for p in policy_list
                        for c in self.suite.cases
                    )
                )
                for p, o in zip(
                    [p for p in policy_list for _ in self.suite.cases], p_outcomes, strict=True
                ):
                    per_policy[p][(o.case.task, o.case.band)].add(o)
                await self._store(run_id, "policy", per_policy)

            summary = self._summary(catalog, per_model, per_policy)
            summary["calibration"] = {
                f"{task}|{band}": [hard, n]
                for (task, band), (hard, n) in sorted(self._calibration(self.suite.cases).items())
            }
            policy_outcomes = list(
                zip([p for p in policy_list for _ in self.suite.cases], p_outcomes, strict=True)
            )
            summary["results_file"] = await self._write_cases(run_id, outcomes, policy_outcomes)
            async with self.c.db.sessionmaker() as session, session.begin():
                await session.execute(
                    update(BenchmarkRun)
                    .where(BenchmarkRun.id == run_id)
                    .values(
                        status="done",
                        summary=summary,
                        models=model_list,
                        policies=policy_list,
                        finished_at=datetime.now(UTC),
                    )
                )
            return summary
        except Exception as exc:
            log.exception("benchmark run failed")
            async with self.c.db.sessionmaker() as session, session.begin():
                await session.execute(
                    update(BenchmarkRun)
                    .where(BenchmarkRun.id == run_id)
                    .values(status="failed", error=str(exc)[:1000], finished_at=datetime.now(UTC))
                )
            raise

    async def _write_cases(
        self,
        run_id: uuid.UUID,
        model_outcomes: list[Outcome],
        policy_outcomes: list[tuple[str, Outcome]],
    ) -> dict[str, Any] | None:
        """Every graded answer as gzipped JSON lines in object storage (S3 in AWS). Rows keep
        only the key: the file is large and read rarely. Failing to write it never fails the
        run, the summary simply has no results file."""
        rows = [("model", o.model, o) for o in model_outcomes] + [
            ("policy", p, o) for p, o in policy_outcomes
        ]
        lines = [
            orjson.dumps(
                {
                    "target_type": kind,
                    "target": target,
                    "case": o.case.id,
                    "task": o.case.task,
                    "difficulty": o.case.difficulty,
                    "model": o.model,
                    "passed": o.passed,
                    "cost_usd": nusd_to_usd(o.cost_nusd),
                    "latency_ms": round(o.latency_ms, 1),
                    "answer": o.answer,
                }
            )
            for kind, target, o in rows
        ]
        data = gzip.compress(b"\n".join(lines) + b"\n")
        key = f"benchmarks/{run_id}/cases.jsonl.gz"
        try:
            await self.c.storage.put(key, data, "application/gzip")
        except Exception:
            log.exception("could not store benchmark cases", extra={"run_id": str(run_id)})
            return None
        return {"key": key, "rows": len(lines), "bytes": len(data)}

    @staticmethod
    def _calibration(
        cases: list[Case] | tuple[Case, ...],
    ) -> dict[tuple[str, str], tuple[int, int]]:
        """How often the analyzer's difficulty band is right: for each (predicted task,
        predicted band), the number of cases that were truly hard and the number of cases."""
        counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        for c in cases:
            a = analyze({"messages": c.messages()})
            cell = counts[(a.task, a.band)]
            cell[0] += int(c.band == "high")
            cell[1] += 1
        return {k: (hard, n) for k, (hard, n) in counts.items()}

    @staticmethod
    def _quality_without(
        outcomes: list[Outcome], fold_of: dict[str, int], fold: int
    ) -> dict[tuple[str, str, str], tuple[float, int]]:
        """Quality cells measured on every case outside ``fold``."""
        cells: dict[tuple[str, str, str], Agg] = defaultdict(Agg)
        for o in outcomes:
            if fold_of[o.case.id] != fold:
                cells[(o.model, o.case.task, o.case.band)].add(o)
        return {k: (a.score, a.cases) for k, a in cells.items()}

    async def _store(
        self, run_id: uuid.UUID, kind: str, per: dict[str, dict[tuple[str, str], Agg]]
    ) -> None:
        rows = [
            {
                "run_id": run_id,
                "target_type": kind,
                "target": target,
                "task": task,
                "band": band,
                "cases": a.cases,
                "passed": a.passed,
                "score": round(a.score, 4),
                "cost_nusd": a.cost,
                "latency_ms_avg": round(a.latency / a.cases, 1) if a.cases else 0.0,
                "model_mix": dict(a.mix),
            }
            for target, cells in per.items()
            for (task, band), a in cells.items()
        ]
        if rows:
            async with self.c.db.sessionmaker() as session, session.begin():
                await session.execute(insert(BenchmarkResult), rows)

    async def _update_quality(
        self, run_id: uuid.UUID, per_model: dict[str, dict[tuple[str, str], Agg]]
    ) -> None:
        now = datetime.now(UTC)
        async with self.c.db.sessionmaker() as session, session.begin():
            for alias, cells in per_model.items():
                for (task, band), a in cells.items():
                    stmt = insert(QualityScore).values(
                        model_alias=alias,
                        task=task,
                        band=band,
                        score=round(a.score, 4),
                        samples=a.cases,
                        source="benchmark",
                        run_id=run_id,
                        updated_at=now,
                    )
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=["model_alias", "task", "band"],
                            set_={
                                "score": stmt.excluded.score,
                                "samples": stmt.excluded.samples,
                                "source": "benchmark",
                                "run_id": run_id,
                                "updated_at": now,
                            },
                        )
                    )

    @staticmethod
    def _summary(
        catalog: Catalog,
        per_model: dict[str, dict[tuple[str, str], Agg]],
        per_policy: dict[str, dict[tuple[str, str], Agg]],
    ) -> dict[str, Any]:
        def roll(cells: dict[tuple[str, str], Agg]) -> Agg:
            total = Agg()
            for a in cells.values():
                total.cases += a.cases
                total.passed += a.passed
                total.cost += a.cost
                total.latency += a.latency
                total.mix.update(a.mix)
            return total

        models = {k: roll(v) for k, v in per_model.items()}
        # Baseline: the most expensive model per token, what teams default to without a router.
        baseline = (
            max(
                models,
                key=lambda a: (
                    catalog.models[a].view.price.input_per_m
                    + catalog.models[a].view.price.output_per_m,
                    a,
                ),
            )
            if models
            else None
        )
        base = models.get(baseline) if baseline else None
        out: dict[str, Any] = {
            "baseline": baseline,
            "cases": next((a.cases for a in models.values()), 0),
            "models": {
                k: {
                    "quality": round(a.score, 4),
                    "cost_usd": nusd_to_usd(a.cost),
                    "latency_ms": round(a.latency / a.cases, 1) if a.cases else 0.0,
                }
                for k, a in sorted(models.items())
            },
            "policies": {},
        }
        for name, cells in sorted(per_policy.items()):
            a = roll(cells)
            entry: dict[str, Any] = {
                "quality": round(a.score, 4),
                "cost_usd": nusd_to_usd(a.cost),
                "latency_ms": round(a.latency / a.cases, 1) if a.cases else 0.0,
                "model_mix": dict(a.mix.most_common()),
            }
            if base and base.cost:
                entry["savings_pct"] = round((1 - a.cost / base.cost) * 100, 1)
                entry["quality_delta"] = round(a.score - base.score, 4)
            out["policies"][name] = entry
        return out


async def create_run(
    container: Container,
    models: list[str] | None,
    policies: list[str] | None,
    user_id: uuid.UUID | None,
) -> uuid.UUID:
    run = BenchmarkRun(
        suite_version=load_suite().version,
        models=models or [],
        policies=policies or [],
        created_by=user_id,
    )
    async with container.db.sessionmaker() as session, session.begin():
        session.add(run)
    return run.id
