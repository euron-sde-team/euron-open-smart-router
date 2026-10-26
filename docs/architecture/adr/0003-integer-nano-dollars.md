# ADR-0003: Money is integer nano-dollars

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

SmartRouter compares costs on every request: each candidate's estimated cost (for the cost axis
and the policy's cost ceiling), the actual cost after the call, and the baseline cost the savings
are measured against. Individual requests are tiny amounts (a classification on a small model
costs millionths of a dollar); savings reports sum millions of them. Floats drift in sums and
comparisons; a decimal type does not survive JSON in the decision stream unchanged.

## Decision

All amounts inside the system are integers in nano-dollars (1 USD = 1,000,000,000 nUSD): the
`*_nusd` `BIGINT` columns in `routing_decisions`, `decision_rollups_hourly` and
`benchmark_results`, the decision records in the Redis stream, and Python `int`. Model prices
are stored as `NUMERIC(14,6)` USD per million tokens and converted by `core/money.py`, rounding
**up**, so an estimate can only overstate a cost. A TokenShield `x-tokenshield-cost-usd` header
is parsed as a decimal and converted the same way. The API converts to USD floats only at the
edge (analytics, decision detail, response headers).

## Consequences

**Good**
- Exact sums for savings and rollups; costs compare exactly against `max_cost_usd`.
- A `BIGINT` still holds over nine billion dollars.
- One rule for contributors: no floats for money inside the API.
- Same unit as TokenShield, so the two products' numbers line up.

**Bad / caveats**
- Sub-nano-dollar costs round up to 1 nUSD. At current prices that is far below any meaningful
  amount.
- The cost axis in ranking takes a logarithm of nano-dollars; a free model needs the `eps` term in
  the engine to stay finite (see [routing.md](../routing.md#scoring)).
- Readers of the raw database see large integers; the dashboard and API always show USD.

## Alternatives considered

- **Floats.** Rejected: drift in sums and comparisons.
- **Python `Decimal` everywhere.** Rejected: does not survive the trip through JSON and Redis.
- **Micro-dollars.** Rejected: too coarse for cheap models on short requests.
