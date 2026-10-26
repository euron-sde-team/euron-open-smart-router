# ADR-0002: Request analysis is deterministic and explainable

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

To route a request the gateway must know what it is: which task (coding, maths, extraction,
classification...), how hard, how large, and which capabilities it needs. That analysis runs on
every request, in the request path, before any model is called. It must be fast, it must not
depend on another model being available, and its conclusions are what an operator reads when a
decision looks wrong ("why did this go to the cheap model?").

The options were a learned classifier (embeddings plus a trained head, or a small LLM asked to
label the request) or rules.

## Decision

`routing/analyzer.py` is a pure, rule-based function. Each task has a few named regular
expressions; matches in the user text score 1.0 (plus 0.5 when they also match the first line,
where the instruction usually is), matches only in the system prompt score 0.5, and the highest
scoring task wins with a fixed tie-break order. Complexity starts at a per-task base and moves
with input size, listed requirements, a fixed vocabulary of hard cues, easy wording and tool use,
clamped to 1..5. Every conclusion carries the signals that produced it, and those signals are
stored on the decision row and returned by the route simulator. Callers that know better can
override the task and complexity with `x-smartrouter-task` and `x-smartrouter-complexity`.

The output is an `Analysis` value object. The engine does not care how it was produced, so a
learned classifier can replace the rules later behind the same contract.

## Consequences

**Good**
- Microseconds per request, no network call, no model dependency, identical output on every
  replica and in every test.
- Every decision is explainable in plain words ("coding: code fence", "hard cues: concurrency,
  edge cases"), and a wrong classification can be reproduced in a unit test and fixed.
- The route simulator can show exactly what production would do.

**Bad / caveats**
- Keyword rules are brittle. Measured on held-out benchmark cases, the analyzer judged the
  difficulty band correctly only about 63% of the time. The router compensates by ranking on
  quality expected over both bands, weighted by the measured error rate
  ([ADR-0006](0006-cross-fitted-benchmark-and-expected-quality.md)), rather than trusting the
  band.
- Rules tuned while looking at evaluation cases memorise them; analyzer changes must be judged on
  cases they were not tuned on.
- The vocabulary is English. Prompts in other languages match few or no rules, so they tend to
  be classified as `chat` at base complexity; the header overrides are the workaround.

## Alternatives considered

- **An LLM call to classify each request.** Rejected: adds latency and cost to every request and
  a dependency on the very providers the router is protecting against.
- **Embedding similarity to labelled examples.** Plausible and on the roadmap; needs an embedding
  model on the request path and a labelled set per deployment.
- **No analysis, policy weights only.** Rejected: without task and difficulty, quality floors
  cannot be applied per task and a hard coding request would be ranked like a greeting.
