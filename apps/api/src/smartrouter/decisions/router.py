from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query

from smartrouter.auth.dependencies import Viewer
from smartrouter.core.deps import ContainerDep, SessionDep
from smartrouter.core.schemas import Page
from smartrouter.decisions import service
from smartrouter.decisions.analytics import Analytics
from smartrouter.decisions.schemas import (
    BreakdownRow,
    DecisionDetail,
    DecisionOut,
    Dimension,
    Overview,
    Range,
    SeriesPoint,
)

router = APIRouter(prefix="/api/v1", tags=["decisions and analytics"])


@router.get("/decisions", response_model=Page[DecisionOut], summary="The routing decision log")
async def list_decisions(
    _: Viewer,
    session: SessionDep,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    project_id: uuid.UUID | None = None,
    policy: str | None = None,
    model: str | None = None,
    task: str | None = None,
    status: str | None = None,
    fallback: bool | None = None,
    source: Literal["api", "benchmark"] = Query(
        "api", description="api: real traffic (default); benchmark: benchmark policy runs"
    ),
    q: str | None = Query(None, max_length=64, description="Request id prefix"),
) -> Page[DecisionOut]:
    return await service.list_decisions(
        session,
        cursor=cursor,
        limit=limit,
        project_id=project_id,
        policy=policy,
        model=model,
        task=task,
        status=status,
        fallback=fallback,
        source=source,
        q=q,
    )


@router.get(
    "/decisions/{request_id}",
    response_model=DecisionDetail,
    summary="One decision: analysis, every candidate with its score and reason, attempts, costs",
)
async def get_decision(request_id: str, _: Viewer, session: SessionDep) -> DecisionDetail:
    return await service.get_decision(session, request_id)


@router.get("/analytics/overview", response_model=Overview, summary="Totals vs the previous period")
async def overview(
    _: Viewer,
    session: SessionDep,
    container: ContainerDep,
    period: Range = Query("24h", alias="range"),
    project_id: uuid.UUID | None = None,
) -> Overview:
    return await Analytics(session, container.redis).overview(period, project_id)


@router.get(
    "/analytics/timeseries", response_model=list[SeriesPoint], summary="Cost vs baseline over time"
)
async def timeseries(
    _: Viewer,
    session: SessionDep,
    container: ContainerDep,
    period: Range = Query("24h", alias="range"),
    project_id: uuid.UUID | None = None,
) -> list[SeriesPoint]:
    return await Analytics(session, container.redis).timeseries(period, project_id)


@router.get(
    "/analytics/breakdown",
    response_model=list[BreakdownRow],
    summary="Traffic and savings by dimension",
)
async def breakdown(
    _: Viewer,
    session: SessionDep,
    container: ContainerDep,
    by: Dimension = "model",
    period: Range = Query("24h", alias="range"),
    project_id: uuid.UUID | None = None,
) -> list[BreakdownRow]:
    return await Analytics(session, container.redis).breakdown(period, by, project_id)
