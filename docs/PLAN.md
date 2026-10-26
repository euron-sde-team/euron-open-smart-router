# Euron Open SmartRouter 0.1.0: build plan

**The right AI model for every request.** A self-hosted, OpenAI-compatible gateway that
picks which model handles each request, balancing cost, latency and quality under policies
you control, and explains every decision.

Source brief: "Project 2: Euron SmartRouter" (Oct 2026). Companion to
[Euron Open TokenShield](https://github.com/euron-sde-team/euron-open-tokenshield): TokenShield
decides whether a request may spend; SmartRouter decides which model spends it.

## Purpose

Teams default every call to their most capable model because choosing per request is hard:
a classification, an extraction and a hard coding task all go to the same expensive model,
and when that provider slows down or fails, everything fails with it. **A router must
route**: look at each request, know which models can do this kind of task well enough,
which are healthy and fast right now, and what each would cost, then pick one, fall back
safely when it fails, and say why. The outcome it guarantees: lower cost and latency at a
quality floor the team sets, with no silent model switches.

### Comparable products

| Product | What it is | Gap SmartRouter fills |
| --- | --- | --- |
| LiteLLM proxy | Self-hosted gateway with config-file routing and fallback lists | No task-aware or quality-aware routing; evaluation minimal |
| RouteLLM (LMSYS) | Research framework: learned strong/weak router | Not a gateway: no health, fallbacks, policies, logs or UI |
| Not Diamond | Hosted learned router | Hosted; opaque decisions |
| OpenRouter auto | Hosted model marketplace with auto routing | Hosted; not your models, policies or data |

SmartRouter's angle: **explainable, policy-driven routing calibrated by your own
benchmarks**, health-aware with circuit breakers and controlled fallback, a decision log
that shows savings against a baseline, self-hosted, and native to TokenShield.

## Acceptance criteria (each is an automated test)

1. An unmodified OpenAI SDK works with `model="auto"` (and `auto:cost`, `auto:latency`,
   `auto:quality`); the response names the chosen model and carries routing headers.
2. At least three model endpoints, including a local model. Cost-first sends a simple
   classification to the cheapest eligible model; quality-first sends a hard coding task to
   the best coding model; latency-first sends to the fastest healthy model.
3. Hard constraints always hold: context length, tool calling, vision, JSON output, the
   policy's allowed models.
4. Quality floor: a policy never picks a model whose benchmark score for the detected task
   is below its floor.
5. An unhealthy model (open circuit) is never chosen; an upstream failure falls back to the
   next candidate and the chain is recorded; with fallback disabled the error is returned,
   never silently switched.
6. Circuit breakers open after repeated failures and recover through a half-open probe.
7. Every decision is logged: task, complexity, candidates with scores and reasons, selected
   model, attempts, estimated and actual cost, latency, and savings against the baseline.
8. A concrete model name is passed through unrouted (no silent switching).
9. TokenShield integration: actual cost read from `x-tokenshield-cost-usd`; a TokenShield
   `402 budget_exceeded` downgrades to a cheaper eligible model when the policy allows.
10. Reproducible benchmark (`make benchmark`): routed policies save cost against the
    always-best baseline with quality inside the policy tolerance; results are stored and
    shown in the dashboard and feed the quality table the router uses.
11. `make up` runs everything in Docker; `make tf-aws-on-floci` applies the AWS stack.
12. Routing adds little latency (`make bench`).

## Scope (0.1.0)

| Area | What ships |
| --- | --- |
| Gateway | `POST /v1/chat/completions` (streaming), `GET /v1/models` (lists `auto` routes and models). Gateway keys per project (`tsr_<prefix>_<secret>`) |
| Endpoints | Provider adapters (OpenAI-compatible incl. Ollama/vLLM, Anthropic) plus a `tokenshield` kind (OpenAI-compatible, reads TokenShield headers and errors) |
| Models | Catalog: endpoint, upstream model, price per 1M in/out, context window, capabilities (tools, vision, json), tier (fast, low-cost, advanced, local) |
| Request analysis | Deterministic, explainable classifier: task (coding, reasoning, math, summarization, extraction, classification, translation, chat), complexity 1 to 5 with the signals that drove it, input tokens, required capabilities |
| Policies | Named policies: strategy (cost, latency, quality, balanced weights), quality floor per task and complexity, max latency, max estimated cost, allowed and excluded models, fallback on/off and max attempts, budget downgrade on/off. Selected by `model` (`auto`, `auto:<policy>`), the `x-smartrouter-policy` header, the project's default, then the global default |
| Ranking | Hard filters, then a weighted score over cost (log-scaled), live latency and expected quality; every candidate gets a score and a reason (selected, filtered by context, below quality floor, circuit open...) |
| Health | Live stats in Redis per model (latency p50/p95, error, timeout and 429 rates over a sliding window); circuit breaker closed/open/half-open; worker probes endpoints |
| Fallback | Ordered by rank; retry on connect error, timeout before first byte, 429, 5xx, TokenShield 402 (cheaper only); never after streaming starts |
| Quality | 160-case benchmark suite (8 tasks x 5 difficulties, deterministic graders), runs per model and per policy; scores per (model, task, difficulty band) become the router's quality table, shrunk hierarchically, and the run measures how often the analyzer misjudges difficulty. Every graded answer is stored as a file in object storage (S3) |
| Decision log | Append-only, via a Redis Stream drained by workers (TokenShield's proven pattern); rollups for analytics |
| Analytics | Requests, savings vs baseline, model and task mix, fallback rate, latency, quality feedback (`POST /v1/feedback`) |
| Dashboard | Overview, Decisions (explanation drawer), Route simulator (rank without calling), Models (health and quality matrix), Policies, Benchmarks, Endpoints, Keys, Projects, Users, Audit, Settings |
| IaC | Terraform reused from TokenShield's proven modules; local-floci, aws-dev, aws-prod |

Not in 0.1.0 (roadmap): learned routers (RouteLLM-style), embedding-based task
classification, per-tenant online learning from feedback, ContextForge integration.

## Ports (project 02)

web 4210, api 4230, postgres 4242, redis 4279, floci 4266, mock LLM 4250, throwaway Floci
for `make tf-aws-on-floci` 4296.

## Design notes

- **No duplicate accounting.** SmartRouter keeps a routing decision log, not a budget
  ledger. With TokenShield in front of the providers, TokenShield enforces budgets and
  records usage; SmartRouter reads its cost header and reacts to its 402.
- **Deterministic mock models** with different quality per task and difficulty make the
  benchmark and routing tests reproducible; the same harness runs against real providers.
- **The benchmark must not grade the router on what it learned from.** Policies are
  routed with quality scores and analyzer calibration measured on the other half of the
  suite (2-fold cross-fitting), and routing ignores live health so a run is reproducible.
  Measured honestly, keyword analysis judged the difficulty band correctly on only about
  63% of held-out cases, so the router ranks by expected quality over both bands, weighted
  by the measured chance a request is truly hard, instead of trusting the band.
- **Small cells are weak evidence.** A (model, task, band) cell holds a handful of cases,
  so it is blended toward the model's band score plus a per-task offset, itself shrunk
  toward the tier prior. A manual score is taken as given.
- **Prices differ by multiples.** Cost is normalised on a log axis, so one expensive model
  cannot squash the cheap ones together and make "cost first" pick a pricier model.

## Results (demo catalog, 160 cases, cross-fitted)

claude-pro (the baseline, always used) passes 88.1% of cases.

| Policy | Quality | Change | Saved vs baseline |
| --- | --- | --- | --- |
| auto:quality | 89.4% | +1.3 points | 5.7% |
| auto:balanced (floor 0.80) | 86.3% | -1.9 points | 19.8% |
| auto:cost | 78.8% | -9.4 points | 37.2% |

The floor is the dial: balanced at 0.70 saved 37% for -9.4 points, at 0.80 19.8% for -1.9.
