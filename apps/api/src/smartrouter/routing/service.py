"""Routing policies, the quality table, and the route simulator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.audit.service import Actor, audit
from smartrouter.core.errors import AppError, ConflictError, NotFoundError
from smartrouter.core.money import nusd_to_usd
from smartrouter.decisions.models import RoutingDecision
from smartrouter.gateway.catalog import policy_view
from smartrouter.gateway.service import GatewayService, RequestContext
from smartrouter.providers.models import Model
from smartrouter.routing.models import QualityScore, RoutingPolicy
from smartrouter.routing.schemas import (
    AnalysisOut,
    PolicyIn,
    PolicyOut,
    PolicyUpdate,
    QualityCell,
    QualityOverride,
    SimulateIn,
    SimulateOut,
)


def policy_out(p: RoutingPolicy, requests_24h: int = 0) -> PolicyOut:
    return PolicyOut.model_validate(p).model_copy(
        update={
            "effective_weights": policy_view(p).resolved_weights(),
            "requests_24h": requests_24h,
        }
    )


async def list_policies(session: AsyncSession) -> list[PolicyOut]:
    since = datetime.now(UTC) - timedelta(hours=24)
    counts = dict(
        (
            await session.execute(
                select(RoutingDecision.policy_name, func.count())
                .where(RoutingDecision.created_at >= since)
                .group_by(RoutingDecision.policy_name)
            )
        ).all()
    )
    rows = (
        await session.scalars(
            select(RoutingPolicy).order_by(RoutingPolicy.is_default.desc(), RoutingPolicy.name)
        )
    ).all()
    return [policy_out(p, int(counts.get(p.name, 0))) for p in rows]


async def _validate_models(session: AsyncSession, names: list[str | None]) -> None:
    wanted = {n for n in names if n}
    if not wanted:
        return
    known = set((await session.scalars(select(Model.alias).where(Model.alias.in_(wanted)))).all())
    if missing := sorted(wanted - known):
        raise AppError(f"Unknown model aliases: {', '.join(missing)}", code="unknown_models")


async def _clear_default(session: AsyncSession, keep: uuid.UUID | None) -> None:
    # Serialise concurrent "make default" requests for the rest of this transaction: the
    # unique index keeps the invariant; the lock makes the losers wait instead of failing.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('routing_policies.default'))")
    )
    stmt = update(RoutingPolicy).where(RoutingPolicy.is_default.is_(True))
    if keep:
        stmt = stmt.where(RoutingPolicy.id != keep)
    await session.execute(stmt.values(is_default=False))


async def create_policy(session: AsyncSession, data: PolicyIn, actor: Actor) -> PolicyOut:
    if await session.scalar(select(RoutingPolicy.id).where(RoutingPolicy.name == data.name)):
        raise ConflictError("A policy with this name exists", code="name_taken")
    await _validate_models(
        session, [*(data.allowed_models or []), *data.excluded_models, data.baseline_model]
    )
    if data.is_default:
        # Clear first: a partial unique index allows only one default at any moment.
        await _clear_default(session, None)
    policy = RoutingPolicy(**data.model_dump())
    session.add(policy)
    await session.flush()
    audit(
        session,
        actor,
        "policy.created",
        "policy",
        policy.id,
        name=policy.name,
        strategy=policy.strategy,
    )
    await session.commit()
    await session.refresh(policy)
    return policy_out(policy)


async def update_policy(
    session: AsyncSession, policy_id: uuid.UUID, data: PolicyUpdate, actor: Actor
) -> PolicyOut:
    policy = await session.get(RoutingPolicy, policy_id)
    if policy is None:
        raise NotFoundError("Policy not found")
    changes = data.model_dump(exclude_unset=True)
    await _validate_models(
        session,
        [
            *(changes.get("allowed_models") or []),
            *(changes.get("excluded_models") or []),
            changes.get("baseline_model"),
        ],
    )
    if changes.get("is_default"):
        # Clear first: a partial unique index allows only one default at any moment.
        await _clear_default(session, policy.id)
    for field, value in changes.items():
        setattr(policy, field, value)
    audit(
        session,
        actor,
        "policy.updated",
        "policy",
        policy.id,
        **{k: str(v) for k, v in changes.items()},
    )
    await session.commit()
    await session.refresh(policy)
    return policy_out(policy)


async def delete_policy(session: AsyncSession, policy_id: uuid.UUID, actor: Actor) -> None:
    policy = await session.get(RoutingPolicy, policy_id)
    if policy is None:
        raise NotFoundError("Policy not found")
    if policy.is_default:
        raise ConflictError(
            "Make another policy the default before deleting this one", code="default_policy"
        )
    audit(session, actor, "policy.deleted", "policy", policy.id, name=policy.name)
    await session.delete(policy)
    await session.commit()


async def quality_table(session: AsyncSession) -> list[QualityCell]:
    rows = await session.scalars(
        select(QualityScore).order_by(
            QualityScore.model_alias, QualityScore.task, QualityScore.band
        )
    )
    return [QualityCell.model_validate(q) for q in rows.all()]


async def set_quality(session: AsyncSession, data: QualityOverride, actor: Actor) -> QualityCell:
    """A manual score, for models a benchmark cannot reach. The next benchmark run replaces it."""
    await _validate_models(session, [data.model_alias])
    now = datetime.now(UTC)
    stmt = insert(QualityScore).values(
        model_alias=data.model_alias,
        task=data.task,
        band=data.band,
        score=data.score,
        samples=0,
        source="manual",
        updated_at=now,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["model_alias", "task", "band"],
            set_={"score": data.score, "source": "manual", "samples": 0, "updated_at": now},
        )
    )
    audit(
        session,
        actor,
        "quality.set",
        "model",
        data.model_alias,
        task=data.task,
        band=data.band,
        score=data.score,
    )
    await session.commit()
    row = await session.scalar(
        select(QualityScore).where(
            QualityScore.model_alias == data.model_alias,
            QualityScore.task == data.task,
            QualityScore.band == data.band,
        )
    )
    assert row is not None
    return QualityCell.model_validate(row)


async def simulate(service: GatewayService, data: SimulateIn) -> SimulateOut:
    body: dict[str, object] = {"model": data.model, "messages": data.messages}
    for k in ("max_tokens", "tools", "response_format"):
        if getattr(data, k) is not None:
            body[k] = getattr(data, k)
    project = service.catalog.projects.get(data.project_id) if data.project_id else None
    ctx = RequestContext(
        request_id="sim",
        project=project,
        key=None,
        source="simulator",
        task_override=data.task,
        complexity_override=data.complexity,
    )
    plan = await service.plan(ctx, body)
    a = plan.analysis
    selected = plan.ranking.selected
    est = selected.cost_nusd if selected else 0
    base_cost = 0
    if plan.baseline is not None:
        from smartrouter.pricing.engine import cost

        base_cost = cost(
            plan.baseline.price,
            a.input_tokens,
            min(a.output_tokens, plan.baseline.max_output_tokens),
        )
    return SimulateOut(
        policy=plan.policy.name,
        strategy=plan.policy.strategy,
        passthrough=plan.passthrough,
        floor=plan.ranking.floor,
        floor_relaxed=plan.ranking.floor_relaxed,
        analysis=AnalysisOut(
            task=a.task,
            complexity=a.complexity,
            band=a.band,
            signals=list(a.signals),
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            needs_tools=a.needs_tools,
            needs_vision=a.needs_vision,
            needs_json=a.needs_json,
        ),
        selected=selected.alias if selected else None,
        baseline_model=plan.baseline.alias if plan.baseline else None,
        baseline_cost_usd=nusd_to_usd(base_cost),
        estimated_cost_usd=nusd_to_usd(est),
        estimated_savings_pct=round((base_cost - est) * 100 / base_cost, 1)
        if base_cost and selected
        else None,
        candidates=[c.as_dict() for c in plan.ranking.candidates],
        routing_us=plan.routing_us,
    )
