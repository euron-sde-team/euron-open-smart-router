/**
 * One mapping from decision, circuit and attempt states to a chip tone, label and explanation,
 * so a status reads the same on the decision log, the drawer, the simulator and the overview.
 */

export type Tone = "success" | "savings" | "info" | "primary" | "warning" | "danger" | "neutral";

export interface ChipSpec {
  label: string;
  tone: Tone;
  hint: string;
}

const STATUS: Record<string, ChipSpec> = {
  SUCCESS: { label: "Success", tone: "success", hint: "A model answered. Cost and savings were recorded." },
  REJECTED: {
    label: "Rejected",
    tone: "warning",
    hint: "Refused before any model was called: an unknown policy, a rate limit, or no model met the policy.",
  },
  ERROR: {
    label: "Error",
    tone: "danger",
    hint: "Every model the policy allowed failed, or fallback is off and the chosen model failed.",
  },
};

const CIRCUIT: Record<string, ChipSpec> = {
  closed: { label: "Healthy", tone: "success", hint: "Circuit closed: the model receives traffic normally." },
  open: {
    label: "Circuit open",
    tone: "danger",
    hint: "Too many recent failures. The router skips this model until the cooldown passes.",
  },
  half_open: {
    label: "Probing",
    tone: "warning",
    hint: "Cooldown over: one probe request is allowed through. Success closes the circuit, failure reopens it.",
  },
};

const ATTEMPT: Record<string, ChipSpec> = {
  ok: { label: "Answered", tone: "success", hint: "This model answered the request." },
  error: { label: "Failed", tone: "danger", hint: "Connection error or 5xx from the provider: retryable, so the router moved on." },
  timeout: { label: "Timed out", tone: "danger", hint: "No answer within the endpoint timeout: retryable." },
  throttled: { label: "Throttled", tone: "warning", hint: "The provider returned 429: retryable." },
  budget: {
    label: "Budget refused",
    tone: "warning",
    hint: "TokenShield refused on budget (402). Only cheaper models are tried, and only if the policy allows downgrades.",
  },
  client: { label: "Rejected", tone: "warning", hint: "The provider rejected the request itself (4xx). Not retried: another model would reject it too." },
  skipped: { label: "Skipped", tone: "neutral", hint: "Not attempted: see the detail." },
};

function fallback(value: string): ChipSpec {
  const label = value ? value.charAt(0).toUpperCase() + value.slice(1).toLowerCase().replace(/_/g, " ") : "Unknown";
  return { label, tone: "neutral", hint: "" };
}

export function statusChip(status: string | null | undefined): ChipSpec {
  const key = (status ?? "").toUpperCase();
  return STATUS[key] ?? fallback(key);
}

export function circuitChip(state: string | null | undefined): ChipSpec {
  return CIRCUIT[state ?? "closed"] ?? fallback(state ?? "");
}

export function attemptChip(outcome: string | null | undefined): ChipSpec {
  return ATTEMPT[outcome ?? ""] ?? fallback(outcome ?? "");
}

export const DECISION_STATUSES = Object.keys(STATUS);

/** Error codes the gateway writes into decisions, in plain words. */
const ERROR_LABEL: Record<string, string> = {
  all_models_failed: "Every model failed",
  no_eligible_model: "No model met the policy",
  model_not_found: "Unknown model",
  policy_not_found: "Unknown policy",
  budget_exceeded: "TokenShield budget",
  rpm_limit: "Requests per minute",
  concurrency_limit: "Concurrency limit",
  upstream_timeout: "Upstream timeout",
  upstream_error: "Upstream error",
  invalid_request: "Invalid request",
};

export function errorLabel(code: string | null | undefined): string {
  if (!code) return "";
  return ERROR_LABEL[code] ?? code.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** Severity of an alert as a tone. */
export function severityTone(severity: string | null | undefined): Tone {
  switch ((severity ?? "").toLowerCase()) {
    case "critical":
      return "danger";
    case "warning":
      return "warning";
    case "info":
      return "info";
    default:
      return "neutral";
  }
}

/** Tone for a quality score against a floor (or the usual 0.7 when no floor applies). */
export function qualityTone(score: number, floor = 0.7): Tone {
  if (score >= Math.min(1, floor + 0.1)) return "success";
  if (score >= floor) return "info";
  if (score >= floor - 0.15) return "warning";
  return "danger";
}
