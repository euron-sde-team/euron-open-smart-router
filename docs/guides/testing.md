# Testing

SmartRouter has several layers of automated checks. Each proves something different.

| Layer | Command | Needs | Proves |
| --- | --- | --- | --- |
| Static | `make lint typecheck` | uv, pnpm | ruff, mypy `--strict`, ESLint and TypeScript strict across the API and dashboard |
| Unit (187) | `make test` | uv, pnpm | Pure logic: 158 API tests and 29 dashboard tests. No database, no network |
| Integration (77) | `make test-integration` | Docker | The real API in-process against real Postgres and Redis, with the mock provider over real HTTP. Every acceptance criterion in [PLAN.md](../PLAN.md) that is about behaviour, and the production hardening. 3 of the 77 run only in the option matrix |
| Option matrix (4 runs) | `make test-matrix` | `make up` (Floci) | The integration suite under every key and storage backend: local or KMS, local disk or S3 |
| Browser e2e (12) | `make e2e` | `make up && make demo` | User journeys through the real dashboard, gateway, API and mock provider |
| Browser QA | `make qa` | `make up && make demo` | Every page in light and dark, desktop and phone: no console errors, failed requests, broken images, error states or overflow |
| Overhead | `make bench` | `make up && make demo` | What routing adds on top of the upstream call |
| Routing benchmark | `make benchmark` | `make up` with models configured | Savings against the baseline at a measured quality |
| Infrastructure | `make tf-validate tf-lint tf-security sanitize` | Docker | Terraform validity for three environments, tflint, checkov, and no secrets, cloud identifiers or em/en dashes in the tree |
| AWS on Floci | `make tf-aws-on-floci` | Docker | The full `aws-dev` environment plans, applies, converges and destroys against an AWS API |

The counts are collected test cases at the time of writing (`uv run pytest --collect-only`).

## Unit tests

```bash
make test
```

API (`apps/api/tests/unit`, mirroring the source tree): the request analyzer and the ranking
engine (`routing/`), the benchmark suite and graders (`benchmarks/`), quality lookup and
difficulty calibration in the catalog (`gateway/`), the decision recorder (`decisions/`),
pricing, the OpenAI and Anthropic adapters against an in-memory HTTP transport and the
endpoint URL checks (`providers/test_endpoint_urls.py`: metadata, link-local, multicast and
reserved addresses refused, private and loopback addresses only while
`ENDPOINT_ALLOW_PRIVATE` is on, every resolved address checked), the mock LLM, and config,
money, sealing, storage, security, error handling and the client address behind
`TRUSTED_PROXY_HOPS` (`core/test_client_ip.py`: a spoofed leftmost `X-Forwarded-For` entry is
ignored). The analyzer tests include the tie-break: equal task scores go to the cue that
comes first in the text. A drift test fails if `docs/api/openapi.yaml` no longer matches the app
(`make openapi` regenerates it, then `pnpm gen:api` in `apps/web` refreshes the typed
client).

Dashboard (`apps/web/test`, Vitest): formatting, error messages, domain helpers and
components.

## Integration tests

```bash
make test-integration
```

Starts the Compose Postgres and Redis if they are not already reachable
(`scripts/test-db.sh`) and runs `apps/api/tests/integration` with coverage against
`smartrouter_test` and Redis database 15 (override with `TEST_DATABASE_URL` and
`TEST_REDIS_URL`). A guard refuses any database whose name lacks `test`, or a non-local host
unless `ALLOW_REMOTE_TEST_DB=true`, because the suite truncates every table. The mock LLM
runs in a background thread on a free port with no simulated latency; tests inject faults
(error, timeout, throttle, TokenShield budget refusal) over its HTTP admin API and read its
call counter to prove what reached an upstream.

