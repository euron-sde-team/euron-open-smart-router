# Configuration reference

Every setting is an environment variable, read once at boot by `core/config.py` (pydantic
settings, case-insensitive). Routing behaviour itself (models, prices, policies, keys) lives
in the database and is changed in the dashboard or the admin API, not here.

## Production boot guards

With `ENVIRONMENT=production` the API and worker refuse to start when:

- `SESSION_SECRET` or `API_KEY_PEPPER` is a placeholder, contains `not-for-production`, or is
  shorter than 32 characters;
- `SESSION_SECRET` and `API_KEY_PEPPER` are the same value;
- `KEY_MODE=local` and `LOCAL_MASTER_KEY` is the development default or shorter than 32
  characters (use `KEY_MODE=kms` instead);
- `SMARTROUTER_CLOUD=aws` and `AWS_ENDPOINT_URL` is set.

`seed-demo` also refuses to run in production.

## Runtime

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development`, `test` or `production`. Production enables the boot guards and blocks the demo seed |
| `SMARTROUTER_CLOUD` | `local` | `local`, `floci` or `aws`. On `aws` (in production), an `AWS_ENDPOINT_URL` override is refused |
| `LOG_LEVEL` | `INFO` | JSON log level |

`smartrouter serve` also reads `PORT` (default `4230`; Compose uses `8080` inside the
container) and `WEB_CONCURRENCY` (default `2` Uvicorn worker processes) as defaults for its
`--port` and `--workers` flags.

## Data stores

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://smartrouter:smartrouter@localhost:4242/smartrouter` | Postgres 16: configuration, decisions, rollups, benchmark results, quality table, audit log |
| `REDIS_URL` | `redis://localhost:4279/0` | Redis 7 or Valkey: model health and circuit breakers, the decision stream, rate limits, the benchmark queue, worker leases. Run with AOF and `noeviction` |

## Security

