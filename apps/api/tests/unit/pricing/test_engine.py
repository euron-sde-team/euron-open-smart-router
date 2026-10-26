from decimal import Decimal

from smartrouter.pricing.engine import Price, cost

PRICE = Price(input_per_m=Decimal("2.50"), output_per_m=Decimal("10"))


def test_cost_adds_input_and_output_in_nano_dollars() -> None:
    # $2.50 per 1M input = 2500 nUSD per token; $10 per 1M output = 10000 nUSD per token.
    assert cost(PRICE, 2000, 500) == 2000 * 2500 + 500 * 10000


def test_cost_rounds_fractional_prices_up() -> None:
    p = Price(Decimal("0.0001"), Decimal("0"))
    assert cost(p, 1, 0) == 1  # 0.1 nUSD rounds up, so estimates never undercount


def test_zero_tokens_cost_nothing() -> None:
    assert cost(PRICE, 0, 0) == 0


def test_free_price() -> None:
    assert Price(Decimal(0), Decimal(0)).free
    assert not PRICE.free