| File | Tests | Proves |
| --- | --- | --- |
| `test_acceptance.py` | 30 | Acceptance criteria 1 to 9, one group per criterion (table below) |
| `test_benchmark.py` | 4 | Acceptance criterion 10: savings within tolerance, reproducibility, downloadable answers, a clear 404 when a run has no results file |
| `test_admin.py` | 15 | Sign-in, CSRF, lockout, password change ends sessions, viewer and admin permissions, owner guards, every resource's CRUD and audit, endpoints and models, disabling a model removes it from routing, the simulator never calls upstream, manual quality overrides, health and metrics |
| `test_operations.py` | 11 | Rollups feed time series and breakdowns; decision log filters and pagination; a circuit opening raises one alert; the health probe counts an unreachable endpoint and closes a half-open circuit; a crashed consumer's decisions are reclaimed without duplicates; decisions are written directly when the stream is unavailable; the worker runs queued benchmarks and marks failed ones; key RPM limits; revoked and expired keys |
| `test_hardening.py` | 14 | Production hardening: an admin write invalidates the catalog on every instance through the `sr:cfg:version` stamp; metadata URLs are refused; moving an endpoint to another host needs its key again; one default policy under concurrent updates; feedback needs a real request id and works before the worker drains the decision; feedback is rate limited per key; a pinned model rate limited upstream returns `429 upstream_rate_limited`; a stream falls back before the first chunk; only one request probes a half-open circuit; a circuit opened by the worker's probe raises an alert; a run abandoned by a dead worker is marked failed; a queue outage never leaves a run `queued`; an expired results file is a clear 404; feedback still works when Redis is down |
| `test_aws_backends.py` | 3 | KMS sealing with encryption context; S3 put, get and presign; benchmark result files landing in S3 with a presigned link. Runs only under `make test-matrix` |

### Acceptance criteria

| # | Criterion (from [PLAN.md](../PLAN.md)) | Proved by |
| --- | --- | --- |
| 1 | An unmodified OpenAI SDK works with `auto`, `auto:cost`, `auto:latency`, `auto:quality`; routing headers are returned | `test_unmodified_openai_sdk_routes_auto`, `test_models_endpoint_lists_routes_and_models` |
| 2 | Cost-first sends a simple classification to the cheapest eligible model; quality-first sends hard coding to the best coder; latency-first sends to the fastest healthy model | `test_cost_first_sends_simple_classification_to_the_cheapest_eligible`, `test_quality_first_sends_hard_coding_to_the_best_coder`, `test_latency_first_sends_to_the_fastest_healthy_model`, `test_policy_header_and_project_default`, `test_unknown_policy_is_a_clear_error` |
| 3 | Hard constraints hold: context, tools, vision, JSON, allowed models | `test_capability_requirements_exclude_models`, `test_context_window_is_respected`, `test_vision_goes_only_to_a_vision_model`, `test_allowed_models_are_respected`, `test_no_eligible_model_with_reject_policy_is_422` |
| 4 | A policy never picks a model below its quality floor | `test_quality_floor_is_never_crossed` |
| 5 | An open circuit is never chosen; failures fall back and the chain is recorded; with fallback off the error is returned | `test_failure_falls_back_and_the_chain_is_recorded`, `test_throttling_and_timeouts_are_retryable`, `test_with_fallback_disabled_the_error_is_returned`, `test_open_circuit_is_never_chosen` |
| 6 | Breakers open after repeated failures and recover through a half-open probe | `test_breaker_recovers_through_a_half_open_probe`, `test_manual_reset_closes_a_circuit` |
| 7 | Every decision is logged with task, complexity, candidates, attempts, costs, latency and savings | `test_every_decision_is_logged_with_costs_and_savings`, `test_failed_requests_are_logged_too`, `test_feedback_is_attached_to_the_decision`, `test_analytics_report_savings` |
| 8 | A concrete model name is passed through unrouted | `test_a_concrete_model_is_passed_through_unrouted`, `test_unknown_model_is_404` |
| 9 | TokenShield: actual cost from `x-tokenshield-cost-usd`; a `402 budget_exceeded` downgrades when the policy allows | `test_actual_cost_comes_from_tokenshield`, `test_budget_refusal_downgrades_to_a_cheaper_model`, `test_budget_refusal_is_returned_when_downgrade_is_off` |
| 10 | Reproducible benchmark: routed policies save cost at quality within tolerance; results stored and fed into the quality table | `test_benchmark.py`: `balanced` saves at least 10% within 5 quality points of the `claude-pro` baseline, `quality` stays within 2 points and still saves; the quality table is replaced by benchmark scores; benchmark traffic stays out of the dashboards; the same run twice gives the same numbers |
| 11 | `make up` runs everything in Docker; `make tf-aws-on-floci` applies the AWS stack | `make up` (with the `local-floci` apply inside it) and `make tf-aws-on-floci` |
| 12 | Routing adds little latency | `make bench` |

Routing in `test_acceptance.py` runs against a pinned quality table in `conftest.World`, so
every expected choice can be worked out by hand.

## Option matrix

```bash
make up
make test-matrix
```