| Variable | Default | Meaning |
| --- | --- | --- |
| `SESSION_SECRET` | development value | Signs dashboard sessions. 32+ random characters in production |
| `API_KEY_PEPPER` | development value | HMAC pepper for gateway key hashes. 32+ random characters, different from `SESSION_SECRET`. Changing it invalidates every `tsr_` key |
| `SESSION_TTL_HOURS` | `12` | Dashboard session lifetime |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `CORS_ORIGINS` | `http://localhost:4210` | Comma-separated dashboard origins |
| `KEY_MODE` | `local` | How endpoint API keys are sealed at rest: `local` (AES-GCM with `LOCAL_MASTER_KEY`) or `kms` |
| `LOCAL_MASTER_KEY` | development value | Key material for `KEY_MODE=local`. 32+ characters in production |
| `KMS_KEY_ID` | `alias/smartrouter-local` | KMS key or alias for `KEY_MODE=kms` |
| `TRUSTED_PROXY_HOPS` | `0` | Reverse proxies in front of the API that append `X-Forwarded-For` (0 to 5). `0`: the direct peer is the client. With `N`, the client is the Nth `X-Forwarded-For` entry from the right; entries further left are written by the client and ignored. Used for the sign-in throttle and the audit log. Compose sets `1` (the dashboard's Next.js proxy), the AWS environments set `1` (the ALB) |
| `ENDPOINT_ALLOW_PRIVATE` | `true` | Allow endpoint base URLs that resolve to loopback or private addresses (local Ollama, vLLM inside the VPC). Link-local and cloud metadata addresses (`169.254.0.0/16`, `fe80::/10`, `fd00:ec2::254`), unspecified, multicast and reserved addresses are refused whatever this says. See [security.md](../architecture/security.md#outbound-requests-and-endpoint-urls) |

## AWS and storage

Object storage holds the per-case benchmark results files (gzipped JSON lines).

| Variable | Default | Meaning |
| --- | --- | --- |
| `AWS_REGION` | `us-east-1` | Region for S3 and KMS |
| `AWS_ENDPOINT_URL` | unset | Emulator endpoint (Floci). Must be unset on real AWS |
| `S3_FORCE_PATH_STYLE` | `false` | Path-style S3 addressing (needed for Floci) |
| `S3_PUBLIC_ENDPOINT` | unset | Host used in presigned download links when the browser reaches S3 at a different address than the API (Compose: `http://localhost:4266`) |
| `STORAGE` | `local` | `local` (a directory) or `s3` |
| `EXPORTS_BUCKET` | `smartrouter-local-exports` | S3 bucket for `STORAGE=s3` |
| `LOCAL_STORAGE_DIR` | `./.data/exports` | Directory for `STORAGE=local` (`/data/exports` in Compose, shared by api and worker) |
| `BENCHMARK_RESULTS_DAYS` | `365` | How long a run's results file is kept. Downloads of an older run answer 404 "expired". Keep it equal to the `storage` module's `expiration_days` (also 365), which deletes the files from S3 |

## Gateway behaviour

| Variable | Default | Meaning |
| --- | --- | --- |
| `UPSTREAM_TIMEOUT_S` | `60` | Upstream timeout when the endpoint sets none of its own (endpoints can set a per-endpoint timeout) |
| `DEFAULT_MAX_OUTPUT_TOKENS` | `1024` | Output allowance when a request sets no `max_tokens` |

## Health and circuit breakers

A model's circuit opens after `BREAKER_FAILURE_THRESHOLD` consecutive failures, or when its
error rate over the 5-minute window exceeds `BREAKER_ERROR_RATE` with at least
`BREAKER_MIN_SAMPLES` outcomes. An open model is skipped. After `BREAKER_COOLDOWN_S` it turns
half-open and gets one probe request: success closes it, failure reopens it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive failures that open a circuit |
| `BREAKER_ERROR_RATE` | `0.5` | Error rate over the window that opens a circuit |
| `BREAKER_MIN_SAMPLES` | `10` | Outcomes needed in the window before the error rate counts |
| `BREAKER_COOLDOWN_S` | `30` | Seconds a circuit stays open before a probe is allowed |
| `HEALTH_PROBE_INTERVAL_S` | `15` | How often the worker checks each endpoint's model listing |

## Benchmarks

| Variable | Default | Meaning |
| --- | --- | --- |
| `BENCHMARK_STALE_AFTER_S` | `7200` | A run still `running` after this many seconds lost its worker (a crash or a redeploy). The worker checks once a minute and marks such runs failed ("stopped before it finished") |
| `BENCHMARK_RESULTS_DAYS` | `365` | See [AWS and storage](#aws-and-storage) |

## Observability

| Variable | Default | Meaning |
| --- | --- | --- |
| `OTEL_ENABLED` | `false` | Export OpenTelemetry traces over OTLP/HTTP |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel default | Collector endpoint (standard OTel variable) |
| `OTEL_SERVICE_NAME` | OTel default | Standard OTel variable; the worker identifies itself as `smartrouter-worker` |

## Demo and tools

These are read by the CLI and scripts, not by `Settings`.

| Variable | Used by | Meaning |
| --- | --- | --- |
| `DEMO_PASSWORD` | `seed-demo`, `make bench` | Password for the demo accounts (default `RouterDemo2026`) |
| `MOCK_LLM_URL` | `seed-demo`, bench | Where demo endpoints point (`http://mock-llm:4250` in Compose) |
| `SMARTROUTER_API_URL` | `seed-demo`, bench | API base URL for live demo traffic (`http://api:8080` in Compose) |
| `SMARTROUTER_PUBLIC_URL` | `seed-demo` | URL printed in the "try it" command |
| `SMARTROUTER_OWNER_PASSWORD` | `create-owner`, `make admin` | Password, instead of an interactive prompt. `make aws-admin` generates one and passes it to the task |
| `BENCH_EMAIL`, `BENCH_REQUESTS`, `BENCH_CONCURRENCY` | `make bench` | Account, request count (400) and concurrency (1: sequential requests, so the numbers measure overhead rather than queueing) |
| `E2E_PASSWORD`, `WEB_URL` | `make e2e` | Set by the Makefile from `DEMO_PASSWORD`; dashboard URL (default `http://localhost:4210`) |
| `QA_PASSWORD`, `QA_BASE`, `QA_PAGES`, `QA_THEMES`, `QA_SIZES` | `make qa` | Sign-in password, base URL and which pages, themes and sizes to sweep |
| `TEST_DATABASE_URL`, `TEST_REDIS_URL`, `ALLOW_REMOTE_TEST_DB` | integration tests | See [testing.md](testing.md#integration-tests) |

## Make targets

`make` with no target lists every target. The ones that take settings:

| Target | Settings | What it does |
| --- | --- | --- |
| `make admin` | `EMAIL` (required), `SMARTROUTER_OWNER_PASSWORD` (optional) | Creates or resets a dashboard owner in the local stack. Prompts for the password unless `SMARTROUTER_OWNER_PASSWORD` is set |
| `make bench` | `BENCH_REQUESTS`, `BENCH_CONCURRENCY` | Measures gateway overhead and throughput against the running stack, sequential by default |
| `make aws-plan` | `ENV` (default `aws-dev`), `TAG` | `terraform plan` for that environment |
| `make aws-deploy` | `ENV` (default `aws-dev`), `TAG` | Registries, `linux/arm64` images, `terraform apply`, migrations as a one-off task, then waits for the api, worker and web services. See [deploy-aws.md](deploy-aws.md#steps) |
| `make aws-migrate` | `ENV` | Runs the migrations as a one-off ECS task |
| `make aws-admin` | `ENV`, `EMAIL` (required) | Creates the first dashboard owner as a one-off ECS task and prints a one-time password |

`TAG` defaults to the git short sha of `HEAD` (`latest` outside a git checkout).

## Compose (`deploy/compose/.env`)

`make env` writes `SESSION_SECRET`, `API_KEY_PEPPER`, `LOCAL_MASTER_KEY` and `DEMO_PASSWORD`
with random local-only values. Optional overrides, all with these defaults:

`SR_WEB_PORT` (4210), `SR_API_PORT` (4230), `SR_MOCK_LLM_PORT` (4250), `SR_PG_PORT` (4242),
`SR_REDIS_PORT` (4279), `SR_FLOCI_PORT` (4266), `SR_LOG_LEVEL` (`INFO`).

The overlays set the backends:

| Overlay | Sets |
| --- | --- |
| `compose.yaml` (base) | `ENVIRONMENT=development`, `KEY_MODE=local`, `STORAGE=local`, `TRUSTED_PROXY_HOPS=1`, the Compose Postgres and Redis |
| `compose.floci.yaml` (`make up`) | `SMARTROUTER_CLOUD=floci`, `KEY_MODE=kms` with `alias/smartrouter-local`, `STORAGE=s3` with `smartrouter-local-exports`, `AWS_ENDPOINT_URL=http://floci:4566`, path-style S3 |
| `compose.aws.yaml` (`make up-aws`) | `SMARTROUTER_CLOUD=aws`, `KEY_MODE=kms`, `STORAGE=s3`, your AWS profile; bucket and key from `.env.aws`. See [local-with-aws.md](local-with-aws.md) |

## Routing policies

Policies are created on the Policies page or with `POST /api/v1/policies`. Clients select
one with `model="auto:<name>"`, the `x-smartrouter-policy` header, or a project's default
policy; one policy can be the global default (`is_default`).

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Lowercase letters, digits and dashes (up to 63). Fixed once created |
| `description` | `""` | Shown on the Policies page |
| `strategy` | `balanced` | Preset weights over cost, latency and quality: `cost` (0.7 / 0.1 / 0.2), `latency` (0.1 / 0.7 / 0.2), `quality` (0.02 / 0.02 / 0.96), `balanced` (0.35 / 0.25 / 0.4) |
| `weights` | none | Custom weights, for example `{"cost": 1, "quality": 2}`; keys `cost`, `latency`, `quality`, non-negative, not all zero, normalised. Overrides the strategy's preset. API only |
| `quality_floor` | `0.7` | Minimum quality score (0 to 1) for a model to be eligible |
| `task_floors` | `{}` | Per-task floors that replace `quality_floor` for that task, for example `{"extraction": 0.8}` |
| `hard_floor_boost` | `0.1` | Added to the floor for complexity 4 and 5 (0 to 0.5) |
| `max_latency_ms` | none | Skip models whose expected latency (live p50, else the catalog prior) is above this |
| `max_cost_usd` | none | Skip models whose estimated cost for this request is above this (USD per request) |
| `allowed_models` | none (all) | Only these aliases may be chosen |
| `excluded_models` | `[]` | These aliases are never chosen |
| `allow_fallback` | `true` | On a retryable failure, try the next eligible model. Off: the first model's error is returned |
| `max_attempts` | `3` | Models tried at most, 1 to 6 |
| `budget_downgrade` | `false` | On a TokenShield `402 budget_exceeded`, retry only cheaper eligible models |
| `on_no_eligible` | `best_quality` | When nothing clears the floor: `best_quality` takes the models that pass every other filter, best quality first, and records "floor relaxed"; `reject` returns 422 `no_eligible_model` |
| `baseline_model` | none | The model savings are measured against. Default: the most expensive model that could have served the request |
| `is_default` | `false` | The global default for `model="auto"` |
| `enabled` | `true` | Disabled policies cannot be selected |

How a policy ranks, in order:

1. **Hard filters**: allowlist and exclusions, context window, required capabilities (tools,
   images, JSON output), open circuit, cost ceiling, latency ceiling. Each filtered model
   keeps its reason.
2. **Quality floor** for the detected task (plus the boost on hard requests). Quality comes
   from the benchmark-measured quality table; a model never benchmarked on a task uses its
   tier prior (advanced 0.9, fast 0.72, low-cost 0.66, local 0.6).
3. **Score** the survivors: cost on a log scale, latency linearly, quality as measured,
   combined with the weights. Ties go to the cheaper model, then the alias.
