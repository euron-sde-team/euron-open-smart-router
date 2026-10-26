"""The ranking engine. A pure function: catalog, health, quality and policy in; an ordered,
explained list of candidates out.

1. **Hard filters**, in order: policy allowlist and exclusions, context window, required
   capabilities (tools, vision, JSON), circuit breaker, cost ceiling, latency ceiling, and the
   quality floor for this task and difficulty. Each filtered model keeps the reason.
2. **Score** the survivors on three normalised axes and combine them with the policy's
   weights. Cost is scaled on a log axis across the survivors (prices differ by multiples, so
   one expensive model must not squash the cheap ones together), latency linearly; the
   cheapest and fastest score 1. Quality is the benchmark score.
3. If nothing clears the quality floor, the policy decides: reject, or take the
   models that pass every *other* filter ordered by quality, best first, so the top one
   answers and the rest remain fallbacks (recorded as "floor relaxed").

Ties break toward the cheaper model, then the alias, so a decision is reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from smartrouter.pricing.engine import Price, cost
from smartrouter.routing.analyzer import Analysis

# Weights over (cost, latency, quality). "quality" keeps a sliver of cost and latency only to
# break near ties: a model must be within about 4 quality points to win on price or speed.
STRATEGIES: dict[str, dict[str, float]] = {
    "cost": {"cost": 0.7, "latency": 0.1, "quality": 0.2},
    "latency": {"cost": 0.1, "latency": 0.7, "quality": 0.2},
    "quality": {"cost": 0.02, "latency": 0.02, "quality": 0.96},
    "balanced": {"cost": 0.35, "latency": 0.25, "quality": 0.4},
}
# Prior quality by tier, used until a benchmark has measured a model on a task.
TIER_PRIOR = {"advanced": 0.9, "fast": 0.72, "low-cost": 0.66, "local": 0.6}


@dataclass(frozen=True, slots=True)
class ModelView:
    alias: str
    endpoint: str
    tier: str
    price: Price
    context_window: int
    max_output_tokens: int
    supports_tools: bool
    supports_vision: bool
    supports_json: bool
    latency_prior_ms: int


@dataclass(frozen=True, slots=True)
class HealthView:
    state: str = "closed"  # closed | open | half_open
    p50_ms: float | None = None
    p95_ms: float | None = None
    error_rate: float = 0.0
    samples: int = 0


@dataclass(frozen=True, slots=True)
class PolicyView:
    name: str
    strategy: str = "balanced"
    weights: dict[str, float] | None = None
    quality_floor: float = 0.7
    task_floors: dict[str, float] = field(default_factory=dict)
    hard_floor_boost: float = 0.1
    max_latency_ms: int | None = None
    max_cost_usd: Decimal | None = None
    allowed_models: tuple[str, ...] | None = None
    excluded_models: tuple[str, ...] = ()
    allow_fallback: bool = True
    max_attempts: int = 3
    budget_downgrade: bool = False
    on_no_eligible: str = "best_quality"
    baseline_model: str | None = None

    def floor_for(self, analysis: Analysis) -> float:
        base = float(self.task_floors.get(analysis.task, self.quality_floor))
        return min(1.0, base + (self.hard_floor_boost if analysis.complexity >= 4 else 0.0))

    def resolved_weights(self) -> dict[str, float]:
        w = (
            dict(self.weights)
            if self.weights
            else dict(STRATEGIES.get(self.strategy, STRATEGIES["balanced"]))
        )
        total = sum(max(0.0, float(v)) for v in w.values()) or 1.0
        return {k: max(0.0, float(w.get(k, 0.0))) / total for k in ("cost", "latency", "quality")}


@dataclass(frozen=True, slots=True)
class Candidate:
    alias: str
    eligible: bool
    reason: str
    score: float = 0.0
    cost_nusd: int = 0
    latency_ms: float = 0.0
    quality: float = 0.0
    quality_source: str = "prior"
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.alias,
            "eligible": self.eligible,
            "reason": self.reason,
            "score": round(self.score, 4),
            "estimated_cost_nusd": self.cost_nusd,
            "latency_ms": round(self.latency_ms, 1),
            "quality": round(self.quality, 3),
            "quality_source": self.quality_source,
            "components": {k: round(v, 4) for k, v in self.components.items()},
        }


@dataclass(frozen=True, slots=True)
class Ranking:
    candidates: tuple[Candidate, ...]  # eligible first, best first; then the filtered ones
    floor: float
    floor_relaxed: bool = False

    @property
    def eligible(self) -> list[Candidate]:
        return [c for c in self.candidates if c.eligible]

    @property
    def selected(self) -> Candidate | None:
        e = self.eligible
        return e[0] if e else None


QualityLookup = Callable[[str, str, str], tuple[float, str] | None]


def expected_latency(model: ModelView, health: HealthView) -> float:
    if health.p50_ms is not None and health.samples >= 5:
        return float(health.p50_ms)
    return float(model.latency_prior_ms)


def rank(
    analysis: Analysis,
    policy: PolicyView,
    models: Sequence[ModelView],
    health: dict[str, HealthView],
    quality: QualityLookup,
) -> Ranking:
    floor = policy.floor_for(analysis)
    filtered: list[Candidate] = []
    floor_only: list[Candidate] = []  # failed nothing but the quality floor
    survivors: list[Candidate] = []

    for m in models:
        h = health.get(m.alias, HealthView())
        est_cost = cost(
            m.price, analysis.input_tokens, min(analysis.output_tokens, m.max_output_tokens)
        )
        latency = expected_latency(m, h)
        q = quality(m.alias, analysis.task, analysis.band)
        if q is not None:
            q_score, q_source = q
        else:
            q_score, q_source = TIER_PRIOR.get(m.tier, 0.6), "prior"
        base = Candidate(
            m.alias,
            False,
            "",
            cost_nusd=est_cost,
            latency_ms=latency,
            quality=q_score,
            quality_source=q_source,
        )

        reason = None
        if policy.allowed_models is not None and m.alias not in policy.allowed_models:
            reason = "not in the policy's allowed models"
        elif m.alias in policy.excluded_models:
            reason = "excluded by the policy"
        elif (
            analysis.input_tokens + min(analysis.output_tokens, m.max_output_tokens)
            > m.context_window
        ):
            reason = (
                f"context window {m.context_window} too small "
                f"for ~{analysis.input_tokens} input tokens"
            )
        elif analysis.needs_tools and not m.supports_tools:
            reason = "no tool calling"
        elif analysis.needs_vision and not m.supports_vision:
            reason = "no image input"
        elif analysis.needs_json and not m.supports_json:
            reason = "no JSON output mode"
        elif h.state == "open":
            reason = "circuit open: recent failures"
        elif policy.max_cost_usd is not None and est_cost > int(
            policy.max_cost_usd * 1_000_000_000
        ):
            reason = f"estimated cost above the policy ceiling of ${policy.max_cost_usd}"
        elif policy.max_latency_ms is not None and latency > policy.max_latency_ms:
            reason = (
                f"expected latency {latency:.0f} ms above the {policy.max_latency_ms} ms ceiling"
            )
        if reason:
            filtered.append(replace(base, reason=reason))
            continue
        if q_score < floor:
            floor_only.append(
                replace(
                    base,
                    reason=f"{analysis.task} quality {q_score:.2f} below the floor {floor:.2f}",
                )
            )
            continue
        survivors.append(base)

    relaxed = False
    if not survivors and floor_only and policy.on_no_eligible == "best_quality":
        # Every model that failed only the floor stays available, best quality first, so a
        # failure of the top model still has a fallback.
        survivors, floor_only = floor_only, []
        relaxed = True

    weights = policy.resolved_weights()
    scored: list[Candidate] = []
    if survivors:
        # eps keeps free models finite and makes sub-0.1% differences irrelevant.
        eps = max(1.0, max(c.cost_nusd for c in survivors) * 0.001)
        logs = [math.log(c.cost_nusd + eps) for c in survivors]
        lats = [c.latency_ms for c in survivors]
        lo_c, hi_c = min(logs), max(logs)
        lo_l, hi_l = min(lats), max(lats)
        for c, log_cost in zip(survivors, logs, strict=True):
            cost_score = 1.0 if hi_c == lo_c else (hi_c - log_cost) / (hi_c - lo_c)
            lat_score = 1.0 if hi_l == lo_l else (hi_l - c.latency_ms) / (hi_l - lo_l)
            components = {
                "cost": weights["cost"] * cost_score,
                "latency": weights["latency"] * lat_score,
                "quality": weights["quality"] * c.quality,
            }
            scored.append(
                replace(c, eligible=True, score=sum(components.values()), components=components)
            )
        if relaxed:
            scored.sort(key=lambda c: (-c.quality, c.cost_nusd, c.alias))
        else:
            scored.sort(key=lambda c: (-c.score, c.cost_nusd, c.alias))
        best = scored[0]
        scored[0] = replace(best, reason=_why(best, policy, relaxed, len(scored)))
        for i, c in enumerate(scored[1:], start=2):
            why = (
                f"fallback {i - 1} with the floor relaxed (quality {c.quality:.2f})"
                if relaxed
                else f"eligible, ranked {i} (score {c.score:.3f})"
            )
            scored[i - 1] = replace(c, reason=why)

    rejected = sorted(floor_only + filtered, key=lambda c: c.alias)
    return Ranking(tuple(scored) + tuple(rejected), floor, relaxed)


def _why(c: Candidate, policy: PolicyView, relaxed: bool, n: int) -> str:
    if relaxed:
        return (
            f"no model cleared the quality floor; picked the highest quality capable model "
            f"({c.quality:.2f}) as policy '{policy.name}' allows"
        )
    top = max(c.components, key=lambda k: c.components[k]) if c.components else "quality"
    lead = {"cost": "cheapest", "latency": "fastest", "quality": "highest quality"}[top]
    return (
        f"best of {n} eligible under '{policy.name}' ({policy.strategy}): {lead} contributor, "
        f"quality {c.quality:.2f} ({c.quality_source})"
    )


def baseline_for(
    models: Sequence[ModelView], policy: PolicyView, analysis: Analysis
) -> ModelView | None:
    """The model savings are measured against: the policy's choice, else the most expensive
    model that could have served the request (what a team defaults to without a router)."""
    if policy.baseline_model:
        for m in models:
            if m.alias == policy.baseline_model:
                return m
    capable = [
        m
        for m in models
        if analysis.input_tokens + min(analysis.output_tokens, m.max_output_tokens)
        <= m.context_window
        and (not analysis.needs_tools or m.supports_tools)
        and (not analysis.needs_vision or m.supports_vision)
    ]
    if not capable:
        return None
    return max(
        capable,
        key=lambda m: (cost(m.price, analysis.input_tokens, analysis.output_tokens), m.alias),
    )
