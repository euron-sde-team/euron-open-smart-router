# Routing

How SmartRouter turns one chat completion request into an ordered, explained list of models.
Three pieces do the work, each a pure function or an immutable snapshot, so a decision can be
replayed in a unit test and shown in the route simulator without calling a model:

1. the **analyzer** (`routing/analyzer.py`) says what the request is: task, difficulty, size and
   required capabilities;
2. the **ranking engine** (`routing/engine.py`) filters the models by hard constraints and the
   quality floor, then scores the survivors under the policy;
3. the **quality model** (`gateway/catalog.py`) says how good each model is at this task, from
   benchmark evidence shrunk toward sensible priors and averaged over the difficulty the
   analyzer might have misjudged.

Where the pipeline calls these and what happens next (admission, the call, fallback) is in
[LLD.md](LLD.md#the-request-pipeline-step-by-step). Why the analyzer is rule-based is
[ADR-0002](adr/0002-deterministic-explainable-request-analysis.md); why quality is an
expectation over difficulty is [ADR-0006](adr/0006-cross-fitted-benchmark-and-expected-quality.md).

- [Policies](#policies)
- [The analyzer](#the-analyzer)
- [The ranking engine](#the-ranking-engine)
- [The quality model](#the-quality-model)
- [The baseline and savings](#the-baseline-and-savings)
- [The route simulator](#the-route-simulator)

## Policies

A policy is a named row in `routing_policies`. Clients select it with `model="auto:<name>"`;
plain `auto` uses the `x-smartrouter-policy` header, then the project's default policy, then the
global default. A concrete model alias bypasses routing entirely (passthrough, no fallback).

| Field | Default | Effect |
| ----- | ------- | ------ |
| `strategy` | `balanced` | One of `cost`, `latency`, `quality`, `balanced`: selects the weight set below |
| `weights` | none | Custom `{cost, latency, quality}` weights; non-negative, not all zero, normalised to sum to 1. Overrides `strategy` |
| `quality_floor` | 0.7 | Minimum quality score (0 to 1) for any task without its own floor |
| `task_floors` | `{}` | Per-task floors, for example `{"coding": 0.85}` |
| `hard_floor_boost` | 0.1 | Added to the floor when complexity is 4 or more (0 to 0.5), capped at 1.0 |
| `max_latency_ms` | none | Hard ceiling on expected latency |
| `max_cost_usd` | none | Hard ceiling on the estimated cost of this request |
| `allowed_models` | none (all) | Allowlist of aliases |
| `excluded_models` | `[]` | Denylist of aliases |
| `allow_fallback` | true | When false, only the top candidate is tried |
| `max_attempts` | 3 | Candidates tried at most (1 to 6) |
| `budget_downgrade` | false | On a TokenShield `402 budget_exceeded`, try strictly cheaper candidates |
| `on_no_eligible` | `best_quality` | What to do when no model clears the floor: `best_quality` or `reject` |
| `baseline_model` | none | The model savings are measured against; default is the priciest capable model |

## The analyzer

`analyze(body)` is deterministic: the same body always yields the same `Analysis`, and every
conclusion carries the signals that produced it, for example
`coding: code fence`, `coding: code verb`, `hard cues: concurrency, edge cases`. The signals are
stored on the decision row and shown in the dashboard.

### Task

Eight tasks, in this order (the last tie-break): `coding`, `math`, `reasoning`, `extraction`,
`classification`, `summarization`, `translation`, `chat`. Each task except `chat` has a small
set of case-insensitive rules (a regular expression and a signal name):

| Task | Rules |
| ---- | ----- |
| coding | code fence; a code keyword (`def`, `class`, `function`, `const`, `let`, `var`, `import`, `return`, `public static`), matched case-sensitively at the start of a line and followed by more code, so English like "Return only the address" does not count; a language name; a code verb (`refactor`, `debug`, `stack trace`, `unit test`, `compile`, `bug`, `endpoint`, `api`); "write/implement/fix a function/script/program/query/class" |
| math | a math verb (`solve`, `calculate`, `integral`, `probability`, `percent`, ...); arithmetic like `3 * 4`; counting ("how many ways"); a numeric answer format ("rounded to", "number only"); proof words |
| reasoning | reasoning cues (`why`, `trade-offs`, `pros and cons`, `compare`, `evaluate`, `justify`); deliberation (`step by step`, `analyse`); planning (`strategy`, `design`, `architecture`, `recommend`) |
| extraction | extract verbs (`extract`, `parse`, `fields`, `entities`); structured output words (`json`, `yaml`, `csv`, `schema`) |
| classification | classify verbs (`classify`, `categorise`, `label`, `sentiment`, `is this spam`); closed answers (`one of`, `one word`, `yes or no`) |
| summarization | `summarise`, `summary`, `tl;dr`, `key points`, `condense`, `recap`, `bullet points` |
| translation | `translate`; a target language ("into French") |

Scoring, per task:

- each rule that matches the **user** text adds 1.0, plus a **first-line bonus** of 0.5 when it
  also matches the first line of the user text (the instruction usually sits there; the
  material to work on follows it);
- a rule that matches only the **system** prompt adds 0.5 (the system prompt often describes the
  application, not the request);
- a JSON `response_format` (`json_object` or `json_schema`) adds 1.0 to `extraction` and sets
  `needs_json`.

The highest score wins. Ties are broken, in order, by:

1. **the earliest cue in the user text**: the task whose first matching rule starts nearest the
   beginning of the user text wins, because a request usually leads with the instruction and
   the material follows. "Summarize in one sentence: the payment provider changed its API."
   scores 1.5 for `summarization` and 1.5 for `coding` (`api` is a code verb, and both are on
   the first line), and is `summarization` because "Summarize" comes first. A task whose
   score comes only from the system prompt or a JSON `response_format` has no position in the
   user text and loses this step to any task that has one;
2. **the task order above.**

With no signals at all the task is `chat`. Proven by
`test_a_tie_goes_to_the_task_whose_cue_comes_first` and `test_the_instruction_line_breaks_ties`.

### Complexity

Complexity is an integer from 1 (trivial) to 5 (hard). It starts at a base per task and moves
with the evidence:

| Task | Base |
| ---- | ---- |
| classification | 1 |
| extraction, summarization, translation, chat | 2 |
| coding, math, reasoning | 3 |

| Signal | Change |
| ------ | ------ |
| Input over 6000 tokens / over 1500 tokens | +2 / +1 |
| Four or more numbered or bulleted requirement lines in the user text | +1 |
| **Hard cues** in the system or user text: one distinct cue / two or more | +1 / +2 |
| Task is `reasoning` and at least two reasoning rules fired | +1 |
| Easy wording (`briefly`, `simple`, `quick question`, `in simple words`, `for a child`) | -1 |
| The request carries `tools` | +1 |

The hard cues are a fixed vocabulary in five groups: engineering (`optimise`, `edge cases`,
`concurrency`, `distributed`, `race condition`, `security`, `scalability`, `production-ready`,
`multi-step`, `complex`, `rigorous`, `formal`, `prove`), legal language (`notwithstanding`,
`clause`, `liable`, `indemnify`, `contractual`), nuance and tone (`sarcasm`, `irony`, `idiom`,
`nuance`, `diplomatic`, `implicit`, `not word for word`), causes and risk (`main driver`,
`root cause`, `risk`, `conflict`, `least risk`), and algorithms (`probability`, `idempotent`,
`O(...)`). Answer-format phrases such as "one word" are deliberately not easy cues: a one-word
verdict can still be hard to reach.

The result is clamped to 1..5. The **band** is `high` when complexity is 4 or more, `low`
otherwise; the quality table is kept per band.

### Size and capabilities

`input_tokens` is estimated with tiktoken (`o200k_base`), adding 3 tokens of framing per message,
3 for reply priming, the JSON of tools, tool calls and `json_schema` formats, and a flat 1105
tokens per image part. `output_tokens` is `max_completion_tokens` or `max_tokens` when given,
else a per-task default used only for cost estimates (classification 20, extraction 200,
summarization 250, chat, math and translation 300, reasoning 600, coding 700). `needs_tools` is
true when the body has `tools`; `needs_vision` when any message has an image part.

### Caller overrides

Two request headers let the application that knows better say so:

| Header | Effect |
| ------ | ------ |
| `x-smartrouter-task` | Replaces the detected task when the value is one of the eight tasks |
| `x-smartrouter-complexity` | Replaces the computed complexity when the value is 1 to 5, and makes the quality lookup trust that band ([below](#expected-quality-over-difficulty)) |

Invalid values are ignored. An applied override is appended to the signals ("task set by
caller: coding") and recorded in `Analysis.overridden`.

## The ranking engine

`rank(analysis, policy, models, health, quality)` returns every model as a `Candidate` with an
`eligible` flag, a score, its components and a reason. Eligible candidates come first, best
first; filtered ones follow, sorted by alias.

### Hard filters, in order

The first filter a model fails is its reason:

1. not in the policy's `allowed_models`;
2. in `excluded_models`;
3. context window: estimated input plus `min(output allowance, max_output_tokens)` exceeds the
   model's `context_window`;
4. the request needs tools and the model has no tool calling;
5. the request has an image and the model has no image input;
6. the request asks for JSON output and the model has no JSON mode;
7. **circuit open** (`half_open` is not filtered: the gateway's health gate lets one probe
   through, see [health-and-fallback.md](health-and-fallback.md));
8. estimated cost above `max_cost_usd`;
9. expected latency above `max_latency_ms`.

A model that passes all of them is then checked against the **quality floor**:

```
floor = task_floors.get(task, quality_floor) + (hard_floor_boost if complexity >= 4 else 0)
floor = min(floor, 1.0)
```

A model whose quality for this task is below the floor is set aside as *floor only*, with the
reason "`<task>` quality 0.62 below the floor 0.80".

### When nothing clears the floor

If no model survives but some failed only the floor:

- `on_no_eligible = "best_quality"`: **every** floor-only model becomes eligible, ordered by
  quality, best first. The top one answers and the rest remain fallbacks. The decision is
  marked `floor_relaxed`, and the reasons say "no model cleared the quality floor; picked the
  highest quality capable model" and "fallback 1 with the floor relaxed".
- `on_no_eligible = "reject"`: nothing is eligible and the gateway answers
  `422 no_eligible_model` with every candidate's reason.

Models removed by a hard filter never come back: the floor is the only thing that is relaxed.

### Scoring

Each survivor gets three components in 0..1 and a weighted sum:

| Axis | Normalisation |
| ---- | ------------- |
| Cost | Log scale across the survivors: `eps = max(1, 0.001 x max cost)`, `c = ln(cost_nusd + eps)`, score `(max c - c) / (max c - min c)`. Prices differ by multiples, so on a linear axis one expensive model would squash every cheap one to the same score; `eps` keeps free models finite and makes sub-0.1% differences irrelevant |
| Latency | Linear across the survivors: `(max - latency) / (max - min)`. Expected latency is the live p50 once the model has at least 5 latency samples in Redis, else the model's `latency_prior_ms` |
| Quality | The quality score itself, not normalised |

The cheapest and fastest survivor score 1 on their axes; when all survivors are equal on an axis,
everyone scores 1. The cost estimate is the model's price applied to the estimated input tokens
and `min(output allowance, max_output_tokens)`, in integer nano-dollars.

Strategy weights over (cost, latency, quality):

| Strategy | Cost | Latency | Quality |
| -------- | ---- | ------- | ------- |
| `cost` | 0.70 | 0.10 | 0.20 |
| `latency` | 0.10 | 0.70 | 0.20 |
| `quality` | 0.02 | 0.02 | 0.96 |
| `balanced` | 0.35 | 0.25 | 0.40 |

Under `quality`, cost and latency together can move a score by at most 0.04, so a cheaper or
faster model wins only when it is within about 4 quality points of the best.

### Deterministic tie breaks

Eligible candidates sort by score descending, then estimated cost ascending, then alias. With
the floor relaxed they sort by quality descending, then cost, then alias. Nothing random enters
the ranking, so the same request against the same catalog and health always yields the same
order. The winner's reason names the largest weighted component ("cheapest", "fastest" or
"highest quality contributor") and its quality source.

## The quality model

The engine asks the catalog for one number per (model, task): how likely this model is to get
this request right. `quality_scores` holds the raw evidence per (model, task, band): the
benchmark's pass rate and the number of cases behind it (`samples`), or a manual score
(`samples = 0`). Raw cells are small (the suite has 20 cases per task, split across two bands
and, in phase 2, across two folds), and "3 of 3 passed" is weak evidence. The catalog turns them
into estimates in three steps.

### Tier priors

Until a model has been measured, its quality is the prior for its tier:

| Tier | Prior |
| ---- | ----- |
| `advanced` | 0.90 |
| `fast` | 0.72 |
| `low-cost` | 0.66 |
| `local` | 0.60 |

### Hierarchical shrinkage

For model *m*, task *t* and band *b*, with measured cells (score *s*, samples *n*):

1. **Band score.** The model's pass rate over every task in band *b*, shrunk toward the tier
   prior with 2 pseudo-cases:
   `B(m,b) = (sum of s x n over tasks in b + prior x 2) / (sum of n + 2)`.
2. **Task offset.** How much better or worse the model does on task *t* than its band score,
   pooled over **both** bands and shrunk toward zero with 2 pseudo-cases:
   `O(m,t) = sum over bands of (s - B(m,b)) x n / (sum of n + 2)`. A model that is weak at
   translation is weak at easy and hard translation alike, so the offset carries across bands.
3. **Cell.** The parent estimate is `P = clamp(B(m,b) + O(m,t), 0, 1)`. A measured cell is
   blended toward it with 2 pseudo-cases: `(s x n + P x 2) / (n + 2)`. A missing cell in a
   measured band is the parent itself, labelled "benchmark (estimated from `<band>` band and
   task)". A band the model was never measured in returns nothing, and the engine falls back to
   the tier prior.

A large cell dominates its parent; a small lucky cell is pulled toward what the model does
elsewhere. **Manual scores** (set with `PUT /api/v1/quality`, for models a benchmark cannot
reach) are taken as given: they skip the shrinkage and are excluded from band scores. The next
benchmark run that measures that cell replaces them.

### Expected quality over difficulty

The analyzer's band is an estimate. On held-out benchmark cases the keyword analyzer judged the
difficulty band correctly only about **63%** of the time. Ranking by the band's score alone
meant that a hard request the analyzer called easy was compared on easy-band scores, where cheap
models look nearly as good as expensive ones, and was sent to a cheap model that then failed.

So unless the caller set the complexity, the engine ranks by the expectation over both bands:

```
quality = P(hard | task, predicted band) x q_high + (1 - P(hard | task, predicted band)) x q_low
```

where `q_high` and `q_low` are the shrunk scores above (the tier prior for a band with no data).
`P(hard | task, band)` is **measured by the benchmark**: for every case it records the
analyzer's predicted (task, band) and whether the case is truly hard (difficulty 4 or more), and
stores the counts in the run summary as `calibration`. The catalog reads the latest finished
run's calibration and shrinks it toward a prior with 4 pseudo-cases:

```
P(hard | task, band) = (truly hard + prior x 4) / (cases + 4)     prior: low 0.2, high 0.8
```

The candidate's quality source then reads "benchmark, expected over difficulty (P(hard) 0.35)".
When the caller sends `x-smartrouter-complexity`, the band is trusted and the cell for that
band is used directly.

The quality **floor** still uses the analyzer's complexity (the hard-floor boost applies when
complexity is 4 or more); the expectation changes the score compared with the floor, not the
floor itself.

## The baseline and savings

Every decision names a baseline: the model the request would have gone to without a router.
`baseline_for` returns the policy's `baseline_model` when set, else the most expensive model
that could have served the request (context window, tools and vision satisfied), priced at the
request's estimated tokens, ties broken by alias. After the call, the decision records
`baseline_cost_nusd` as the **actual** tokens priced at the baseline's rates, next to
`actual_cost_nusd`. Savings are `baseline - actual`, floored at zero in the Prometheus counter
and in analytics totals, which also count baseline cost only for successful requests.

Passthrough requests use the requested model as their own baseline, so they show no savings.

## The route simulator

`POST /api/v1/route/simulate` (viewer role) runs the planning half of the pipeline (policy
resolution, analysis with optional task and complexity overrides, live health, ranking and the
baseline) and returns the analysis, the floor, every candidate with its score components and
reason, the estimated cost and the estimated savings against the baseline. It never calls a
model and records no decision. Integration test `test_simulate_explains_without_calling_upstream`
checks the mock upstream's call counter stays unchanged.
