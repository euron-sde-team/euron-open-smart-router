# ADR-0008: TokenShield integration without a second budget ledger

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

SmartRouter is a companion to Euron Open TokenShield: TokenShield decides whether a request may
spend, SmartRouter decides which model spends it. A team running both should not see two sets of
spend numbers that disagree, and should not have to configure budgets twice.

SmartRouter needs two things from spend accounting. It needs the **actual cost** of each request
to report savings against the baseline, and it needs to **react to a budget refusal**: when a
budget is exhausted for the expensive model, a cheaper model may still be acceptable.

TokenShield already owns both. It reserves worst-case cost atomically before calling a provider,
records actual usage in an append-only ledger, reports the cost in `x-tokenshield-cost-usd`, and
refuses with `402 budget_exceeded` (carrying `x-tokenshield-request-id`).

## Decision

SmartRouter keeps a routing decision log, **not a budget ledger**. A TokenShield gateway is
configured as an endpoint of kind `tokenshield` (it speaks the OpenAI wire format) in front of
some or all models.

- **Actual cost.** The adapters keep every `x-tokenshield-*` response header. When
  `x-tokenshield-cost-usd` is present, the decision's `actual_cost_nusd` is that value and
  `cost_source` is `tokenshield`; otherwise the cost is computed from provider usage and the
  model's price (`cost_source = computed`). `x-tokenshield-request-id` is stored as
  `upstream_request_id`, so a decision links to TokenShield's usage event.
- **Budget refusals.** A `402` whose error code is `budget_exceeded`, or any `402` carrying
  `x-tokenshield-request-id`, is a budget outcome. With the policy's `budget_downgrade` on, the
  gateway tries the remaining candidates whose estimated cost is strictly below the refused one,
  in rank order. With it off, the 402 is returned to the client unchanged. A budget refusal is
  recorded as a healthy outcome for the model's circuit.
- SmartRouter enforces no spend limit of its own. Its per-key RPM and concurrency limits protect
  availability, not money, and fail open when Redis is down.

## Consequences

**Good**
- One source of truth for spend: TokenShield's ledger. SmartRouter's savings use the same cost
  TokenShield billed when the request went through it.
- Budgets are configured once, in TokenShield, and SmartRouter honours them, including by
  downgrading instead of failing when the policy allows.
- SmartRouter stays simple: no reservations, no settlement, no reconciliation of uncertain
  outcomes.

**Bad / caveats**
- Without TokenShield, SmartRouter has no hard spend control; costs in the decision log are
  computed from its own price table.
- Streaming responses through TokenShield usually carry no actual cost header (it is sent before
  the cost is known), so their cost is computed from usage.
- A downgrade picks by SmartRouter's cost estimate, which may differ from TokenShield's worst-case
  reservation; a cheaper candidate can still be refused, and is then recorded as another budget
  attempt.

## Alternatives considered

- **A budget ledger in SmartRouter too.** Rejected: duplicate accounting that drifts from
  TokenShield's and doubles the configuration.
- **Calling TokenShield's API to pre-check budgets.** Rejected: an extra round trip per request,
  and TokenShield already checks atomically on the request itself.
- **Treating 402 as a plain client error.** Rejected: loses the most useful reaction to an
  exhausted budget, a cheaper model.
