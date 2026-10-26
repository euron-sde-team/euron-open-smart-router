"""Cost estimates. Pure functions over integer nano-dollars (see core/money.py)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from smartrouter.core.money import tokens_cost_nusd


@dataclass(frozen=True, slots=True)
class Price:
    input_per_m: Decimal
    output_per_m: Decimal

    @property
    def free(self) -> bool:
        return self.input_per_m == 0 and self.output_per_m == 0


def cost(price: Price, input_tokens: int, output_tokens: int) -> int:
    return tokens_cost_nusd(input_tokens, price.input_per_m) + tokens_cost_nusd(
        output_tokens, price.output_per_m
    )
