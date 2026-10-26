# Operations

## Processes

| Process | Command | Scale |
| --- | --- | --- |
| API and gateway | `smartrouter serve` | Stateless; run as many replicas as traffic needs. Model health, circuits and limits live in Redis, so every replica sees the same open circuits. Each keeps a short in-process config cache, invalidated on every admin change |
| Worker | `smartrouter worker` | Run at least one. Every worker drains the decision stream (consumer group); periodic jobs take a Redis lease so one instance runs each per interval |
| Dashboard | Next.js standalone server | Stateless |
| Migrations | `smartrouter migrate` | One-off before each release (ECS: the `migrate` task; `make aws-deploy` runs it, `make aws-migrate` runs it alone) |

Other CLI commands: `smartrouter benchmark [--models a,b] [--policies x,y]`,
`smartrouter create-owner --email <email> [--name <name>]` (password from
`SMARTROUTER_OWNER_PASSWORD` or a prompt; also resets an existing account to owner),
`smartrouter seed-demo [--days 60] [--traffic 120] [--api-url <url>]` (never in production)
and `smartrouter openapi`. `make admin EMAIL=<address>` runs `create-owner` in the local
stack; `make aws-admin ENV=<env> EMAIL=<address>` runs it as a one-off ECS task and prints a
one-time password ([deploy-aws.md](deploy-aws.md#steps)).

### Worker jobs

| Job | Interval | Purpose |
| --- | --- | --- |
| `decision_drain` | continuous | Write buffered routing decisions from the Redis stream `sr:decisions:stream` to Postgres in batches of up to 500; every 30 s, reclaim entries a crashed worker left pending for over a minute |
| `rollups` | 60 s | Aggregate closed hours into `decision_rollups_hourly` for the Overview and Projects pages. Benchmark traffic is excluded |
| `health_probe` | `HEALTH_PROBE_INTERVAL_S` (15 s) | Call each endpoint's model listing. A failure counts against every model it serves; a success closes a half-open circuit |
| `benchmarks` | continuous | Run benchmark runs queued from the dashboard (Redis list `sr:bench:queue`). Once a minute, mark runs still `running` after `BENCHMARK_STALE_AFTER_S` (2 hours) as failed: their worker stopped before it finished |

If no worker runs, routing still works and decisions are buffered in Redis, but the decision
log and analytics stop updating, queued benchmarks stay queued, and endpoint probes stop. If
Redis rejects a decision, the API writes it straight to Postgres. If Redis is down when a
benchmark is started, the run is marked failed at once and the API answers
`503 dependency_unavailable`, so no run sits in `queued` forever.

SmartRouter's Redis keys all start with `sr:`, so it can share a Redis database with
TokenShield (whose keys start with `ts:`) without either invalidating the other's caches or
sharing rate-limit windows.

## Health

| Endpoint | Meaning |
| --- | --- |
| `GET /healthz` | Liveness: the process answers. Used by the ALB and container health checks |
| `GET /readyz` | Readiness: Postgres and Redis answer within 2 s. 503 with `"status": "degraded"` and per-dependency detail otherwise |
| `GET /api/v1/system` | Deployment configuration and live checks (dashboard Settings page) |
| `GET /api/v1/health/models` | Live health of every model: circuit state, p50 and p95 latency, error rate, samples and outcome counts over the last 5 minutes (dashboard Models page) |

## Metrics

`GET /metrics` exposes Prometheus metrics. Labels are bounded sets (policies, model aliases,
tasks, statuses), never per-request values.

| Metric | Type | Labels | Use |
| --- | --- | --- | --- |
| `smartrouter_decisions_total` | counter | `policy`, `model`, `task`, `status` | Traffic, model mix and outcomes (`SUCCESS`, `ERROR`, `REJECTED`). Pinned models count under `passthrough` |
| `smartrouter_fallbacks_total` | counter | `policy` | Requests that needed more than one attempt |
| `smartrouter_savings_usd_total` | counter | `policy` | Cost saved against each request's baseline model |
| `smartrouter_routing_seconds` | histogram | | Time spent analysing and ranking (excludes the upstream call) |

Useful alerts: the fallback rate (`rate(smartrouter_fallbacks_total[5m])` over
`rate(smartrouter_decisions_total[5m])`), any rise in `status="ERROR"` or `"REJECTED"`, p95
of `smartrouter_routing_seconds`, and the length of the Redis stream `sr:decisions:stream` (a
growing backlog means no worker is draining).

Logs are JSON lines with a `request_id` (also returned as `x-request-id`). Each failure that
leaves a model's circuit open logs one line with `"event": "circuit_opened"`, the `model`, and
the `source`: `traffic` from the api (a gateway call), `probe` from the worker's health probe. Traces: set
`OTEL_ENABLED=true` and the standard `OTEL_EXPORTER_OTLP_ENDPOINT`.

### CloudWatch

The `observability` module (AWS environments) creates log groups
`/smartrouter/<name_prefix>/{api,worker,web,migrate}`, a dashboard and alarms to one SNS
topic (optionally e-mailed with `alarm_email`):

- 5xx from each load-balanced app and from the load balancer;
- api p95 time to first byte above 20 s (non-streaming completions include the upstream
  call);
- unhealthy targets, CPU and memory per ECS service;
- database CPU, free storage and connections;
- cache memory per node (the cache runs `noeviction`, so writes fail when it is full) and
  engine CPU;
- **CircuitOpened**, optional: a metric filter turns the api's `circuit_opened` log lines
  into `SmartRouter/<name_prefix>/CircuitOpened`, and setting `circuit_open_threshold` alarms
  when failures on open circuits exceed it in 5 minutes (a provider is down and traffic is
  falling back). The filter reads the api log group only, so circuits opened by the worker's
  probe (`"source": "probe"`) reach the dashboard bell but not this metric.

A circuit opening also raises a critical alert in the dashboard bell, at most once per
model per hour, whether gateway traffic or the worker's probe opened it.

## Routine tasks

### Reset a circuit

A circuit closes on its own: after `BREAKER_COOLDOWN_S` it lets one probe request through,
and the worker's health probe also closes a half-open circuit when the endpoint answers. To
close it immediately after fixing a provider: **Models**, the model's menu, **Close circuit
now** (admins), or `POST /api/v1/health/models/<alias>/reset`. The reset is audited as
`health.reset`. If the provider is still failing, the next failures open it again.

### Re-run benchmarks after a catalog change

The router ranks by the quality table, which only a benchmark run fills. A new model has no
measured scores, so it is ranked by its tier prior until the next run. After adding a model,
changing an upstream model, or changing prices, open **Benchmarks** and **Run benchmark**
(admins; the worker runs it), or from the stack:

```bash
make benchmark
```

The run replaces benchmark scores (and manual overrides) in the quality table, and the
router uses them as soon as it finishes. On real providers a run sends the whole suite to
every model and policy and costs money; limit it with `--models` or `--policies`.

### Rotate keys

- **Endpoint (provider) key:** Endpoints, edit, paste the new key. It is sealed and live
  within moments; audited as `provider.key_rotated`. Each process drops the old plaintext
  from its in-memory key cache when it loads the new one. Moving an endpoint to another host
  also needs its key in the same request (`api_key_required`), so a stored credential is
  never sent to a new host.
- **Gateway key:** create a new key for the project, deploy it to the application, then
  revoke the old one. Revocation is immediate.
- **`SESSION_SECRET`:** signs everyone out of the dashboard.
- **`API_KEY_PEPPER`:** invalidates every `tsr_` key. Issue new keys first.
- **`LOCAL_MASTER_KEY`** (with `KEY_MODE=local`): stored endpoint keys can no longer be
  opened; re-enter them afterwards. With `KEY_MODE=kms`, KMS rotates the key material itself.

### Change routing safely

Try a policy change in the **Simulator** first. Then watch the **Decisions** page for the
new choices and **Benchmarks** for the quality and cost trade-off.

## Backups

Postgres holds everything durable: configuration, policies, the quality table, decisions,
benchmark results and the audit log. RDS automated backups cover it (3 days in `aws-dev`,
14 in `aws-prod`). Redis holds model health and circuit state (rebuilt from live traffic),
rate-limit counters, the benchmark queue and the decision stream (drained within moments).
Run Redis with AOF so a restart does not lose the stream backlog; Compose does. Benchmark
result files expire after `BENCHMARK_RESULTS_DAYS` (365); the S3 lifecycle rule
(`expiration_days` in the `storage` module, also 365) deletes them, and the API answers 404
"expired" for an older run. Keep the two values equal. The run summaries stay in Postgres.
