/**
 * Model identity and vocabulary shared by every screen. A model's chart color follows the
 * model, never its rank: it is the model's position in the alphabetically sorted catalog,
 * so filtering a chart never repaints the models that remain.
 */
import type { Tier } from "./api/types";

export const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"] as const;
const OTHER = "var(--chart-baseline)";

export function modelColors(aliases: readonly string[]): (alias: string) => string {
  const order = [...new Set(aliases)].sort();
  return (alias) => {
    const i = order.indexOf(alias);
    return i >= 0 && i < SERIES.length ? `var(${SERIES[i]})` : OTHER;
  };
}

export const TIER_LABEL: Record<Tier, string> = {
  advanced: "Advanced",
  fast: "Fast",
  "low-cost": "Low cost",
  local: "Local",
};

export const TIER_HINT: Record<Tier, string> = {
  advanced: "Strongest models; the usual baseline.",
  fast: "Quick answers at a low price.",
  "low-cost": "Cheapest hosted models.",
  local: "Self-hosted: free per token.",
};

export const TASKS = [
  "classification",
  "extraction",
  "summarization",
  "translation",
  "math",
  "reasoning",
  "coding",
  "chat",
] as const;
export type Task = (typeof TASKS)[number];

export function taskLabel(task: string | null | undefined): string {
  if (!task) return "";
  return task.charAt(0).toUpperCase() + task.slice(1);
}

export const STRATEGY_LABEL: Record<string, string> = {
  balanced: "Balanced",
  cost: "Cost first",
  latency: "Latency first",
  quality: "Quality first",
};

export const KIND_LABEL: Record<string, string> = {
  openai: "OpenAI-compatible",
  anthropic: "Anthropic API",
  tokenshield: "TokenShield",
};

/** USD per 1M tokens as entered: $0.05, $3, $15. */
export function perMillion(value: number | string | null | undefined): string {
  if (value == null) return "n/a";
  const n = Number(value);
  if (!Number.isFinite(n)) return "n/a";
  if (n === 0) return "Free";
  if (n < 1) return `$${n.toFixed(Number.isInteger(Math.round(n * 1e6) / 1e4) ? 2 : 3)}`;
  return `$${n.toFixed(n % 1 ? 2 : 0)}`;
}

/** Nano-dollars (the engine's integer money) as USD. */
export function nusdToUsd(nusd: number | null | undefined): number {
  return (nusd ?? 0) / 1e9;
}

/** A 0..1 quality score as two decimals. */
export function formatQuality(score: number | null | undefined): string {
  if (score == null || !Number.isFinite(score)) return "n/a";
  return score.toFixed(2);
}
