# ADR-0004: Decisions go through a Redis Stream, drained to Postgres by workers

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

Every gateway request, routed, passed through, rejected or failed, produces a decision record:
the analysis signals, every candidate with its score and reason, the attempt chain, costs and
timing. It is the product's explanation and its savings ledger, so it must not be lost. Writing it
synchronously to Postgres would put a database round trip and a row with several JSONB columns
on the critical path of every request. TokenShield measured this cost for its usage events
(about 10.6 ms at p50 on a laptop stack, roughly half of a cache-miss request) and moved them to
a Redis Stream; SmartRouter reuses that proven pattern rather than repeating the experiment.

## Decision

The gateway appends each record to the Redis Stream `sr:decisions:stream` with one `XADD`
(approximate cap of 1,000,000 entries). Workers read it through the consumer group `recorders`
(`XREADGROUP`, batches of up to 500, blocking 1 s), insert each batch in one statement with
`ON CONFLICT (request_id) DO NOTHING`, and acknowledge and delete the entries only after the
commit. Every 30 s a worker claims entries that another consumer read but did not acknowledge
for 60 s (`XAUTOCLAIM`) and commits them. If Redis rejects the `XADD`, the gateway inserts the
record directly into Postgres.

## Consequences

**Good**
- Recording is one Redis round trip on the request path; Postgres sees batched inserts.
- Any number of workers can drain in parallel; a crashed worker's entries are reclaimed.
- At-least-once delivery never duplicates a row, because `request_id` is unique.
- A Redis outage degrades to synchronous inserts instead of losing decisions.

**Bad / caveats**
- The decision log and analytics are eventually consistent with the gateway: milliseconds with
  a worker running; without one, decisions accumulate in Redis up to the cap.
- If Redis loses data before a worker drains it, up to about a second of decisions can be lost
  (AOF `everysec`). Savings reports would then slightly undercount; routing is unaffected.
- `routing_decisions` is append-only, so a decision cannot be corrected in place; there is
  nothing in 0.1.0 that needs to.

## Alternatives considered

- **Synchronous Postgres insert.** Correct and simple; rejected on the latency TokenShield
  measured for the same pattern.
- **In-process batching.** Fast, but a crashed api process loses its buffer.
- **SQS or Kafka.** Adds a moving part to every deployment; Redis is already required for health.
