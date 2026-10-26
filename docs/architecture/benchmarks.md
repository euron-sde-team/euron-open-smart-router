# Benchmarks

The benchmark is where SmartRouter's quality numbers come from and how its savings claim is
checked. It measures every model on a fixed suite of graded cases, writes the results into the
quality table the router ranks by, then routes the same cases through every policy and compares
cost and quality with the always-best baseline. The code is `benchmarks/suite.py`,
`benchmarks/suite.yaml` and `benchmarks/runner.py`. Why it is cross-fitted, and why the router
ranks by expected quality over difficulty, is
[ADR-0006](adr/0006-cross-fitted-benchmark-and-expected-quality.md).

- [The suite](#the-suite)
- [Oracle metadata and simulated endpoints](#oracle-metadata-and-simulated-endpoints)
- [Running a benchmark](#running-a-benchmark)
- [Phase 1: models](#phase-1-models)
- [Phase 2: policies, cross-fitted](#phase-2-policies-cross-fitted)
- [What a run stores](#what-a-run-stores)
- [Results from the demo catalog](#results-from-the-demo-catalog)
- [What the simulation proves and what it does not](#what-the-simulation-proves-and-what-it-does-not)
- [Lessons](#lessons)

## The suite

`suite.yaml` (version `2026.11`) holds **160 cases**: 8 tasks (classification, extraction,
summarization, translation, math, reasoning, coding, chat) x 5 difficulties (1 trivial to 5
hard) x 4 cases each. Difficulty 4 and 5 form the `high` band, 1 to 3 the `low` band, matching
the analyzer's bands. Each case is a single user message and a deterministic grader:

| Grader | Passes when | Cases |
| ------ | ----------- | ----- |
| `exact` | The normalised answer (lower case, trailing punctuation and whitespace removed) equals `expected` | 0 (available, unused by the current suite) |
| `choice` | The answer contains `expected` as a word and none of the other `choices` | 22 |
| `number` | The first number in the answer equals `expected` within `tolerance` (thousands separators allowed) | 32 |
| `contains` | The answer contains every string in `expected` as a **whole word or phrase** (so "no" does not match inside "not" or "one") | 73 |
| `json` | The answer (or the first fenced block) parses as a JSON object containing every expected key and value | 16 |
| `regex` | The answer matches the regular expression in `expected`, ignoring case | 17 |

`load_suite` refuses duplicate case ids and textual expectations that YAML parsed as booleans
(bare `yes` or `no` must be quoted). The graders are pure functions with unit tests
(`tests/unit/benchmarks/test_suite.py`). An LLM judge is not part of 0.1.0.

## Oracle metadata and simulated endpoints

The local demo has no real models, so the mock LLM needs a way to answer a case correctly with a
probability set by its simulated skill. Each case can produce **oracle metadata**
(`Case.oracle()`): `benchmark_case`, `benchmark_task`, `benchmark_difficulty` and
`benchmark_expected`, sent in the request's `metadata`. The mock answers correctly with
probability `sigmoid(2.4 x (skill - difficulty))`, decided deterministically from the case and
the model, so runs are reproducible.

The oracle is the expected answer, so it must never reach a real provider:

- An endpoint is **simulated** only if it was created with `simulated = true` (the flag cannot be
  changed afterwards).
- Phase 1 adds the metadata only when the model's endpoint is simulated.
- Phase 2 always attaches it, and the gateway's `_payload` removes every `benchmark_*` key (and
  the whole `metadata` object if nothing else is left) for any endpoint that is not simulated.

Against real endpoints the same suite runs unchanged; the models simply answer the prompt.

## Running a benchmark

| How | What happens |
| --- | ------------ |
| Dashboard or `POST /api/v1/benchmarks/runs` (admin) | Creates a `benchmark_runs` row (`queued`), pushes `{run_id, models, policies}` onto the Redis list `sr:bench:queue`, writes an audit row, answers `202`. A worker pops it (`BLPOP`) and runs it. If the push fails because Redis is down, the run is marked `failed` and the API answers `503 dependency_unavailable` |
| `make benchmark` (`smartrouter benchmark` in the api container) | Runs the suite in process and prints quality, cost, savings and quality change per model and policy |

`models` restricts phase 1 to some aliases (default: every enabled model). `policies` restricts
phase 2 (default: every enabled policy; an empty list skips phase 2). A run moves through
`queued`, `running`, then `done` or `failed` (with the error). Up to 16 upstream calls run at
once. A run whose worker dies mid-run (a crash or a redeploy) would stay `running` forever, so
every worker checks once a minute and marks runs still `running` after `BENCHMARK_STALE_AFTER_S`
(7200 s) as `failed`, with "The worker running this run stopped before it finished. Start it
again." Raise the setting if a real suite on slow providers takes longer than two hours.

## Phase 1: models

Every selected model answers every case (temperature 0, `max_tokens` 600), called through its
adapter directly, not through the gateway, so phase 1 records no decisions and no health. A
call that fails counts as a failed case with zero cost. Each answer is graded, and the outcomes
are aggregated per (model, task, band):

1. one `benchmark_results` row per cell with `target_type = model`: cases, passed, score, cost,
   average latency;
2. an upsert into `quality_scores` per cell (`source = benchmark`, `samples` = the number of
   cases, `run_id`), replacing any earlier benchmark or manual score for that cell;
3. `config_cache.invalidate()`, so every api process ranks by the new table within about a
   second.

## Phase 2: policies, cross-fitted

Each selected policy then routes every case through the real pipeline:
`GatewayService.chat` with `model = "auto:<policy>"`, source `benchmark`, no project and no key.
The decision is recorded like any other (with `source = benchmark`, which rollups and analytics
exclude), the answer is graded, and the cost is the chosen model's price applied to the
response's usage.

Two things keep phase 2 honest:

- **Cross-fitting (2 folds).** Cases are assigned to fold `index mod 2`. A case in fold *f* is
  routed with a catalog whose quality table is built only from phase 1 outcomes on the **other**
  fold, and whose analyzer calibration is measured only on the other fold. Without this the
  router would rank models by their score on the very case it is about to be graded on, and the
  result would overstate what it can do on traffic it has not seen.
- **Live health is ignored for ranking.** The service is built with `live_health = False`:
  ranking sees every circuit closed and uses each model's latency prior, so a run does not depend
  on what an earlier run or demo traffic did to health. (The candidate loop still records
  outcomes to Redis and still honours a half-open probe lock if one exists.)

The **calibration** is the evidence behind expected quality over difficulty
([routing.md](routing.md#expected-quality-over-difficulty)): for every case, the analyzer's
predicted (task, band) and whether the case is truly hard. The run summary stores it over all
160 cases, and the catalog reads it from the latest finished run.

## What a run stores

| Where | What |
| ----- | ---- |
| `benchmark_runs.summary` | `baseline` (the model with the highest input plus output price per 1M tokens), `cases`, `models` (`quality`, `cost_usd`, `latency_ms` each), `policies` (`quality`, `cost_usd`, `latency_ms`, `model_mix`, and against the baseline `savings_pct` and `quality_delta`), `calibration` (`{"<task>|<band>": [truly hard, cases]}`) and `results_file` |
| `benchmark_results` | One row per (target, task, band) for models and policies, with `model_mix` for policies |
| `quality_scores` | The phase 1 cells, which the router ranks by |
| Object storage | `benchmarks/<run_id>/cases.jsonl.gz`: every graded answer as gzipped JSON lines (`target_type`, `target`, `case`, `task`, `difficulty`, `model`, `passed`, `cost_usd`, `latency_ms`, and the first 400 characters of `answer`) |

The per-case file is large and read rarely, so the row keeps only its key, row count and size.
Failing to write it never fails the run; the summary's `results_file` is then null and the
download endpoint answers 404. `GET /api/v1/benchmarks/runs/{id}/cases` returns a link: on S3, a
presigned GET valid for 300 s; on local storage, the API's own streaming endpoint
(`/cases/file`). Result files are kept for `BENCHMARK_RESULTS_DAYS` (365 by default): for an older
run both endpoints answer 404 ("This run's results file expired after 365 days"), and on AWS
the `storage` module's lifecycle rule (`expiration_days`, also 365 by default; keep the two
equal) deletes the object. Older runs keep their summary.

## Results from the demo catalog

The demo catalog has five simulated models (`gpt-mini`, `gpt-fast`, `llama-local`, `gpt-pro`,
`claude-pro`) and the demo policies. The baseline is `claude-pro`, the most expensive, which a
team without a router would send everything to. Over the 160 cases, cross-fitted, `claude-pro`
passes **88.1%**.

| Policy | Quality | Change vs baseline | Cost saved vs baseline |
| ------ | ------- | ------------------ | ---------------------- |
| `auto:quality` | 89.4% | +1.3 points | 5.7% |
| `auto:balanced` (floor 0.80) | 86.3% | -1.9 points | 19.8% |
| `auto:cost` | 78.8% | -9.4 points | 37.2% |

`auto:quality` can beat the baseline because no single model is best at every task, so routing
by measured quality per task can outscore always using one model. The floor is the dial between the other two: the
same balanced policy at a 0.70 floor saved 37% for -9.4 points, at 0.80 it saved 19.8% for
-1.9 points. Integration test `test_benchmark_shows_savings_within_quality_tolerance` asserts the shape on
every run (baseline `claude-pro`; `auto:quality` within 2 points of the baseline and cheaper;
`auto:balanced` at a 0.80 floor saving at least 10% within 5 points, and routing to models other
than the baseline), and `test_benchmark_is_reproducible` asserts two runs give identical quality
and cost.

## What the simulation proves and what it does not

It proves the machinery: graders, the quality table, shrinkage, calibration, cross-fitting, the
ranking engine and the fallback path all work end to end, deterministically, and a policy's
cost and quality trade-off can be measured and compared before it carries traffic.

It does not prove that these savings hold for your traffic. The mock models have per-task skills
chosen to make routing worthwhile (a cheap model that is good at classification, an expensive
one that is best at code). Real providers will differ, and so will real prompts, which are
longer and messier than single-message benchmark cases. Run the suite against your own
endpoints (create them with `simulated = false`) before trusting a number, and add cases that
look like your workload.

## Lessons

- **Tuning heuristics on the evaluation cases inflates savings.** Analyzer rules or quality
  scores adjusted while looking at the cases they are graded on make routed results look better
  than they are: the system memorises the suite instead of learning the workload. That is why
  the quality table and the calibration are cross-fitted, and why an analyzer change should be
  judged on cases it was not tuned on. The numbers above were measured that way.
- **The analyzer's difficulty band is a guess.** Measured on held-out cases, the keyword analyzer
  judged the band correctly only about 63% of the time. Ranking by the predicted band alone sent
  hard work to cheap models; ranking by quality expected over both bands, weighted by the
  measured chance a request is truly hard, fixed it
  ([routing.md](routing.md#expected-quality-over-difficulty)).
- **Small cells lie.** A (model, task, band) cell holds a handful of cases. Hierarchical
  shrinkage keeps a lucky 3 of 3 from outranking a model with a strong record across the band.
