"""The decision log: listing, detail with feedback."""

from __future__ import annotations

import uuid

from sqlalchemy import Subquery, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.core.errors import NotFoundError
from smartrouter.core.schemas import Page, decode_cursor, encode_cursor
from smartrouter.decisions.models import DecisionFeedback, RoutingDecision
from smartrouter.decisions.schemas import DecisionDetail, DecisionOut
from smartrouter.projects.models import Project


def _feedback_subq() -> Subquery:
    return (
        select(DecisionFeedback.request_id, func.sum(DecisionFeedback.score).label("score"))
        .group_by(DecisionFeedback.request_id)
        .subquery()
    )


async def list_decisions(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
    project_id: uuid.UUID | None = None,
    policy: str | None = None,
    model: str | None = None,
    task: str | None = None,
    status: str | None = None,
    fallback: bool | None = None,
    source: str = "api",
    q: str | None = None,
) -> Page[DecisionOut]:
    fb = _feedback_subq()
    stmt = (
        select(RoutingDecision, Project.name, fb.c.score)
        .outerjoin(Project, Project.id == RoutingDecision.project_id)
        .outerjoin(fb, fb.c.request_id == RoutingDecision.request_id)
        .order_by(RoutingDecision.id.desc())
        .limit(limit + 1)
    )
    if (after := decode_cursor(cursor)) and after.isdigit():
        stmt = stmt.where(RoutingDecision.id < int(after))
    for column, value in (
        (RoutingDecision.project_id, project_id),
        (RoutingDecision.policy_name, policy),
        (RoutingDecision.final_model, model),
        (RoutingDecision.task, task),
        (RoutingDecision.status, status),
        (RoutingDecision.source, source),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if fallback is True:
        stmt = stmt.where(RoutingDecision.attempts > 1)
    if q:
        stmt = stmt.where(RoutingDecision.request_id.startswith(q.strip()))
    rows = (await session.execute(stmt)).all()
    items = [
        DecisionOut.model_validate(d).model_copy(
            update={"project_name": p, "feedback": (1 if s > 0 else -1) if s else None}
        )
        for d, p, s in rows[:limit]
    ]
    return Page[DecisionOut](
        items=items, next_cursor=encode_cursor(rows[limit - 1][0].id) if len(rows) > limit else None
    )


async def get_decision(session: AsyncSession, request_id: str) -> DecisionDetail:
    fb = _feedback_subq()
    row = (
        await session.execute(
            select(RoutingDecision, Project.name, fb.c.score)
            .outerjoin(Project, Project.id == RoutingDecision.project_id)
            .outerjoin(fb, fb.c.request_id == RoutingDecision.request_id)
            .where(RoutingDecision.request_id == request_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Decision not found")
    d, project, score = row
    return DecisionDetail.model_validate(d).model_copy(
        update={"project_name": project, "feedback": (1 if score > 0 else -1) if score else None}
    )
