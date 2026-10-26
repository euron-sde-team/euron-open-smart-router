"""Health, readiness, metrics and deployment info."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from smartrouter import __version__
from smartrouter.auth.dependencies import Viewer
from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.core.schemas import ApiModel
from smartrouter.keys.models import ApiKey
from smartrouter.projects.models import Project
from smartrouter.providers.models import Model
from smartrouter.routing.models import RoutingPolicy

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _probe(name: str, coro: Any) -> tuple[str, dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(coro, timeout=2)
        return name, {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:
        return name, {"ok": False, "error": type(exc).__name__}


@router.get("/readyz", summary="Readiness: Postgres and Redis reachable")
async def readyz(container: ContainerDep, response: Response) -> dict[str, Any]:
    async def pg() -> None:
        async with container.db.engine.connect() as conn:
            await conn.execute(text("select 1"))

    checks = dict(
        await asyncio.gather(_probe("postgres", pg()), _probe("redis", container.redis.ping()))
    )
    ok = all(c["ok"] for c in checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "degraded", "checks": checks}


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class SystemInfo(ApiModel):
    version: str
    environment: str
    cloud: str
    key_mode: str
    storage: str
    breaker_failure_threshold: int
    breaker_cooldown_s: int
    default_policy: str | None = None
    policies: int = 0
    projects: int
    models: int
    active_keys: int
    checks: dict[str, Any]


@router.get("/api/v1/system", response_model=SystemInfo, summary="Deployment configuration")
async def system(_: Viewer, session: SessionDep, container: ContainerDep) -> SystemInfo:
    s = container.settings
    projects = await session.scalar(select(func.count()).where(Project.archived.is_(False)))
    models = await session.scalar(select(func.count()).select_from(Model))
    keys = await session.scalar(select(func.count()).where(ApiKey.revoked_at.is_(None)))
    policies = await session.scalar(select(func.count()).select_from(RoutingPolicy))
    default_policy = await session.scalar(
        select(RoutingPolicy.name).where(RoutingPolicy.is_default.is_(True))
    )
    checks = dict([await _probe("redis", container.redis.ping())])
    checks["postgres"] = {"ok": True}
    return SystemInfo(
        version=__version__,
        environment=s.environment,
        cloud=s.cloud,
        key_mode=s.key_mode,
        storage=s.storage,
        breaker_failure_threshold=s.breaker_failure_threshold,
        breaker_cooldown_s=s.breaker_cooldown_s,
        default_policy=default_policy,
        policies=int(policies or 0),
        projects=int(projects or 0),
        models=int(models or 0),
        active_keys=int(keys or 0),
        checks=checks,
    )
