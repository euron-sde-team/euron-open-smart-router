from decimal import Decimal

import pytest

from smartrouter.pricing.engine import Price
from smartrouter.routing.analyzer import Analysis
from smartrouter.routing.engine import (
    HealthView,
    ModelView,
    PolicyView,
    Ranking,
    baseline_for,
    expected_latency,
    rank,
)


def _m(
    alias: str,
    tier: str,
    price: tuple[str, str],
    latency: int,
    *,
    context: int = 128000,
    tools: bool = True,
    vision: bool = False,
) -> ModelView:
    return ModelView(
        alias=alias,
        endpoint="ep",
        tier=tier,
        price=Price(Decimal(price[0]), Decimal(price[1])),
        context_window=context,
        max_output_tokens=8192,
        supports_tools=tools,
        supports_vision=vision,
        supports_json=True,
        latency_prior_ms=latency,
    )


MODELS = [
    _m("cheap", "low-cost", ("0.05", "0.20"), 450),
    _m("fast", "fast", ("0.10", "0.40"), 200),
    _m("pro", "advanced", ("3", "12"), 1600),
    _m("local", "local", ("0", "0"), 900, context=8000, tools=False),
]
QUALITY = {
    ("cheap", "chat", "low"): 0.80,
    ("fast", "chat", "low"): 0.85,
    ("pro", "chat", "low"): 0.97,
    ("local", "chat", "low"): 0.75,
    ("cheap", "math", "high"): 0.30,
    ("fast", "math", "high"): 0.40,
    ("pro", "math", "high"): 0.92,
    ("local", "math", "high"): 0.20,
}


def _lookup(alias: str, task: str, band: str) -> tuple[float, str] | None:
    q = QUALITY.get((alias, task, band))
    return (q, "benchmark") if q is not None else None


def _an(task: str = "chat", complexity: int = 2, **kw: object) -> Analysis:
    base: dict[str, object] = {"signals": (), "input_tokens": 200, "output_tokens": 300}
    base.update(kw)
    return Analysis(task=task, complexity=complexity, **base)  # type: ignore[arg-type]


def _rank(
    policy: PolicyView,
    analysis: Analysis | None = None,
    health: dict[str, HealthView] | None = None,
) -> Ranking:
    return rank(analysis or _an(), policy, MODELS, health or {}, _lookup)


def _selected(r: Ranking) -> str | None:
    return r.selected.alias if r.selected else None


def test_cost_strategy_picks_the_cheapest_model_above_the_floor() -> None:
    assert _selected(_rank(PolicyView("p", strategy="cost", quality_floor=0.7))) == "local"


def test_quality_strategy_picks_the_best_model() -> None:
    assert _selected(_rank(PolicyView("p", strategy="quality", quality_floor=0.7))) == "pro"


def test_latency_strategy_picks_the_fastest() -> None:
    assert _selected(_rank(PolicyView("p", strategy="latency", quality_floor=0.7))) == "fast"


def test_floor_filters_and_keeps_the_reason() -> None:
    r = _rank(PolicyView("p", strategy="cost", quality_floor=0.82))
    assert _selected(r) == "fast"
    local = next(c for c in r.candidates if c.alias == "local")
    assert not local.eligible and "below the floor 0.82" in local.reason


def test_hard_requests_raise_the_floor() -> None:
    p = PolicyView("p", strategy="cost", quality_floor=0.8, hard_floor_boost=0.1)
    assert p.floor_for(_an(complexity=4)) == pytest.approx(0.9)
    r = _rank(p, _an("math", 5))
    assert _selected(r) == "pro" and r.floor == pytest.approx(0.9)


def test_task_floor_overrides_the_policy_floor() -> None:
    p = PolicyView("p", strategy="cost", quality_floor=0.5, task_floors={"chat": 0.9})
    assert _selected(_rank(p)) == "pro"


def test_relaxed_floor_keeps_every_model_as_a_fallback_ordered_by_quality() -> None:
    r = _rank(PolicyView("p", strategy="cost", quality_floor=0.99))
    assert r.floor_relaxed
    assert [c.alias for c in r.eligible] == ["pro", "fast", "cheap", "local"]
    assert "no model cleared the quality floor" in r.eligible[0].reason
    assert "fallback 1" in r.eligible[1].reason


def test_reject_policy_leaves_nothing_eligible() -> None:
    r = _rank(PolicyView("p", quality_floor=0.99, on_no_eligible="reject"))
    assert r.selected is None and not r.floor_relaxed


def test_open_circuit_is_skipped() -> None:
    r = _rank(PolicyView("p", strategy="quality"), health={"pro": HealthView(state="open")})
    assert _selected(r) == "fast"
    pro = next(c for c in r.candidates if c.alias == "pro")
    assert "circuit open" in pro.reason


def test_capabilities_and_context_are_hard_filters() -> None:
    r = _rank(PolicyView("p", strategy="cost", quality_floor=0.5), _an(needs_tools=True))
    assert _selected(r) == "cheap"
    r = _rank(PolicyView("p", strategy="cost", quality_floor=0.5), _an(input_tokens=20000))
    assert "context window" in next(c for c in r.candidates if c.alias == "local").reason
    r = _rank(PolicyView("p", quality_floor=0.5), _an(needs_vision=True))
    assert r.selected is None


def test_allowlist_exclusions_and_ceilings() -> None:
    assert (
        _selected(_rank(PolicyView("p", quality_floor=0.5, allowed_models=("cheap",)))) == "cheap"
    )
    assert (
        _selected(
            _rank(PolicyView("p", strategy="cost", quality_floor=0.5, excluded_models=("local",)))
        )
        == "cheap"
    )
    r = _rank(PolicyView("p", strategy="quality", quality_floor=0.5, max_cost_usd=Decimal("0.001")))
    assert _selected(r) != "pro"
    r = _rank(PolicyView("p", strategy="quality", quality_floor=0.5, max_latency_ms=1000))
    assert _selected(r) == "fast"


def test_custom_weights_are_normalised() -> None:
    p = PolicyView("p", weights={"cost": 2, "latency": 0, "quality": 2})
    assert p.resolved_weights() == {"cost": 0.5, "latency": 0.0, "quality": 0.5}


def test_unmeasured_models_use_the_tier_prior() -> None:
    r = rank(
        _an("coding", 2),
        PolicyView("p", strategy="quality", quality_floor=0.5),
        MODELS,
        {},
        _lookup,
    )
    pro = next(c for c in r.candidates if c.alias == "pro")
    assert pro.quality == 0.9 and pro.quality_source == "prior"


def test_ranking_is_deterministic_and_explained() -> None:
    a = _rank(PolicyView("p"))
    b = _rank(PolicyView("p"))
    assert [c.as_dict() for c in a.candidates] == [c.as_dict() for c in b.candidates]
    assert "best of" in a.candidates[0].reason
    assert set(a.candidates[0].as_dict()["components"]) == {"cost", "latency", "quality"}


def test_measured_latency_replaces_the_prior_once_there_are_samples() -> None:
    m = MODELS[0]
    assert expected_latency(m, HealthView(p50_ms=100, samples=3)) == 450
    assert expected_latency(m, HealthView(p50_ms=100, samples=5)) == 100


def test_baseline_is_the_policy_choice_or_the_priciest_capable_model() -> None:
    assert baseline_for(MODELS, PolicyView("p"), _an()).alias == "pro"  # type: ignore[union-attr]
    assert baseline_for(MODELS, PolicyView("p", baseline_model="fast"), _an()).alias == "fast"  # type: ignore[union-attr]
    assert baseline_for(MODELS, PolicyView("p"), _an(needs_vision=True)) is None
