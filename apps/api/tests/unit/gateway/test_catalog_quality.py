import uuid
from decimal import Decimal

import pytest

from smartrouter.gateway.catalog import BAND_WEIGHT, Catalog, CatalogModel
from smartrouter.pricing.engine import Price
from smartrouter.routing.engine import TIER_PRIOR, ModelView


def _model(alias: str, tier: str) -> CatalogModel:
    view = ModelView(
        alias, "ep", tier, Price(Decimal(1), Decimal(1)), 128000, 8192, True, False, True, 500
    )
    return CatalogModel(view, uuid.uuid4(), alias, "o200k_base")


def _catalog(quality: dict[tuple[str, str, str], tuple[float, int]]) -> Catalog:
    models = {"pro": _model("pro", "advanced"), "fast": _model("fast", "fast")}
    return Catalog(models, {}, {}, None, {}, quality)


def test_unmeasured_model_returns_none_so_the_engine_uses_the_prior() -> None:
    assert _catalog({}).quality_lookup("pro", "math", "low") is None


def test_manual_scores_are_taken_as_given() -> None:
    cat = _catalog({("pro", "math", "low"): (0.42, 0)})
    assert cat.quality_lookup("pro", "math", "low") == (0.42, "manual")


def test_a_small_lucky_cell_is_shrunk_toward_the_model_band_score() -> None:
    # fast is weak across easy tasks, but passed its 2 easy maths cases.
    q = {("fast", t, "low"): (0.4, 10) for t in ("chat", "coding", "summarization")}
    q[("fast", "math", "low")] = (1.0, 2)
    score, source = _catalog(q).quality_lookup("fast", "math", "low")  # type: ignore[misc]
    assert source == "benchmark"
    assert 0.5 < score < 0.9


def test_a_large_cell_dominates_its_parent() -> None:
    q = {("pro", "chat", "low"): (0.95, 200), ("pro", "math", "low"): (0.5, 200)}
    score, _ = _catalog(q).quality_lookup("pro", "math", "low")  # type: ignore[misc]
    assert score == pytest.approx(0.5, abs=0.02)


def test_task_weakness_carries_across_bands() -> None:
    # Weak at coding on easy cases, never measured on hard coding: the estimate for hard coding
    # must sit below the model's hard-band average.
    q = {
        ("fast", "chat", "low"): (0.9, 10),
        ("fast", "coding", "low"): (0.3, 10),
        ("fast", "chat", "high"): (0.6, 10),
    }
    cat = _catalog(q)
    coding, source = cat.quality_lookup("fast", "coding", "high")  # type: ignore[misc]
    chat_high, _ = cat.quality_lookup("fast", "chat", "high")  # type: ignore[misc]
    assert "estimated" in source
    assert coding < chat_high


def test_band_score_is_shrunk_toward_the_tier_prior() -> None:
    q = {("pro", "chat", "high"): (0.0, 1)}
    cat = _catalog(q)
    expected = (0.0 * 1 + TIER_PRIOR["advanced"] * BAND_WEIGHT) / (1 + BAND_WEIGHT)
    assert cat.band_scores[("pro", "high")] == pytest.approx(expected)


def test_scores_stay_between_zero_and_one() -> None:
    q = {("pro", "chat", "low"): (1.0, 50), ("pro", "math", "low"): (1.0, 50)}
    score, _ = _catalog(q).quality_lookup("pro", "coding", "low")  # type: ignore[misc]
    assert 0.0 <= score <= 1.0


def _calibrated(calibration: dict[tuple[str, str], tuple[int, int]]) -> Catalog:
    q = {("fast", "math", "low"): (0.9, 50), ("fast", "math", "high"): (0.1, 50)}
    return Catalog({"fast": _model("fast", "fast")}, {}, {}, None, {}, q, calibration=calibration)


def test_p_hard_uses_the_prior_until_measured_then_the_data() -> None:
    assert _calibrated({}).p_hard("math", "low") == pytest.approx(0.2)
    assert _calibrated({}).p_hard("math", "high") == pytest.approx(0.8)
    measured = _calibrated({("math", "low"): (50, 100)})
    assert measured.p_hard("math", "low") == pytest.approx((50 + 0.2 * 4) / 104)


def test_expected_quality_mixes_bands_by_how_often_the_analyzer_is_wrong() -> None:
    reliable = _calibrated({("math", "low"): (0, 100)})
    unreliable = _calibrated({("math", "low"): (60, 100)})
    good, _ = reliable.expected_quality("fast", "math", "low")  # type: ignore[misc]
    bad, source = unreliable.expected_quality("fast", "math", "low")  # type: ignore[misc]
    assert good > 0.8 > 0.6 > bad
    assert "P(hard)" in source


def test_a_caller_set_complexity_is_trusted() -> None:
    cat = _calibrated({("math", "low"): (60, 100)})
    trusted, _ = cat.expected_quality("fast", "math", "low", trusted=True)  # type: ignore[misc]
    assert trusted == cat.quality_lookup("fast", "math", "low")[0]  # type: ignore[index]


def test_calibration_parses_from_a_benchmark_summary() -> None:
    from smartrouter.gateway.catalog import parse_calibration

    assert parse_calibration({"math|low": [3, 10], "bad|band": [1, 1]}) == {
        ("math", "low"): (3, 10)
    }
