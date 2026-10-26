# ADR-0006: A cross-fitted benchmark, and quality expected over difficulty

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

The router ranks models by a quality score per (model, task, band), and its headline claim is
"lower cost at a quality floor you set". Both rest on the benchmark: phase 1 measures every model
on 160 graded cases and fills the quality table; phase 2 routes the same cases through each policy
and compares cost and quality with the always-best baseline.

A straightforward version of this benchmark has two problems:

1. **Grading the router on what it learned from.** If phase 2 routes a case using quality scores
   that include that very case's phase 1 outcome, the router "knows" which model got it right.
   Its measured savings at a given quality are then better than anything it can achieve on new
   traffic. Tuning analyzer rules while looking at the same cases inflates the result the same
   way.
2. **Trusting the analyzer's difficulty band.** Quality is kept per band because cheap models
   collapse on hard cases. But measured on held-out cases, the analyzer put a case in the right
   band only about 63% of the time. A hard request misread as easy was ranked on easy-band
   scores, where a cheap model looks nearly as good as an expensive one, and was sent to the
   cheap model, which then failed.

## Decision

**Cross-fit phase 2.** Cases are split into two folds (index mod 2). A case in one fold is routed
with a catalog whose quality table and analyzer calibration are built only from the other fold.
Routing in phase 2 also ignores live health (every circuit closed, latency priors), so a run is
reproducible and does not depend on what earlier traffic did.

**Rank by quality expected over difficulty.** For every case the benchmark records the
analyzer's predicted (task, band) and whether the case is truly hard (difficulty 4 or more), and
stores the counts in the run summary as `calibration`. The catalog estimates
`P(hard | task, predicted band)` from the latest finished run, shrunk toward a prior (0.2 for a
predicted low band, 0.8 for high) with 4 pseudo-cases, and ranks by

```
P(hard) x quality(high band) + (1 - P(hard)) x quality(low band)
```

unless the caller set the complexity with `x-smartrouter-complexity`, in which case that band is
trusted.

**Shrink small cells.** A cell holds a handful of cases, so each is blended (2 pseudo-cases)
toward a parent: the model's score over every task in that band (itself shrunk toward the tier
prior) plus the model's offset on this task pooled over both bands. Manual scores are taken as
given. Details in [routing.md](../routing.md#the-quality-model).

## Consequences

**Good**
- The published numbers are honest out-of-sample estimates. On the demo catalog: `auto:balanced`
  (floor 0.80) 86.3% quality, 1.9 points under the `claude-pro` baseline's 88.1%, for 19.8% lower
  cost; `auto:quality` 89.4% (+1.3) for 5.7% lower cost; `auto:cost` 78.8% (-9.4) for 37.2% lower
  cost ([benchmarks.md](../benchmarks.md#results-from-the-demo-catalog)).
- Hard requests the analyzer misreads are no longer sent to models that measurably fail them.
- The analyzer can stay simple and explainable
  ([ADR-0002](0002-deterministic-explainable-request-analysis.md)); its error rate is measured
  and priced in rather than ignored.

**Bad / caveats**
- Expected quality pulls cheap models' scores down on tasks where they are weak on hard cases,
  so savings are smaller than a band-trusting router would report. That is the point.
- Two folds halve the evidence behind each phase 2 ranking; shrinkage compensates, but the per-cell
  estimates are noisier than phase 1's.
- The calibration is only as representative as the suite. Traffic that looks unlike the 160
  cases needs its own cases.

## Alternatives considered

- **No cross-fitting.** Rejected: measures memorisation, not routing.
- **Leave-one-out.** More evidence per ranking, but one catalog per case; two folds remove the
  leak with two catalogs per run.
- **Trust the band, raise the floor for high complexity only.** Kept as `hard_floor_boost`, but on
  its own it does nothing for hard requests the analyzer calls easy.
