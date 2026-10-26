"""The gateway's read model: everything routing needs, loaded in a few queries and cached
in-process (``core.cache.ConfigCache``), invalidated cluster-wide on any admin write."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartrouter.benchmarks.models import BenchmarkRun
from smartrouter.core.sealing import KeySealer
from smartrouter.keys.models import ApiKey
from smartrouter.pricing.engine import Price
from smartrouter.projects.models import Project
from smartrouter.providers.models import Model, Provider
from smartrouter.routing.engine import TIER_PRIOR, ModelView, PolicyView
from smartrouter.routing.models import QualityScore, RoutingPolicy

# Quality is shrunk hierarchically, because a benchmark cell (model, task, band) holds only a
# handful of cases and 3 of 3 passed is weak evidence. The parent estimate for a cell is the
# model's score over every task in that band (many cases, shrunk toward the tier prior) plus
# the model's offset on this task, pooled over both bands (shrunk toward zero). The cell is
# then blended toward its parent. Weights are pseudo-samples: how many cases the parent counts as.
CELL_WEIGHT = 2.0
TASK_WEIGHT = 2.0
BAND_WEIGHT = 2.0
# How often the analyzer's band is right, before a benchmark has measured it: a request it
# calls easy is hard 20% of the time, one it calls hard is hard 80% of the time. Shrinkage
# weight in pseudo-cases.
P_HARD_PRIOR = {"low": 0.2, "high": 0.8}
P_HARD_WEIGHT = 4.0


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    timeout_s: float | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class CatalogModel:
    view: ModelView
    endpoint_id: uuid.UUID
    upstream_model: str
    tokenizer: str


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: uuid.UUID
    name: str
    default_policy: str | None


@dataclass(frozen=True, slots=True)
class KeyInfo:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    secret_hash: str
    rpm_limit: int | None
    concurrency_limit: int | None
    expires_at: datetime | None
    revoked: bool


@dataclass(frozen=True, slots=True)
class Catalog:
    models: dict[str, CatalogModel]
    endpoints: dict[uuid.UUID, Endpoint]
    policies: dict[str, PolicyView]
    default_policy: str | None
    projects: dict[uuid.UUID, ProjectInfo]
    quality: dict[tuple[str, str, str], tuple[float, int]]
    endpoint_keys: dict[uuid.UUID, str | None] = field(default_factory=dict)
    # (predicted task, predicted band) -> (cases that were truly hard, cases), measured by the
    # benchmark: how far to trust the analyzer's difficulty estimate.
    calibration: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    band_scores: dict[tuple[str, str], float] = field(init=False)
    task_offsets: dict[tuple[str, str], float] = field(init=False)

    def __post_init__(self) -> None:
        measured = [(k, v) for k, v in self.quality.items() if v[1] > 0]
        totals: dict[tuple[str, str], list[float]] = {}
        for (alias, _task, band), (score, n) in measured:
            t = totals.setdefault((alias, band), [0.0, 0.0])
            t[0] += score * n
            t[1] += n
        bands: dict[tuple[str, str], float] = {}
        for (alias, band), (passed, count) in totals.items():
            prior = self._prior(alias)
            bands[(alias, band)] = (passed + prior * BAND_WEIGHT) / (count + BAND_WEIGHT)
        residuals: dict[tuple[str, str], list[float]] = {}
        for (alias, task, band), (score, n) in measured:
            r = residuals.setdefault((alias, task), [0.0, 0.0])
            r[0] += (score - bands[(alias, band)]) * n
            r[1] += n
        offsets = {k: total / (count + TASK_WEIGHT) for k, (total, count) in residuals.items()}
        object.__setattr__(self, "band_scores", bands)
        object.__setattr__(self, "task_offsets", offsets)

    def _prior(self, alias: str) -> float:
        m = self.models.get(alias)
        return TIER_PRIOR.get(m.view.tier, 0.6) if m else 0.6

    def quality_lookup(self, alias: str, task: str, band: str) -> tuple[float, str] | None:
        hit = self.quality.get((alias, task, band))
        if hit is not None and hit[1] <= 0:
            return hit[0], "manual"  # set by an operator: taken as given
        band_score = self.band_scores.get((alias, band))
        if band_score is None:
            return None  # never measured in this band: the engine uses the tier prior
        parent = min(1.0, max(0.0, band_score + self.task_offsets.get((alias, task), 0.0)))
        if hit is None:
            return round(parent, 4), f"benchmark (estimated from {band} band and task)"
        score, samples = hit
        blended = (score * samples + parent * CELL_WEIGHT) / (samples + CELL_WEIGHT)
        return round(blended, 4), "benchmark"

    def p_hard(self, task: str, band: str) -> float:
        """Probability that a request the analyzer put in ``band`` is truly hard."""
        prior = P_HARD_PRIOR[band]
        hard, n = self.calibration.get((task, band), (0, 0))
        return (hard + prior * P_HARD_WEIGHT) / (n + P_HARD_WEIGHT)

    def expected_quality(
        self, alias: str, task: str, band: str, *, trusted: bool = False
    ) -> tuple[float, str] | None:
        """Quality the router ranks by. The analyzer's difficulty band is an estimate, so unless
        the caller set the complexity (``trusted``), the score is the expectation over both
        bands, weighted by how often that estimate is right."""
        if trusted:
            return self.quality_lookup(alias, task, band)
        hard = self.quality_lookup(alias, task, "high")
        easy = self.quality_lookup(alias, task, "low")
        if hard is None and easy is None:
            return None
        prior = self._prior(alias)
        q_hard = hard[0] if hard else prior
        q_easy = easy[0] if easy else prior
        p = self.p_hard(task, band)
        score = p * q_hard + (1 - p) * q_easy
        return round(score, 4), f"benchmark, expected over difficulty (P(hard) {p:.2f})"

    def views(self) -> list[ModelView]:
        return [m.view for m in self.models.values()]


_opened: dict[tuple[uuid.UUID, str], str] = {}


async def _open_key(sealer: KeySealer, p: Provider) -> str | None:
    if not p.sealed_key:
        return None
    k = (p.id, hashlib.sha256(p.sealed_key).hexdigest())
    if k not in _opened:
        # A rotated key replaces the old plaintext in memory rather than sitting beside it.
        for stale in [old for old in _opened if old[0] == p.id]:
            del _opened[stale]
        _opened[k] = await sealer.open(p.sealed_key, f"provider:{p.id}")
    return _opened[k]


def policy_view(p: RoutingPolicy) -> PolicyView:
    return PolicyView(
        name=p.name,
        strategy=p.strategy,
        weights={k: float(v) for k, v in p.weights.items()} if p.weights else None,
        quality_floor=p.quality_floor,
        task_floors={k: float(v) for k, v in (p.task_floors or {}).items()},
        hard_floor_boost=p.hard_floor_boost,
        max_latency_ms=p.max_latency_ms,
        max_cost_usd=Decimal(p.max_cost_usd) if p.max_cost_usd is not None else None,
        allowed_models=tuple(p.allowed_models) if p.allowed_models is not None else None,
        excluded_models=tuple(p.excluded_models or ()),
        allow_fallback=p.allow_fallback,
        max_attempts=p.max_attempts,
        budget_downgrade=p.budget_downgrade,
        on_no_eligible=p.on_no_eligible,
        baseline_model=p.baseline_model,
    )


async def load_catalog(session: AsyncSession, sealer: KeySealer) -> Catalog:
    rows = (
        await session.execute(
            select(Model, Provider)
            .join(Provider, Provider.id == Model.provider_id)
            .where(Model.enabled.is_(True), Provider.enabled.is_(True))
        )
    ).all()
    models: dict[str, CatalogModel] = {}
    endpoints: dict[uuid.UUID, Endpoint] = {}
    keys: dict[uuid.UUID, str | None] = {}
    for m, p in rows:
        if p.id not in endpoints:
            endpoints[p.id] = Endpoint(
                p.id,
                p.name,
                p.kind,
                p.base_url,
                float(p.timeout_s) if p.timeout_s is not None else None,
                p.simulated,
            )
            keys[p.id] = await _open_key(sealer, p)
        models[m.alias] = CatalogModel(
            view=ModelView(
                alias=m.alias,
                endpoint=p.name,
                tier=m.tier,
                price=Price(Decimal(m.input_per_m), Decimal(m.output_per_m)),
                context_window=m.context_window,
                max_output_tokens=m.max_output_tokens,
                supports_tools=m.supports_tools,
                supports_vision=m.supports_vision,
                supports_json=m.supports_json,
                latency_prior_ms=m.latency_prior_ms,
            ),
            endpoint_id=p.id,
            upstream_model=m.upstream_model,
            tokenizer=m.tokenizer,
        )
    policy_rows = (
        await session.scalars(select(RoutingPolicy).where(RoutingPolicy.enabled.is_(True)))
    ).all()
    policies = {p.name: policy_view(p) for p in policy_rows}
    default = next((p.name for p in policy_rows if p.is_default), None)
    by_id = {p.id: p.name for p in policy_rows}
    projects = {
        p.id: ProjectInfo(
            p.id, p.name, by_id.get(p.default_policy_id) if p.default_policy_id else None
        )
        for p in (await session.scalars(select(Project).where(Project.archived.is_(False)))).all()
    }
    quality = {
        (q.model_alias, q.task, q.band): (q.score, q.samples)
        for q in (await session.scalars(select(QualityScore))).all()
    }
    summary = await session.scalar(
        select(BenchmarkRun.summary)
        .where(BenchmarkRun.status == "done")
        .order_by(BenchmarkRun.finished_at.desc())
        .limit(1)
    )
    calibration = parse_calibration((summary or {}).get("calibration") or {})
    return Catalog(models, endpoints, policies, default, projects, quality, keys, calibration)


def parse_calibration(raw: dict[str, list[int]]) -> dict[tuple[str, str], tuple[int, int]]:
    """``{"math|low": [hard, n]}`` as stored in a benchmark summary."""
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for key, (hard, n) in raw.items():
        task, _, band = key.partition("|")
        if band in P_HARD_PRIOR:
            out[(task, band)] = (int(hard), int(n))
    return out


async def load_key(session: AsyncSession, prefix: str) -> KeyInfo | None:
    key = await session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        return None
    return KeyInfo(
        key.id,
        key.project_id,
        key.name,
        key.secret_hash,
        key.rpm_limit,
        key.concurrency_limit,
        key.expires_at,
        key.revoked_at is not None,
    )
