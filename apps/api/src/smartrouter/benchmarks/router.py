"""Benchmark runs. Runs are queued in Redis and executed by a worker, so a long run never
holds an API process."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from fastapi import APIRouter, Query, Response
from redis.exceptions import RedisError
from sqlalchemy import select, update

from smartrouter.audit.service import audit
from smartrouter.auth.dependencies import Admin, Viewer
from smartrouter.benchmarks.models import BenchmarkResult, BenchmarkRun
from smartrouter.benchmarks.runner import create_run
from smartrouter.benchmarks.schemas import (
    CasesDownload,
    ResultOut,
    RunDetail,
    RunIn,
    RunOut,
    SuiteCase,
)
from smartrouter.benchmarks.suite import load_suite
from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.core.errors import DependencyUnavailableError, NotFoundError

router = APIRouter(prefix="/api/v1/benchmarks", tags=["benchmarks"])
QUEUE_KEY = "sr:bench:queue"


@router.get("/suite", response_model=list[SuiteCase], summary="The benchmark cases")
async def suite(_: Viewer) -> list[SuiteCase]:
    return [
        SuiteCase(id=c.id, task=c.task, difficulty=c.difficulty, grader=c.grader, prompt=c.prompt)
        for c in load_suite().cases
    ]


@router.get("/runs", response_model=list[RunOut], summary="Recent benchmark runs")
async def runs(
    _: Viewer, session: SessionDep, limit: int = Query(20, ge=1, le=100)
) -> list[RunOut]:
    rows = await session.scalars(
        select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc()).limit(limit)
    )
    return [RunOut.model_validate(r) for r in rows.all()]


@router.post("/runs", response_model=RunOut, status_code=202, summary="Queue a benchmark run")
async def start(body: RunIn, actor: Admin, session: SessionDep, container: ContainerDep) -> RunOut:
    run_id = await create_run(container, body.models, body.policies, actor.id)
    try:
        await container.redis.rpush(
            QUEUE_KEY,
            orjson.dumps({"run_id": str(run_id), "models": body.models, "policies": body.policies}),
        )
    except RedisError as exc:
        # Never leave a "queued" row that no worker will ever pick up.
        await session.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == run_id)
            .values(status="failed", error="Could not queue the run: Redis is unavailable")
        )
        await session.commit()
        raise DependencyUnavailableError(
            "The benchmark queue is unavailable. Check Redis, then start the run again."
        ) from exc
    audit(session, actor, "benchmark.queued", "benchmark", run_id)
    await session.commit()
    run = await session.get(BenchmarkRun, run_id)
    assert run is not None
    return RunOut.model_validate(run)


@router.get("/runs/{run_id}", response_model=RunDetail, summary="A run with every result")
async def run_detail(run_id: uuid.UUID, _: Viewer, session: SessionDep) -> RunDetail:
    run = await session.get(BenchmarkRun, run_id)
    if run is None:
        raise NotFoundError("Benchmark run not found")
    results = await session.scalars(
        select(BenchmarkResult)
        .where(BenchmarkResult.run_id == run_id)
        .order_by(
            BenchmarkResult.target_type,
            BenchmarkResult.target,
            BenchmarkResult.task,
            BenchmarkResult.band,
        )
    )
    return RunDetail.model_validate(run).model_copy(
        update={"results": [ResultOut.model_validate(r) for r in results.all()]}
    )


async def _results_file(
    session: SessionDep, run_id: uuid.UUID, retention_days: int
) -> dict[str, Any]:
    run = await session.get(BenchmarkRun, run_id)
    if run is None:
        raise NotFoundError("Benchmark run not found")
    info = (run.summary or {}).get("results_file")
    if not info:
        raise NotFoundError("This run has no per-case results file (it failed or is still running)")
    if run.finished_at and run.finished_at < datetime.now(UTC) - timedelta(days=retention_days):
        raise NotFoundError(
            f"This run's results file expired after {retention_days} days. Run the benchmark again."
        )
    return dict(info)


@router.get(
    "/runs/{run_id}/cases",
    response_model=CasesDownload,
    summary="Download link for every graded answer of a run (gzipped JSON lines)",
)
async def cases(
    run_id: uuid.UUID, _: Viewer, session: SessionDep, container: ContainerDep
) -> CasesDownload:
    info = await _results_file(session, run_id, container.settings.benchmark_results_days)
    filename = f"benchmark-{run_id}.jsonl.gz"
    url = await container.storage.download_url(info["key"], filename)
    return CasesDownload(
        url=url or f"/api/v1/benchmarks/runs/{run_id}/cases/file",
        filename=filename,
        rows=int(info["rows"]),
        bytes=int(info["bytes"]),
    )


@router.get(
    "/runs/{run_id}/cases/file",
    summary="Stream the per-case results file (local storage; S3 serves it directly)",
    response_class=Response,
)
async def cases_file(
    run_id: uuid.UUID, _: Viewer, session: SessionDep, container: ContainerDep
) -> Response:
    info = await _results_file(session, run_id, container.settings.benchmark_results_days)
    data = await container.storage.get(info["key"])
    return Response(
        data,
        media_type="application/gzip",
        headers={"content-disposition": f'attachment; filename="benchmark-{run_id}.jsonl.gz"'},
    )