Runs the integration suite four times: keys `local` or `kms`, storage `local` or `s3`,
against Floci with the `local-floci` Terraform applied. It stops early with a clear message
if Floci is not reachable on `SR_FLOCI_PORT` (4266).

## Browser end-to-end tests

```bash
make up && make demo
make e2e
```

Playwright (Chromium) against http://localhost:4210 (`WEB_URL` overrides), signing in once
with the demo password. Suites:

- `01-auth`: a wrong password shows an actionable error, signed-out visitors are sent to
  sign in, the owner signs in and out.
- `02-route`: create a project with a default policy and a key in the UI, call the gateway
  with `model="auto"` and see it routed by that policy and explained, and see a pinned model
  passed through.
- `03-policies-simulator`: create a policy in the dashboard and call it as `auto:<name>`;
  the simulator ranks models without calling any.
- `04-models-benchmarks-roles`: live health and the quality matrix, an endpoint connection
  test, benchmark results with a working download, and a viewer who can read everything and
  change nothing.

`E2E_HEADED=1 pnpm test` in `e2e/` shows the browser.

## Browser QA sweep

```bash
make qa
```

`scripts/qa/qa.mjs` (Puppeteer) signs in through the real form and visits every page (login,
overview, decisions, both Models tabs, benchmarks, simulator, policies, projects and the
first project, keys, endpoints, teams, users, audit, settings) in light and dark at 1440x900
and 390x844. It saves screenshots to `scripts/qa/output/` and fails on console errors,
failed or 4xx/5xx requests, broken images, rendered error states and horizontal overflow.
Narrow a run with `QA_PAGES`, `QA_THEMES` and `QA_SIZES`. The README captures come from
`scripts/qa/screenshots.mjs`, and `scripts/qa/make-tour.sh` stitches the tour GIF (needs
ffmpeg).

## Overhead benchmark

```bash
make up && make demo
make bench
```

`apps/api/src/smartrouter/bench.py` sends the same request three ways: straight to the mock
provider, through SmartRouter with a pinned model (auth, limits, recording), and through
SmartRouter with `model="auto"` (adding analysis, health lookup and ranking). The mock
answers instantly for these prompts, so the differences are SmartRouter's own cost. 400
sequential requests by default (`BENCH_REQUESTS`, and `BENCH_CONCURRENCY`, default 1), so the
numbers measure overhead rather than queueing.

## Routing benchmark

```bash
make benchmark
```

Runs `smartrouter benchmark` in the api container: every model answers the 160-case suite,
the pass rates per task and difficulty become the quality table, then every policy routes
every case through the real pipeline (cross-fitted, so a case is never routed by scores
measured on itself). It prints quality, cost, savings and quality change per policy against
the baseline (the most expensive model). With the demo catalog, the baseline `claude-pro`
scores 88.1%; `auto:balanced` scores 86.3% (1.9 points lower) and saves 19.8%,
`auto:quality` 89.4% (1.3 points higher) saving 5.7%, and `auto:cost` 78.8% (9.4 points
lower) saving 37.2%.

Limit a run with `--models gpt-fast,llama-local` or `--policies cost,balanced` (an empty
`--policies ""` skips phase 2):

```bash
docker compose --env-file deploy/compose/.env -f deploy/compose/compose.yaml \
  -f deploy/compose/compose.floci.yaml exec -T api smartrouter benchmark --policies balanced
```

The Benchmarks page can queue the same run for the worker, and each run's per-case answers
download as gzipped JSON lines from object storage.

## AWS environment on Floci

```bash
make tf-aws-on-floci
```

`terraform validate` checks syntax and types, not what happens at plan and apply time. This
target starts a throwaway Floci on port 4296 (`TF_FLOCI_PORT` overrides), copies
`infra/terraform` to a temp directory, points the unmodified `aws-dev` environment at it with
a provider override, requests a certificate from Floci's ACM, then runs plan, apply, a
second plan and destroy. The second plan may show in-place updates for attributes Floci does
not persist; any addition or replacement fails the run. The ECR repositories are removed
from state before the destroy, because Floci deletes repositories only when it can reach a
backing registry through the Docker socket, which the script does not give it. It takes
about five minutes.

## Sanitization

```bash
make sanitize
```

`scripts/check-sanitization.sh` scans the files git would commit for cloud credentials,
private keys, real account or resource ids, committed env files, and em or en dashes. Run it
before every commit.
