from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from smartrouter.audit.service import audit
from smartrouter.auth.dependencies import Admin, Viewer
from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.core.errors import NotFoundError
from smartrouter.core.schemas import ApiModel, Message
from smartrouter.gateway.catalog import Catalog
from smartrouter.gateway.dependencies import get_catalog
from smartrouter.health.tracker import HealthTracker

router = APIRouter(prefix="/api/v1/health", tags=["health"])
CatalogDep = Annotated[Catalog, Depends(get_catalog)]


class ModelHealth(ApiModel):
    model: str
    endpoint: str
    tier: str
    state: str = Field(description="closed (healthy), open (skipped) or half_open (probing)")
    p50_ms: float | None
    p95_ms: float | None
    error_rate: float
    samples: int
    counts: dict[str, int]
    latency_prior_ms: int


@router.get("/models", response_model=list[ModelHealth], summary="Live health of every model")
async def models(_: Viewer, container: ContainerDep, catalog: CatalogDep) -> list[ModelHealth]:
    tracker = HealthTracker(container.redis, container.settings.breaker)
    aliases = sorted(catalog.models)
    snap = await tracker.snapshot(aliases)
    out = []
    for alias in aliases:
        view = catalog.models[alias].view
        h = snap.get(alias)
        out.append(
            ModelHealth(
                model=alias,
                endpoint=view.endpoint,
                tier=view.tier,
                state=h.state if h else "closed",
                p50_ms=h.p50_ms if h else None,
                p95_ms=h.p95_ms if h else None,
                error_rate=round(h.error_rate, 4) if h else 0.0,
                samples=h.samples if h else 0,
                counts=await tracker.window_counts(alias),
                latency_prior_ms=view.latency_prior_ms,
            )
        )
    return out


@router.post(
    "/models/{alias}/reset", response_model=Message, summary="Close a model's circuit by hand"
)
async def reset(
    alias: str, actor: Admin, container: ContainerDep, catalog: CatalogDep, session: SessionDep
) -> Message:
    if alias not in catalog.models:
        raise NotFoundError("Model not found")
    await HealthTracker(container.redis, container.settings.breaker).reset(alias)
    audit(session, actor, "health.reset", "model", alias)
    await session.commit()
    return Message(message=f"Circuit for {alias} closed")
