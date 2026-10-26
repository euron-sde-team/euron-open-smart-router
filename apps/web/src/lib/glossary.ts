/** Plain-words explanations for the router's vocabulary, shown as inline hints. */
export const GLOSSARY = {
  auto: 'Send model "auto" (or "auto:<policy>") and SmartRouter picks the model. Send a model name and it is passed through, never switched.',
  policy:
    "A policy decides how to trade cost, speed and quality: a strategy (weights), a quality floor, and hard limits such as allowed models or a latency ceiling.",
  qualityFloor:
    "The minimum benchmark score a model needs on this kind of task to be eligible. Hard requests (complexity 4 or 5) raise it by the hard-request boost.",
  taskFloors: "A stricter (or looser) floor for one task, such as coding 0.85, overriding the policy floor.",
  complexity:
    "1 (trivial) to 5 (hard), estimated from the request: size, listed requirements and wording. Callers can set it with the x-smartrouter-complexity header.",
  pHard:
    "The analyzer's difficulty estimate is often wrong, so the router ranks models by expected quality: the measured chance the request is truly hard, times the hard-case score, plus the rest times the easy-case score.",
  baseline:
    "What the request would have cost without a router: the policy's baseline model, or the most expensive model able to serve it.",
  savings: "Baseline cost minus actual cost, over successful requests.",
  fallback:
    "When the chosen model fails with a retryable error (connection, timeout, 429, 5xx) the next eligible model is tried, up to the policy's attempt limit. Streams fall back only before the first chunk.",
  circuit:
    "A circuit breaker per model. It opens after repeated failures so traffic skips the model, then lets one probe through after a cooldown.",
  floorRelaxed:
    "No model cleared the quality floor, so the policy allowed the best available models instead, ordered by quality. Recorded so you can raise quality or lower the floor.",
  budgetDowngrade:
    "When TokenShield refuses a request on budget (402), retry only on models cheaper than the refused one. Off: the 402 goes back to the caller.",
  benchmark:
    "Runs every model on a fixed suite of graded cases, then routes the same cases through each policy. Routing is cross-fitted: a case is routed with scores measured on the other half of the suite.",
  qualitySource:
    "benchmark: measured. prior: never measured, so the tier's default. manual: set by an operator; the next benchmark run replaces it.",
  routingOverhead: "Time spent analysing the request and ranking models, before any upstream call.",
  simulated:
    "A simulated endpoint (the bundled mock) may receive the benchmark's answer key so it can play a model of known skill. Real endpoints never do.",
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;
