# Low-Level Design

This document walks the code: how the API is laid out, what each package owns, the gateway
pipeline step by step with the Redis commands each step issues, the decision stream, and what
the worker does. The system view is in [HLD.md](HLD.md); routing, health and the benchmark
have their own deep dives in [routing.md](routing.md),
[health-and-fallback.md](health-and-fallback.md) and [benchmarks.md](benchmarks.md). Paths are
relative to `apps/api/src/smartrouter/` unless they start with `apps/`, `infra/` or `deploy/`.

- [Repository layout](#repository-layout)
- [Layering](#layering)
- [core](#core)
- [Domain packages](#domain-packages)
- [The catalog read model](#the-catalog-read-model)
- [The request pipeline step by step](#the-request-pipeline-step-by-step)
- [Streaming](#streaming)
- [Response headers](#response-headers)
- [Error codes](#error-codes)
- [The decision stream](#the-decision-stream)
- [Provider adapters](#provider-adapters)
- [The worker](#the-worker)
- [Redis key reference](#redis-key-reference)
- [Timeouts, limits and constants](#timeouts-limits-and-constants)
- [Concurrency and idempotency guarantees](#concurrency-and-idempotency-guarantees)

## Repository layout

```
apps/api/                 FastAPI service, worker, CLI and the mock LLM (one Python package each)
  src/smartrouter/        the product
  src/mockllm/            deterministic OpenAI, Anthropic and TokenShield-style upstream (demo and tests)
  tests/unit/             no network, no database
  tests/integration/      real Postgres, Redis and the mock upstream over HTTP
apps/web/                 Next.js 15 dashboard (App Router, TanStack Query, Tailwind v4)
e2e/                      Playwright suites
deploy/compose/           compose.yaml + compose.floci.yaml (+ compose.aws.yaml)
infra/docker/             api.Dockerfile, web.Dockerfile
infra/terraform/          modules/ + envs/{local-floci,aws-dev,aws-prod}
scripts/                  gen-local-env.sh, test-db.sh, test-matrix.sh, tf-aws-on-floci.sh, check-sanitization.sh, qa/
docs/                     PLAN.md, architecture/, guides/, api/openapi.yaml
```

## Layering

The API is packaged by domain. Within a domain, dependencies point inward:

```
router.py     HTTP only: parse, authorize (role dependency), call the service, shape the response
schemas.py    Pydantic request and response models: the public contract, validated at the edge
service.py    business rules, transactions and audit rows; owns its SQLAlchemy queries
models.py     SQLAlchemy models owned by the domain
```

Services hold their own queries; there are no per-domain `repository.py` modules in 0.1.0.
Two deliberate cross-domain reads exist, both read-only: the catalog loader
([below](#the-catalog-read-model)), which builds the gateway's snapshot from the endpoint,
model, policy, project, quality and benchmark tables, and the analytics module, which joins
`routing_decisions`, `decision_rollups_hourly`, `decision_feedback` and `projects`.

Every admin mutation follows the same shape: the service changes rows, adds an `audit_log` row
in the same session (`audit/service.py audit`), commits, and the router then calls
`container.config_cache.invalidate()` when the change affects routing.

## core

| Module | Responsibility |
| ------ | -------------- |
| [`core/config.py`](../../apps/api/src/smartrouter/core/config.py) | `Settings` (pydantic-settings, every value from the environment) and `guard()`, the production boot guards. `get_settings()` is cached and calls the guard. The `breaker` property builds the `BreakerConfig`. Reference: [configuration.md](../guides/configuration.md) |
| [`core/container.py`](../../apps/api/src/smartrouter/core/container.py) | `Container`: the process-wide services built once (database, Redis, HTTP client, `ConfigCache`, adapter registry, sealer, storage), held on `app.state` instead of module globals so tests build an isolated one per app |
| [`core/db.py`](../../apps/api/src/smartrouter/core/db.py) | Async engine (pool 10, overflow 20, pre-ping, recycle 1800 s), session factory, declarative `Base` |
| [`core/redis_client.py`](../../apps/api/src/smartrouter/core/redis_client.py) | One pooled client per process: 2 s socket and connect timeouts, health check every 15 s, no retry on timeout |
| [`core/cache.py`](../../apps/api/src/smartrouter/core/cache.py) | `ConfigCache`: in-process TTL cache (5 s) with single-flight loading and cluster-wide invalidation through the Redis stamp `sr:cfg:version` |
| [`core/security.py`](../../apps/api/src/smartrouter/core/security.py) | argon2 password hashing with a constant-time dummy for unknown users, password policy, HS256 session JWTs, gateway key generation, parsing and peppered HMAC |
| [`core/sealing.py`](../../apps/api/src/smartrouter/core/sealing.py) | `KeySealer` protocol: `LocalSealer` (AES-256-GCM) and `KmsSealer` (KMS with encryption context) |
| [`core/storage.py`](../../apps/api/src/smartrouter/core/storage.py) | `ObjectStorage` protocol: `LocalStorage` (path-traversal safe) and `S3Storage` (SSE-KMS, presigned GET through `S3_PUBLIC_ENDPOINT`); holds benchmark result files |
| [`core/aws.py`](../../apps/api/src/smartrouter/core/aws.py) | boto3 clients built from configuration only; no emulator branches |
| [`core/errors.py`](../../apps/api/src/smartrouter/core/errors.py) | `AppError` hierarchy and the one place errors become JSON (`{"error": {code, type, message, details}}`, compatible with OpenAI SDK error parsing) |
| [`core/middleware.py`](../../apps/api/src/smartrouter/core/middleware.py) | Pure ASGI middleware (not `BaseHTTPMiddleware`, which buffers streams): request id, security headers, one log line per request |
| [`core/logs.py`](../../apps/api/src/smartrouter/core/logs.py) | JSON formatter with request id from a context variable and key-name redaction |
| [`core/money.py`](../../apps/api/src/smartrouter/core/money.py) | Integer nano-dollar helpers, always rounding up ([ADR-0003](adr/0003-integer-nano-dollars.md)) |
| [`core/tracing.py`](../../apps/api/src/smartrouter/core/tracing.py) | OpenTelemetry tracer, enabled by `OTEL_ENABLED` |
| [`core/deps.py`](../../apps/api/src/smartrouter/core/deps.py) | `ContainerDep`, `SessionDep`, and `client_ip`: the direct peer when `TRUSTED_PROXY_HOPS` is 0, else the Nth `X-Forwarded-For` entry from the right (entries further left are client-written and ignored). Uvicorn's own `proxy_headers` is off (`cli.py`), so this is the only place a forwarded address is trusted |
| [`main.py`](../../apps/api/src/smartrouter/main.py) | `create_app()`: routers, error handlers, GZip (over 2 KiB), CORS allowlist, request middleware; the lifespan builds the container and preloads tiktoken encodings |
| [`cli.py`](../../apps/api/src/smartrouter/cli.py) | `smartrouter serve | worker | migrate | openapi | benchmark | create-owner | seed-demo` |

## Domain packages

| Package | Owns | Notable details |
| ------- | ---- | --------------- |
| `auth` | Sign-in, sign-out, `/me`, password change, role dependencies | Lockout after 5 failures for 15 minutes; 20 sign-ins per minute per IP; password change bumps `session_version` |
| `users` | Dashboard users and roles | At least one active owner always; only an owner creates or changes users; role or disable changes bump `session_version` |
| `teams` | Teams | Only empty teams can be deleted |
| `projects` | Projects and their default routing policy | A project's `default_policy_id` is used when a request sends plain `auto` with no policy header |
| `keys` | Gateway keys | Plaintext shown once on creation; RPM and concurrency limits and expiry per key; revocation |
| `providers` | Endpoints (`providers` table) and models | Endpoint key sealed on write, never returned (`has_key`, `key_hint` only); kinds `openai`, `anthropic`, `tokenshield`; `simulated` set only at creation; the alias `auto` and `auto:*` are reserved; `urls.py check_endpoint_url` resolves the base URL's host and refuses metadata, link-local, unspecified, multicast and reserved addresses (and loopback and private ones when `ENDPOINT_ALLOW_PRIVATE=false`) on create and update (`400 endpoint_url_blocked`); moving an endpoint to another host needs `api_key` in the same request (`400 api_key_required`) |
| `pricing` | `Price`, `cost` | Pure functions over nano-dollars |
| `routing` | `analyzer.py`, `engine.py`, policy CRUD, the quality table and manual overrides, the route simulator | Setting a default takes `pg_advisory_xact_lock(hashtext('routing_policies.default'))` and clears the old default first, so concurrent requests serialise; see [routing.md](routing.md) |
| `health` | `tracker.py` (Lua record script, snapshot, probe lock), health endpoints, manual circuit reset | See [health-and-fallback.md](health-and-fallback.md) |
| `limits` | `Limiter`: sliding-window RPM, concurrency slots | Atomic Lua scripts |
| `gateway` | Router, key authentication, catalog, `GatewayService`, token estimation, metrics | The pipeline below |
| `decisions` | `recorder.py` (stream and direct writes), the decision log, feedback, `analytics.py` | See [the decision stream](#the-decision-stream) |
| `benchmarks` | `suite.yaml` and graders, `runner.py`, run CRUD and result downloads | See [benchmarks.md](benchmarks.md) |
| `alerts` | Health alerts, deduplicated by key; `circuit_opened(sessionmaker, alias, source)` | One per model per hour when a circuit opens, from gateway traffic or the worker's probe |
| `audit` | Append-only audit trail written in the same transaction as the change | |
| `system` | `/healthz`, `/readyz`, `/metrics`, `/api/v1/system` | |
| `demo` | `seed-demo`: configuration through the real services, backfilled history, live traffic through the gateway | Refuses to run with `ENVIRONMENT=production` |

## The catalog read model

The gateway needs endpoints, models, policies, projects, the quality table and the benchmark's
analyzer calibration on every request. `gateway/catalog.py load_catalog` reads them in a
handful of queries (one join of enabled models with enabled endpoints, one each for enabled
policies, non-archived projects and every `quality_scores` row, and one for the summary of the
latest finished benchmark run) and builds an immutable `Catalog` of frozen dataclasses
(`Endpoint`, `CatalogModel`, `ProjectInfo`, `PolicyView`). On construction the catalog derives
the band scores and per-task offsets used by hierarchical shrinkage
([routing.md](routing.md#the-quality-model)).

This is the one sanctioned exception to "a domain reads another domain only through its
service": it is read-only, it is a snapshot, and every write path that affects it invalidates
it. The snapshot lives in `ConfigCache` under the key `catalog`; gateway keys are cached per
prefix under `key:<prefix>` (`load_key`), so a lookup never loads every key.

Endpoint keys are opened once per sealed value: `_open_key` caches the plaintext under
`(provider_id, sha256(sealed_key))`, so a catalog refresh does not call KMS, and a rotated key
(new ciphertext, new digest) is opened on the first refresh after the rotation. When it is,
every older entry for the same provider is deleted first, so the old plaintext does not stay
in memory beside the new one.

Writes that call `config_cache.invalidate()`: endpoint create and update; model create and
update; policy create, update and delete; manual quality score; project create and update; key
update and revoke; team delete; and the benchmark runner after phase 1 writes the quality
table.

## The request pipeline step by step

`gateway/router.py` reads the body (at most 8 MiB, must be a JSON object with a non-empty
`messages` list) and calls `GatewayService.chat`. The numbers below follow the code in
`gateway/dependencies.py` and `gateway/service.py`.

### 1. Authenticate (`gateway/dependencies.py authenticate_key`)

1. The `get_catalog` dependency loads the catalog through `ConfigCache`.
2. Read `Authorization: Bearer tsr_...` (or `x-api-key`), split it into prefix and secret
   (`parse_api_key`: three parts, `tsr`, a 12-character prefix, a non-empty secret).
3. `ConfigCache.get("key:<prefix>")`. On a miss: one `SELECT` on `api_keys` by prefix.
4. HMAC-SHA256 the secret with `API_KEY_PEPPER` and compare in constant time. Check revoked,
   expired, and that the key's project is in the catalog (not archived).
5. Schedule `last_used_at` off the request path, at most once a minute per key.
6. Build `RequestContext`: request id (`req_` + 24 hex), project, key, and the optional
   headers `x-smartrouter-policy`, `x-smartrouter-task` and `x-smartrouter-complexity` (values
   trimmed and cut to 120 characters; the complexity header is used only when it is all
   digits).

Redis: `GET sr:cfg:version` at most once per second per process (inside `ConfigCache`);
`SET sr:keyused:<key_id> 1 NX EX 60` in the background task.

### 2. Resolve the policy (`GatewayService.resolve_policy`)

| `model` in the body | Result |
| ------------------- | ------ |
| `auto:<name>` | Policy `<name>` |
| `auto` | `x-smartrouter-policy` header, else the project's default policy, else the global default (`is_default`) |
| A configured model alias | Passthrough: a single candidate, no ranking, no fallback |
| Anything else | `404 model_not_found`, with the available `auto` routes in `details` |

A named policy that does not exist (or no default at all) is `404 policy_not_found`. A
failure here is recorded as a `REJECTED` decision. Redis: none.

### 3. Analyse (`routing/analyzer.py analyze`)

A pure function over the body: task, complexity 1 to 5, the signals that drove both, input
tokens (`gateway/tokens.py estimate_chat_input` with `o200k_base`), the output allowance
(`max_completion_tokens`, `max_tokens`, or a per-task default), and whether the request needs
tools, vision or JSON output. The task and complexity headers override the detected values and
are recorded as signals. Details in [routing.md](routing.md#the-analyzer). Redis: none.

### 4. Health snapshot and ranking (`GatewayService.plan`, `routing/engine.py rank`)

`HealthTracker.snapshot` reads every model's breaker hash, latency ring and five per-minute
buckets in one non-transactional pipeline (`HGETALL`, `LRANGE`, `HGETALL` x5 per model). A
breaker stored as `open` whose cooldown has passed is reported as `half_open`. `rank` then
applies the hard filters, the quality floor and the weighted score, and `baseline_for` picks
the model savings are measured against. The quality lookup is `Catalog.expected_quality`,
trusted to the analyzer's band only when the caller set the complexity. If no candidate is
eligible the gateway raises `422 no_eligible_model` with every candidate's reason in
`details` and records a `REJECTED` decision. The time spent in steps 2 to 4 is `routing_us`.
Details in [routing.md](routing.md#the-ranking-engine).

### 5. Admit (`GatewayService._admit`, `limits/limiter.py`)

Per gateway key, only when the key has the limit set:

| Limit | Redis | Refusal |
| ----- | ----- | ------- |
| RPM | `EVALSHA` sliding-window script over `sr:rl:rpm:<key>:<minute>` and the previous minute: `GET`, `GET`, `INCRBY`, `EXPIRE` | `429 rpm_limit`, `Retry-After` to the end of the minute |
| Concurrency | `EVALSHA` acquire on `sr:conc:key:<key>`: `INCR`, `EXPIRE`, `DECR` when over the limit | `429 concurrency_limit`, `Retry-After: 1` |

A `RedisError` here skips the limit: routing does not hold money, so limits fail open. The
concurrency slot is released when a non-streaming request returns, or when a stream ends.

### 6. Try candidates (`GatewayService._call`)

The attempt order is the eligible candidates in rank order, cut to `max_attempts` when the
policy allows fallback and to one candidate when it does not. For each candidate:

1. **Budget ceiling.** After a TokenShield refusal, a candidate whose estimated cost is not
   strictly below the refused one is skipped (`skipped`, "not cheaper than the
   budget-refused model").
2. **Health gate** (`_health_gate`). The ranking's health snapshot may be stale by the time
   a candidate is reached (an earlier attempt failed, another replica's traffic or the worker's
   probe opened a circuit), so health is read again for this one model. If it is `half_open`,
   `SET sr:cbp:<alias> 1 NX EX <cooldown>`; a request that does not win the lock skips the model
   (`skipped`, "half-open: probe already in flight"). If it is `open` and the request is
   routed, the model is skipped (`skipped`, "circuit opened after ranking"). A pinned model is
   still called when open: the caller chose it.
3. **Call the adapter** with the body (`model` set to the alias, `stream_options` removed,
   `benchmark_*` metadata kept only for simulated endpoints), the upstream model name, the
   opened endpoint key and the endpoint's timeout (or `UPSTREAM_TIMEOUT_S`).
4. **Classify** a failure (`_classify`):

   | Failure | Outcome | Retryable |
   | ------- | ------- | --------- |
   | Connect error, connect timeout, pool timeout (`UpstreamNotSentError`) | `error` | yes |
   | Read or write timeout, dropped connection, protocol error (`UpstreamUncertainError`) | `timeout` | yes |
   | `402` with code `budget_exceeded`, or a `402` carrying `x-tokenshield-request-id` | `budget` | only on cheaper candidates, only with `budget_downgrade` |
   | `429` | `throttled` | yes |
   | `5xx` | `error` | yes |
   | Any other `4xx` | `client` | no: returned to the caller |

5. **Record health** off the response path for every outcome except `client` (a `budget`
   refusal is recorded as `ok`: the model is healthy, the budget is not).
6. **Fall back or stop.** A `budget` outcome stops the loop unless `budget_downgrade` is on, in
   which case it sets the budget ceiling and continues. Any other outcome stops the loop when it
   is not retryable or the policy disallows fallback, and continues otherwise.

### 7. Success (`_success`)

If the provider reported no usage, the gateway uses the input estimate and counts the
completion text, and marks the usage as an estimate. It sets the response body's `model` to the
alias, builds the `DecisionRecord` (actual cost from `x-tokenshield-cost-usd` when present,
else computed from usage and the model's price; baseline cost is the same usage at the baseline
model's price), appends it to the decision stream, schedules the health record, updates the
Prometheus counters, and returns the provider's body with the headers listed
[below](#response-headers).

### 8. Exhaustion (`_exhausted`)

| Last failure | Error returned |
| ------------ | -------------- |
| A `client` 4xx or a `budget` 402 | The upstream status and code (for example `402 budget_exceeded`), with the upstream `error` object and the attempts in `details` |
| A single attempt (others skipped or none left) answered `429` | `429 upstream_rate_limited`, `Retry-After` copied from the upstream (`1` when absent), the attempt in `details` |
| A single attempt that timed out | `504 upstream_timeout` |
| Anything else (several failures, a 5xx, or every candidate skipped) | `502 all_models_failed`, naming the models tried and whether fallback was disabled |

The failure is recorded as an `ERROR` decision with the error code and HTTP status.

When a failure in this loop leaves a circuit open, `_health` calls
`alerts/service.py circuit_opened(..., source="traffic")`, which logs one
`event=circuit_opened` line and inserts an alert deduplicated per model per hour. The worker's
probe calls the same function with `source="probe"` ([health-and-fallback.md](health-and-fallback.md#alerts-and-the-circuit_opened-event)).

## Streaming

`_stream` runs the same candidate loop, but each attempt opens the adapter's SSE generator and
waits for the **first chunk**. A failure before it arrives (including an empty stream, treated
as an uncertain failure) is classified and may fall back exactly like a non-streaming call.
Once the first chunk arrives the gateway commits to that model and returns a
`StreamingResponse` that relays the rest.

When the relay ends, for any reason, a finish task runs under `asyncio.shield`, because
Starlette cancels the generator when the client disconnects and the decision must still be
recorded:

| Ending | Recorded |
| ------ | -------- |
| Stream completed | `SUCCESS` with provider usage from the final chunk (the OpenAI adapter sets `stream_options.include_usage`), or an estimate from the relayed text |
| Upstream failed after chunks were sent | The client gets an SSE error event (`upstream_error`) and `[DONE]`; decision `ERROR` with `stream_interrupted`; health `error` |
| Client disconnected mid-stream | Decision `ERROR` with `client_disconnected`; health `ok` (the model was answering) |

Streaming responses carry the routing headers and `x-smartrouter-estimated-cost-usd` but not
`x-smartrouter-cost-usd`, because headers are sent before the usage is known; the actual cost
is on the decision row. (A TokenShield endpoint that sends `x-tokenshield-cost-usd` on the
stream's opening response is the exception: that value is used.)

## Response headers

Set by `GatewayService._headers` on every successful response:

| Header | Value |
| ------ | ----- |
| `x-smartrouter-request-id` | The decision's request id |
| `x-smartrouter-model` | The model that answered |
| `x-smartrouter-policy` | The policy name, or `passthrough` |
| `x-smartrouter-strategy` | `cost`, `latency`, `quality`, `balanced`, or `passthrough` |
| `x-smartrouter-task` | Detected (or caller-set) task |
| `x-smartrouter-complexity` | 1 to 5 |
| `x-smartrouter-attempts` | Attempts made (skipped candidates not counted), at least 1 |
| `x-smartrouter-estimated-cost-usd` | The selected candidate's pre-call estimate |
| `x-smartrouter-cost-usd` | Actual cost (not on streams) |
| `x-smartrouter-baseline-model` | The model savings are measured against |
| `x-smartrouter-baseline-cost-usd` | Actual tokens at the baseline price (not on streams) |
| `x-smartrouter-fallbacks` | Comma-separated models that failed before the answer, when any did |
| `x-smartrouter-reason` | The selection reason, ASCII, at most 180 characters |

CORS exposes all of them, plus `x-request-id` and `retry-after`, to browser clients.

## Error codes

Every error uses the `{"error": {code, type, message, details}}` envelope.

| Status | Code | When |
| ------ | ---- | ---- |
| 400 | `body_too_large`, `invalid_json`, `invalid_request` | Body over 8 MiB, not a JSON object, or `messages` missing or empty |
| 401 | `invalid_api_key`, `api_key_revoked`, `api_key_expired` | Gateway key missing, malformed, wrong, revoked or expired |
| 403 | `project_unavailable` | The key's project is archived or missing |
| 404 | `model_not_found`, `policy_not_found` | Unknown model alias; unknown policy or no default policy |
| 422 | `no_eligible_model` | Nothing survives the filters (or the policy rejects when nothing clears the floor) |
| 422 | `validation_error` | Schema validation (for example `/v1/feedback`) |
| 429 | `rpm_limit`, `concurrency_limit` | Per-key limits (also `rpm_limit` on `/v1/feedback`) |
| 429 | `upstream_rate_limited` | The only attempt was rate limited upstream; `Retry-After` is the upstream's |
| 404 | `not_found` | `/v1/feedback` with a request id SmartRouter did not route |
| upstream | upstream code | A non-retryable upstream 4xx, or a TokenShield `402 budget_exceeded` that was not downgraded |
| 502 | `all_models_failed` | Every attempt failed or was skipped |
| 504 | `upstream_timeout` | The only attempt timed out |
| 400 | `invalid_score`, `not_your_request` | `/v1/feedback` with a score other than 1 or -1, or a request id owned by another project |
| 500 | `internal_error` | Unhandled exception (logged with the request id, no detail returned) |

## The decision stream

`decisions/recorder.py` writes one `DecisionRecord` per gateway request, success or failure
([ADR-0004](adr/0004-decisions-through-a-redis-stream.md)):

1. **Append.** `XADD sr:decisions:stream MAXLEN ~ 1000000 * r <orjson record>`, pipelined
   (non-transactional) with `SET sr:req:<request_id> <project_id> EX 86400`, the marker
   [feedback](#feedback) reads before the row exists. One round trip on the request path.
2. **Fallback.** If Redis raises, the record is inserted straight into `routing_decisions`
   (logged as `decision stream unavailable; writing directly`). Proven by
   `test_decisions_are_written_directly_when_the_stream_is_unavailable`.
3. **Drain.** Every worker runs `XGROUP CREATE ... recorders 0 MKSTREAM` (ignoring
   `BUSYGROUP`), then loops `XREADGROUP GROUP recorders <host-pid> COUNT 500 BLOCK 1000`.
4. **Commit.** The batch is inserted in one statement with
   `INSERT ... ON CONFLICT (request_id) DO NOTHING`, then `XACK` and `XDEL` in one pipeline. An
   entry is acknowledged only after its row is committed.
5. **Reclaim.** Every 30 s a worker runs `XAUTOCLAIM sr:decisions:stream recorders <self> 60000
   0-0 COUNT 500` and commits what it claims: entries a crashed consumer read but never
   acknowledged. Because inserts ignore duplicate request ids, a redelivered entry never
   produces a second row. Proven by `test_a_crashed_consumer_is_reclaimed_without_duplicates`.

`routing_decisions` is append-only (a trigger refuses `UPDATE` and `DELETE`), so the log is
also tamper evident for anyone without superuser rights.

## Feedback

`POST /v1/feedback` (`gateway/router.py feedback`):

1. `score` must be `1` or `-1` (`400 invalid_score`).
2. With a gateway key, `Limiter.hit("feedback:<key_id>", rpm_limit or 600)`; over the limit is
   `429 rpm_limit` with `Retry-After`. Unlike the gateway's own limits this one does not fail
   open: a `RedisError` here becomes `500 internal_error`.
3. Look up the owner: `SELECT project_id FROM routing_decisions WHERE request_id = ...`, else
   `GET sr:req:<request_id>` (written with the decision `XADD`, 24 hours). Neither:
   `404 not_found`, so feedback can only attach to a request SmartRouter routed, and still works
   before the worker drains the stream.
4. A key whose project differs from the owner is `400 not_your_request`.
5. Insert the `decision_feedback` row.

Proven by `test_feedback_needs_a_real_request_and_works_before_the_drain` and
`test_feedback_is_rate_limited_per_key`.

## Provider adapters

`providers/adapters/base.py` defines the `ProviderAdapter` protocol: `chat`, `chat_stream` and
`embeddings`, all speaking the OpenAI shape on both sides. The registry
(`providers/adapters/registry.py`) maps an endpoint kind to an adapter.

| Kind | Adapter | Notes |
| ---- | ------- | ----- |
| `openai` | `openai.py` | Any OpenAI-compatible base URL (OpenAI, Ollama, vLLM, LM Studio, Groq, Together, proxies). Reads `prompt_tokens_details.cached_tokens` |
| `anthropic` | `anthropic.py` | Translates to the Messages API (`anthropic-version: 2023-06-01`, `x-api-key`), maps stop reasons, tool calls and images |
| `tokenshield` | `openai.py` | A TokenShield gateway speaks the OpenAI wire format; the gateway service reads its cost header and reacts to its 402 |

`http.py` holds the shared client (at most 500 connections, 100 keep-alive, no redirects,
HTTP/1.1), the failure classification, and `kept_headers`, which keeps `x-request-id`,
`retry-after` (passed on with `429 upstream_rate_limited`) and every `x-tokenshield-*` response
header. The connect timeout is `min(5 s, timeout)`. There is no
retry inside an adapter: retrying is the gateway's decision, made per candidate.

The mock upstream (`apps/api/src/mockllm/app.py`) speaks the OpenAI and Anthropic APIs plus a
TokenShield-style route that adds `x-tokenshield-cost-usd` and `x-tokenshield-request-id`. Each
mock model has a skill per task and a latency profile; faults (`error`, `timeout`, `throttle`,
`budget`) are injected through `/admin/faults`, and `GET /stats` returns call counts the tests
use to prove which model was called.

## The worker

`smartrouter worker` (`worker.py`) runs two loops on every instance plus two leased jobs. Run
as many workers as you like: the decision stream is shared through the consumer group, the
benchmark queue is a list each job is popped from once, and each periodic job takes
`SET sr:worker:lease:<job> 1 NX EX <interval - 1>` so exactly one instance runs it per
interval.

| Job | Interval | What it does |
| --- | -------- | ------------ |
| `decision_drain` | continuous, every worker | [The decision stream](#the-decision-stream): read, insert, ack, delete; reclaim idle entries every 30 s |
| `benchmarks` | continuous, every worker | `BLPOP sr:bench:queue 2`; runs the popped benchmark with `BenchmarkRunner.run` ([benchmarks.md](benchmarks.md)). At most once a minute, before polling, `fail_stale_runs` marks every run still `running` with `started_at` older than `BENCHMARK_STALE_AFTER_S` (7200 s) as `failed` ("The worker running this run stopped before it finished"). The update is idempotent, so every worker running it is harmless |
| `rollups` | 60 s, leased | Recompute every closed hour from two hours before the latest rolled hour (or from the first decision) up to the open hour, one idempotent `INSERT ... ON CONFLICT DO UPDATE` per hour into `decision_rollups_hourly`; benchmark decisions are excluded |
| `health_probe` | `HEALTH_PROBE_INTERVAL_S` (15 s), leased | `GET <base>/models` (Anthropic: `/v1/models`) per endpoint with a 5 s timeout; a failure (transport error, 5xx, 401, 403) records `error` against every model the endpoint serves, and calls `circuit_opened(..., source="probe")` when that leaves a circuit open; a success records `ok` only for models whose circuit is half-open, which closes them ([health-and-fallback.md](health-and-fallback.md#the-worker-probe)) |

On `SIGTERM` or `SIGINT` the worker stops after the current iteration. A failed job is logged
and retried at its next interval; a failed benchmark run is marked `failed` with its error.
`POST /api/v1/benchmarks/runs` creates the run, then pushes it; if the push raises a
`RedisError`, the run is marked `failed` ("Could not queue the run: Redis is unavailable") and
the API answers `503 dependency_unavailable`, so no run is left `queued` with nothing to pick
it up.

## Redis key reference

| Key | Type | Written by | TTL |
| --- | ---- | ---------- | --- |
| `sr:cfg:version` | string counter | Admin writes (`ConfigCache.invalidate`) | none |
| `sr:keyused:<key_id>` | string | Gateway auth | 60 s |
| `sr:rl:rpm:<key_id>:<minute>` | string counter | Limiter | 120 s |
| `sr:rl:feedback:<key_id>:<minute>` | string counter | Limiter (`/v1/feedback`) | 120 s |
| `sr:rl:login:<ip>:<minute>` | string counter | Sign-in | 120 s |
| `sr:conc:key:<key_id>` | string counter | Limiter | `2 x UPSTREAM_TIMEOUT_S + 30` s, refreshed on acquire |
| `sr:h:<alias>:<minute>` | hash: outcome to count | Health record script | 360 s |
| `sr:hl:<alias>` | list of latencies (ms) | Health record script | none, trimmed to 200 |
| `sr:cb:<alias>` | hash: `state`, `consecutive`, `opened_at`, `changed_at` | Health record script | none |
| `sr:cbp:<alias>` | string (half-open probe lock) | Health gate | `BREAKER_COOLDOWN_S` |
| `sr:decisions:stream` | stream, group `recorders` | Gateway | entries deleted after commit; capped near 1,000,000 |
| `sr:req:<request_id>` | string: project id (empty without a project) | Gateway, with the `XADD` | 86,400 s |
| `sr:bench:queue` | list | `POST /api/v1/benchmarks/runs` | until popped |
| `sr:an:<query>` | string | Analytics | 10 s |
| `sr:worker:lease:<job>` | string | Worker | job interval minus 1 s |

Every key starts with `sr:`. TokenShield's keys start with `ts:`, so the two products can
share one Redis database without invalidating each other's config caches or sharing
rate-limit windows. A separate Redis per product is still the default in the Terraform and
Compose.

## Timeouts, limits and constants

| Constant | Value | Where |
| -------- | ----- | ----- |
| Request body limit | 8 MiB | `gateway/router.py MAX_BODY_BYTES` |
| Header value length | 120 characters | `gateway/dependencies.py _MAX` |
| Config cache TTL, version check | 5 s, 1 s | `core/cache.py` |
| Upstream timeout | 60 s (per endpoint override up to 600 s) | `UPSTREAM_TIMEOUT_S`, `providers.timeout_s` |
| Upstream connect timeout | `min(5 s, timeout)` | `providers/adapters/http.py` |
| Max attempts | 3 by default, 1 to 6 per policy | `routing_policies.max_attempts` |
| Breaker: consecutive failures, error rate, min samples, cooldown | 5, 0.5, 10, 30 s | `BREAKER_FAILURE_THRESHOLD`, `BREAKER_ERROR_RATE`, `BREAKER_MIN_SAMPLES`, `BREAKER_COOLDOWN_S` |
| Health window, latency ring | 5 one-minute buckets, 200 samples | `health/tracker.py WINDOW_MIN`, `LATENCY_SAMPLES` |
| Measured latency replaces the prior after | 5 samples | `routing/engine.py expected_latency` |
| Health probe interval, probe timeout | 15 s, 5 s | `HEALTH_PROBE_INTERVAL_S`, `worker.py` |
| Decision stream cap, batch, reclaim | about 1,000,000 entries; 500; idle 60 s, every 30 s | `decisions/recorder.py`, `worker.py` |
| Analytics cache | 10 s | `decisions/analytics.py CACHE_TTL_S` |
| Benchmark concurrency, folds, answer excerpt | 16 calls, 2 folds, 400 characters | `benchmarks/runner.py` |
| Stale benchmark run, sweep interval | 7200 s, 60 s | `BENCHMARK_STALE_AFTER_S`, `worker.py` |
| Benchmark results file retention | 365 days | `BENCHMARK_RESULTS_DAYS`, storage module `expiration_days` |
| Feedback marker, feedback rate | 24 h; the key's RPM, else 600 per minute | `decisions/recorder.py REQUEST_TTL_S`, `gateway/router.py FEEDBACK_RPM` |
| Sign-in throttle, lockout | 20 per minute per IP; 5 failures, 15 minutes | `auth/router.py`, `auth/service.py` |
| Session lifetime | 12 h | `SESSION_TTL_HOURS` |
| Presigned results link | 300 s | `core/storage.py` |
| Readiness probe timeout | 2 s per dependency | `system/router.py` |

## Concurrency and idempotency guarantees

| Concern | Mechanism | Proven by |
| ------- | --------- | --------- |
| Replicas disagreeing about a model's health | Health and breaker state live only in Redis; one Lua script updates counters, latencies and the breaker atomically | `test_open_circuit_is_never_chosen`, `test_breaker_recovers_through_a_half_open_probe` |
| Many requests probing a recovering model at once | `SET NX` probe lock with the cooldown as TTL, taken at call time; losers skip to the next candidate | `test_only_one_request_probes_a_half_open_circuit` |
| Two concurrent "make default" policy updates | Transaction-scoped advisory lock (`pg_advisory_xact_lock`), clear the old default before setting the new one, and the partial unique index `uq_routing_policies_single_default` (migration `0002`) | `test_one_default_policy_even_under_concurrent_updates` |
| A benchmark run stuck after a worker died, or never queued | Stale sweep in the worker; a failed queue push marks the run failed | `test_a_run_abandoned_by_a_dead_worker_is_marked_failed`, `test_a_queue_outage_never_leaves_a_queued_run_behind` |
| A worker crashing mid-batch | Entries stay pending until acked; `XAUTOCLAIM` hands them to a live worker | `test_a_crashed_consumer_is_reclaimed_without_duplicates` |
| Redelivered decisions double counting | Unique `request_id`, `ON CONFLICT DO NOTHING` | same |
| Redis down while recording | Direct insert into Postgres | `test_decisions_are_written_directly_when_the_stream_is_unavailable` |
| Concurrent requests overshooting a key's RPM | One sliding-window Lua script per hit | `test_key_rpm_limit` |
| Two workers running the same sweep | Redis lease per job | the lease in `Worker.run_job` |
| Decision rows being edited after the fact | `BEFORE UPDATE OR DELETE` triggers on `routing_decisions`, `decision_feedback`, `audit_log` | the trigger in migration `0001` |
