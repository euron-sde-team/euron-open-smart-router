"""Routing analytics over hourly rollups plus raw decisions after the last rolled hour.

Rollups keep dashboard queries proportional to hours, not requests; the raw tail keeps them
current. Savings are measured per request against its baseline model (the actual tokens at
the baseline's price), so they are comparable across policies and traffic mixes.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import Select, String, case, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.core.money import nusd_to_usd
from smartrouter.decisions.models import DecisionFeedback, DecisionRollupHourly, RoutingDecision
from smartrouter.decisions.schemas import BreakdownRow, Overview, SeriesPoint, Totals
from smartrouter.projects.models import Project

# A failed request has no final model; say so instead of showing a blank row.
EMPTY_LABEL = {"model": "No model answered"}

RANGES = {
    "1h": timedelta(hours=1),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
CACHE_TTL_S = 10
NIL = uuid.UUID(int=0)


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


async def raw_from(session: AsyncSession) -> datetime:
    last = await session.scalar(select(func.max(DecisionRollupHourly.hour)))
    return last + timedelta(hours=1) if last else datetime(2000, 1, 1, tzinfo=UTC)


def _unified(
    since: datetime, until: datetime, project_id: uuid.UUID | None, raw_start: datetime
) -> Any:
    r = DecisionRollupHourly
    roll = select(
        r.hour.label("hour"),
        r.project_id.label("project_id"),
        r.policy_name.label("policy"),
        r.task.label("task"),
        r.final_model.label("model"),
        r.status.label("status"),
        r.requests.label("requests"),
        r.fallbacks.label("fallbacks"),
        r.cost_nusd.label("cost"),
        r.baseline_cost_nusd.label("baseline"),
        r.latency_ms_sum.label("latency"),
        r.input_tokens.label("tin"),
        r.output_tokens.label("tout"),
    ).where(r.hour >= _hour(since), r.hour < min(raw_start, until))
    d = RoutingDecision
    raw = select(
        func.date_trunc("hour", d.created_at).label("hour"),
        func.coalesce(d.project_id, NIL).label("project_id"),
        func.coalesce(d.policy_name, "passthrough").label("policy"),
        d.task.label("task"),
        func.coalesce(d.final_model, "").label("model"),
        d.status.label("status"),
        literal(1).label("requests"),
        case((d.attempts > 1, 1), else_=0).label("fallbacks"),
        d.actual_cost_nusd.label("cost"),
        d.baseline_cost_nusd.label("baseline"),
        d.latency_ms.label("latency"),
        d.input_tokens.label("tin"),
        d.output_tokens.label("tout"),
    ).where(d.created_at >= max(raw_start, since), d.created_at < until, d.source != "benchmark")
    if project_id:
        roll = roll.where(r.project_id == project_id)
        raw = raw.where(d.project_id == project_id)
    return union_all(roll, raw).subquery("u")


def _totals_select(u: Any) -> Select[Any]:
    ok = u.c.status == "SUCCESS"
    return select(
        func.coalesce(func.sum(u.c.requests), 0),
        func.coalesce(func.sum(case((ok, u.c.requests), else_=0)), 0),
        func.coalesce(func.sum(case((ok, 0), else_=u.c.requests)), 0),
        func.coalesce(func.sum(u.c.fallbacks), 0),
        func.coalesce(func.sum(u.c.cost), 0),
        func.coalesce(func.sum(case((ok, u.c.baseline), else_=0)), 0),
        func.coalesce(func.sum(case((ok, u.c.latency), else_=0)), 0),
        func.coalesce(func.sum(u.c.tin), 0),
        func.coalesce(func.sum(u.c.tout), 0),
    )


async def _totals(
    session: AsyncSession, since: datetime, until: datetime, project_id: uuid.UUID | None
) -> Totals:
    u = _unified(since, until, project_id, await raw_from(session))
    req, ok, failed, fb, cost, base, lat, tin, tout = (
        int(v) for v in (await session.execute(_totals_select(u))).one()
    )
    savings = max(0, base - cost)
    return Totals(
        requests=req,
        succeeded=ok,
        failed=failed,
        fallbacks=fb,
        cost_usd=nusd_to_usd(cost),
        baseline_cost_usd=nusd_to_usd(base),
        savings_usd=nusd_to_usd(savings),
        savings_pct=round(savings * 100 / base, 1) if base else 0.0,
        avg_latency_ms=round(lat / ok, 1) if ok else 0.0,
        fallback_rate=round(fb / req, 4) if req else 0.0,
        input_tokens=tin,
        output_tokens=tout,
    )


class Analytics:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def _cached(self, key: str, compute: Any) -> Any:
        with contextlib.suppress(RedisError):
            hit = await self.redis.get(f"sr:an:{key}")
            if hit:
                return orjson.loads(hit)
        value = await compute()
        data = orjson.loads(orjson.dumps(value, default=lambda o: o.model_dump(mode="json")))
        with contextlib.suppress(RedisError):
            await self.redis.set(f"sr:an:{key}", orjson.dumps(data), ex=CACHE_TTL_S)
        return data

    async def overview(self, range_: str, project_id: uuid.UUID | None) -> Overview:
        async def compute() -> Overview:
            now = datetime.now(UTC)
            span = RANGES[range_]
            overhead = await self.session.scalar(
                select(func.percentile_cont(0.5).within_group(RoutingDecision.routing_us)).where(
                    RoutingDecision.created_at >= now - span, RoutingDecision.status == "SUCCESS"
                )
            )
            fb = (
                await self.session.execute(
                    select(
                        func.count(), func.sum(case((DecisionFeedback.score > 0, 1), else_=0))
                    ).where(DecisionFeedback.created_at >= now - span)
                )
            ).one()
            return Overview(
                range=range_,
                since=now - span,
                totals=await _totals(self.session, now - span, now, project_id),
                previous=await _totals(self.session, now - 2 * span, now - span, project_id),
                routing_overhead_us_p50=float(overhead) if overhead is not None else None,
                feedback_positive_rate=round(int(fb[1] or 0) / int(fb[0]), 3) if fb[0] else None,
            )

        return Overview.model_validate(await self._cached(f"ov:{range_}:{project_id}", compute))

    async def timeseries(self, range_: str, project_id: uuid.UUID | None) -> list[SeriesPoint]:
        async def compute() -> list[SeriesPoint]:
            now = datetime.now(UTC)
            span = RANGES[range_]
            u = _unified(now - span, now, project_id, await raw_from(self.session))
            hourly = span <= timedelta(days=2)
            bucket = u.c.hour if hourly else func.date_trunc("day", u.c.hour)
            ok = u.c.status == "SUCCESS"
            rows = (
                await self.session.execute(
                    select(
                        bucket.label("t"),
                        func.sum(u.c.requests),
                        func.sum(u.c.cost),
                        func.sum(case((ok, u.c.baseline), else_=0)),
                        func.sum(u.c.fallbacks),
                        func.sum(case((ok, 0), else_=u.c.requests)),
                    )
                    .group_by("t")
                    .order_by("t")
                )
            ).all()
            by_t = {r[0]: r for r in rows}
            step = timedelta(hours=1) if hourly else timedelta(days=1)
            t = (
                _hour(now - span)
                if hourly
                else (now - span).replace(hour=0, minute=0, second=0, microsecond=0)
            )
            points = []
            while t <= now:
                r = by_t.get(t)
                points.append(
                    SeriesPoint(
                        t=t,
                        requests=int(r[1]) if r else 0,
                        cost_usd=nusd_to_usd(int(r[2])) if r else 0,
                        baseline_cost_usd=nusd_to_usd(int(r[3])) if r else 0,
                        fallbacks=int(r[4]) if r else 0,
                        failed=int(r[5]) if r else 0,
                    )
                )
                t += step
            return points

        return [
            SeriesPoint.model_validate(p)
            for p in await self._cached(f"series:{range_}:{project_id}", compute)
        ]

    async def breakdown(
        self, range_: str, by: str, project_id: uuid.UUID | None
    ) -> list[BreakdownRow]:
        async def compute() -> list[BreakdownRow]:
            now = datetime.now(UTC)
            u = _unified(now - RANGES[range_], now, project_id, await raw_from(self.session))
            col = {
                "model": u.c.model,
                "task": u.c.task,
                "policy": u.c.policy,
                "project": cast(u.c.project_id, String),
                "status": u.c.status,
            }[by]
            ok = u.c.status == "SUCCESS"
            rows = (
                await self.session.execute(
                    select(
                        col.label("k"),
                        func.sum(u.c.requests),
                        func.sum(u.c.cost),
                        func.sum(case((ok, u.c.baseline), else_=0)),
                        func.sum(case((ok, u.c.latency), else_=0)),
                        func.sum(case((ok, u.c.requests), else_=0)),
                    )
                    .group_by("k")
                    .order_by(func.sum(u.c.requests).desc())
                    .limit(25)
                )
            ).all()
            labels: dict[str, str] = {}
            if by == "project":
                ids = []
                for r in rows:
                    with contextlib.suppress(ValueError):
                        ids.append(uuid.UUID(str(r[0])))
                for i, name in (
                    await self.session.execute(
                        select(Project.id, Project.name).where(Project.id.in_(ids))
                    )
                ).all():
                    labels[str(i)] = name
                labels[str(NIL)] = "No project"
            total = sum(int(r[1]) for r in rows) or 1
            return [
                BreakdownRow(
                    key=str(r[0]),
                    label=labels.get(str(r[0]), str(r[0]) or EMPTY_LABEL.get(by, "none")),
                    requests=int(r[1]),
                    cost_usd=nusd_to_usd(int(r[2])),
                    baseline_cost_usd=nusd_to_usd(int(r[3])),
                    savings_usd=nusd_to_usd(max(0, int(r[3]) - int(r[2]))),
                    share=round(int(r[1]) / total, 4),
                    avg_latency_ms=round(int(r[4]) / int(r[5]), 1) if int(r[5]) else 0.0,
                )
                for r in rows
            ]

        return [
            BreakdownRow.model_validate(p)
            for p in await self._cached(f"bd:{range_}:{by}:{project_id}", compute)
        ]
