# ADR-0009: Circuit breakers and health in Redis Lua, shared across replicas

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

The router must not send traffic to a model that is failing, and must notice when it recovers.
The api runs as N stateless replicas behind a load balancer. Health kept in each process's memory
would mean each replica learns about an outage separately (N times the failed requests before
every replica stops), replicas disagree about which models are healthy, a new or restarted replica
starts blind, and N replicas each send their own probe to a recovering model.

Health updates also race: many requests to the same model finish at the same moment, and the
decision to open a circuit depends on a consecutive-failure count and an error rate over a window
that all of them update.

## Decision

All health state lives in Redis, and every upstream outcome is applied by **one Lua script**
(`health/tracker.py _RECORD`) that, atomically:

1. increments the outcome count in the current one-minute bucket (`sr:h:<alias>:<minute>`, 360 s
   TTL);
2. pushes a latency sample onto a ring of the last 200 (`sr:hl:<alias>`) for successes;
3. updates the breaker hash (`sr:cb:<alias>`): resets the consecutive count on success and closes a
   non-closed circuit; on failure increments it, re-opens an open circuit, or opens a closed one
   when the count reaches `BREAKER_FAILURE_THRESHOLD` or the five-bucket error rate exceeds
   `BREAKER_ERROR_RATE` with at least `BREAKER_MIN_SAMPLES` outcomes.

Half-open is derived on read: a stored `open` breaker whose cooldown has passed is reported as
`half_open`, so no timer is needed and every replica sees the transition at the same moment. Only
one request at a time may probe a half-open model: the one that wins
`SET sr:cbp:<alias> 1 NX EX <cooldown>`. Routing reads every model's health in one pipeline per
request. The worker's leased probe adds an outside-in signal for endpoints with no traffic.

## Consequences

**Good**
- An outage seen by any replica opens the circuit for all of them on the next request, and
  recovery is decided once, by one probe, for the whole deployment.
- No lost updates: concurrent outcomes are serialised by Redis, so the consecutive count and the
  window are exact.
- Replicas start with the shared view, and `GET /api/v1/health/models` shows exactly what the
  router sees.
- One script call per outcome, recorded off the response path; one pipeline per routed request.

**Bad / caveats**
- Redis is on the routing path. When it is unreachable, routing continues without health
  awareness (every model treated as closed and ranked on its latency prior, the probe gate letting
  every request through) rather than failing; open circuits are forgotten if Redis loses its
  data.
- Thresholds are global settings, not per model or per policy.
- Failures that arrive while a circuit is already open restart its cooldown, so a model with a
  long tail of in-flight timeouts stays out a little longer.

## Alternatives considered

- **In-process breakers per replica.** Rejected for the reasons above; it is what most client
  libraries do, and it does not fit N replicas.
- **Postgres for health state.** Rejected: a row update per upstream outcome on the hot path, and
  no atomic multi-key script.
- **A separate health service.** Rejected: another moving part for what one Lua script does.
