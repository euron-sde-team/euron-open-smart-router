"use client";

import { ArrowRightIcon, CheckIcon, ShieldIcon, ThumbsDownIcon, ThumbsUpIcon, XIcon } from "lucide-react";
import * as React from "react";

import { CopyButton } from "@/components/copy-button";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { Chip } from "@/components/ui/chip";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useModels } from "@/features/models/api";
import type { Attempt, Candidate, DecisionDetail } from "@/lib/api/types";
import { attemptChip, errorLabel, statusChip } from "@/lib/chips";
import { cn } from "@/lib/cn";
import { formatDateTime, formatLatency, formatNumber, formatRatio, formatUsd } from "@/lib/format";
import { formatQuality, modelColors, nusdToUsd, STRATEGY_LABEL, taskLabel } from "@/lib/models";

import { useDecision } from "../api";

export const AXES = [
  { key: "cost", label: "cost", color: "var(--chart-cost)" },
  { key: "latency", label: "latency", color: "var(--series-4)" },
  { key: "quality", label: "quality", color: "var(--chart-saved)" },
] as const;

function Section({ title, hint, children, className }: { title: string; hint?: React.ComponentProps<typeof Hint>["term"]; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-2.5", className)}>
      <h3 className="flex items-center gap-1 text-[12px] font-semibold tracking-[0.04em] text-subtle uppercase">
        {title}
        {hint && <Hint term={hint} />}
      </h3>
      {children}
    </section>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "savings" }) {
  return (
    <div className="rounded-xl bg-muted/60 px-3 py-2.5">
      <div className="text-[11.5px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-[15px] font-semibold tabular", tone === "savings" && "text-savings")}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11.5px] text-subtle">{sub}</div>}
    </div>
  );
}

/** One candidate: rank, model, its weighted score split by axis, quality and the reason. */
export function CandidateRow({ c, rank, max, color, final }: { c: Candidate; rank: number; max: number; color: string; final: boolean }) {
  const total = Object.values(c.components ?? {}).reduce((a, b) => a + (b ?? 0), 0);
  return (
    <li className={cn("rounded-xl px-3 py-2.5", final ? "bg-primary-soft/70" : "bg-muted/40", !c.eligible && "opacity-70")}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="w-4 text-right text-[11.5px] text-subtle tabular">{c.eligible ? rank : ""}</span>
          <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
          <span className="font-mono text-[12.5px] font-medium">{c.model}</span>
          {final && (
            <Chip tone="primary">
              <CheckIcon />
              Answered
            </Chip>
          )}
          {!c.eligible && <Chip tone="neutral">Filtered</Chip>}
        </div>
        <span className="shrink-0 text-[12px] text-muted-foreground tabular">
          {c.eligible ? `score ${c.score.toFixed(3)}` : ""}
        </span>
      </div>
      {c.eligible && total > 0 && (
        <div className="mt-2 ml-6">
          <div
            className="flex h-1.5 gap-0.5 overflow-hidden rounded-full"
            role="img"
            aria-label={AXES.map((a) => `${a.label} ${(c.components[a.key] ?? 0).toFixed(3)}`).join(", ")}
            style={{ width: `${Math.max(8, Math.min(100, (total / max) * 100))}%` }}
          >
            {AXES.map((a) => {
              const v = c.components[a.key] ?? 0;
              return v > 0 ? <div key={a.key} style={{ width: `${(v / total) * 100}%`, background: a.color }} /> : null;
            })}
          </div>
        </div>
      )}
      <div className="mt-1.5 ml-6 grid grid-cols-3 gap-2 text-[11.5px] text-muted-foreground tabular">
        <span title={`Source: ${c.quality_source}`}>
          quality <span className="font-medium text-foreground">{formatQuality(c.quality)}</span>
        </span>
        <span>
          est. <span className="font-medium text-foreground">{formatUsd(nusdToUsd(c.estimated_cost_nusd))}</span>
        </span>
        <span>
          ~<span className="font-medium text-foreground">{formatLatency(c.latency_ms)}</span>
        </span>
      </div>
      <p className="mt-1 ml-6 text-[12px] text-muted-foreground">{c.reason}</p>
    </li>
  );
}

function Attempts({ chain }: { chain: Attempt[] }) {
  if (!chain.length) return <p className="text-[12.5px] text-muted-foreground">No model was called.</p>;
  return (
    <ol className="relative space-y-2 pl-5">
      <span aria-hidden className="absolute top-2 bottom-2 left-[5px] w-px bg-border" />
      {chain.map((a, i) => {
        const chip = attemptChip(a.outcome);
        return (
          <li key={`${a.model}-${i}`} className="relative">
            <span
              aria-hidden
              className={cn(
                "absolute top-1.5 -left-5 flex size-[11px] items-center justify-center rounded-full ring-2 ring-card",
                a.outcome === "ok" ? "bg-savings" : a.outcome === "skipped" ? "bg-faint" : "bg-danger",
              )}
            />
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[12.5px] font-medium">{a.model}</span>
              <Chip tone={chip.tone} title={chip.hint}>
                {chip.label}
              </Chip>
              {a.status != null && <span className="text-[11.5px] text-subtle tabular">HTTP {a.status}</span>}
              {a.ms > 0 && <span className="text-[11.5px] text-subtle tabular">{formatLatency(a.ms)}</span>}
            </div>
            {a.detail && <p className="mt-0.5 text-[12px] text-muted-foreground">{a.detail}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function Body({ d }: { d: DecisionDetail }) {
  const models = useModels();
  const color = modelColors((models.data ?? []).map((m) => m.alias).concat((d.candidates as unknown as Candidate[]).map((c) => c.model)));
  const candidates = d.candidates as unknown as Candidate[];
  const chain = d.fallback_chain as unknown as Attempt[];
  const eligible = candidates.filter((c) => c.eligible);
  const filtered = candidates.filter((c) => !c.eligible);
  const max = Math.max(0.0001, ...eligible.map((c) => c.score));
  const saved = d.status === "SUCCESS" ? d.baseline_cost_usd - d.actual_cost_usd : 0;
  const savedPct = d.baseline_cost_usd > 0 ? saved / d.baseline_cost_usd : null;
  const fellBack = d.selected_model && d.final_model && d.selected_model !== d.final_model;

  return (
    <div className="space-y-6">
      <Section title="Why this model">
        <div className="rounded-xl bg-muted/60 p-3.5">
          <div className="flex flex-wrap items-center gap-2 font-mono text-[13px] font-semibold">
            {d.selected_model ?? "No model"}
            {fellBack && (
              <>
                <ArrowRightIcon className="size-3.5 text-subtle" aria-label="fell back to" />
                {d.final_model}
              </>
            )}
            {d.floor_relaxed && (
              <Chip tone="warning" title="No model cleared the quality floor">
                Floor relaxed
              </Chip>
            )}
          </div>
          <p className="mt-1.5 text-[13px] text-muted-foreground">{d.reason || errorLabel(d.error_code)}</p>
          {d.error_code && d.status !== "SUCCESS" && (
            <p className="mt-1.5 text-[12.5px] font-medium text-danger">{errorLabel(d.error_code)}</p>
          )}
        </div>
      </Section>

      <Section title="Cost" hint="baseline">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat
            label="Actual"
            value={formatUsd(d.actual_cost_usd)}
            sub={
              d.cost_source === "tokenshield" ? (
                <span className="inline-flex items-center gap-1">
                  <ShieldIcon className="size-3" /> from TokenShield
                </span>
              ) : (
                d.cost_source
              )
            }
          />
          <Stat label="Baseline" value={formatUsd(d.baseline_cost_usd)} sub={d.baseline_model ?? "n/a"} />
          <Stat label="Saved" value={formatUsd(saved)} sub={savedPct != null ? formatRatio(savedPct) : undefined} tone={saved > 0 ? "savings" : undefined} />
          <Stat label="Estimated" value={formatUsd(d.estimated_cost_usd)} sub="before the call" />
        </div>
      </Section>

      <Section title="What the request looked like" hint="complexity">
        <div className="flex flex-wrap gap-1.5">
          <Chip tone="primary">{taskLabel(d.task)}</Chip>
          <Chip tone={d.complexity >= 4 ? "warning" : "neutral"}>Complexity {d.complexity} of 5</Chip>
          <Chip tone="neutral">~{formatNumber(d.input_tokens_est)} input tokens</Chip>
        </div>
        <ul className="space-y-1 text-[12.5px] text-muted-foreground">
          {d.signals.map((s) => (
            <li key={s} className="flex gap-2">
              <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-faint" />
              {s}
            </li>
          ))}
        </ul>
      </Section>

      <Section title={`Candidates (${eligible.length} eligible of ${candidates.length})`} hint="pHard">
        <div className="flex flex-wrap gap-3 text-[11.5px] text-muted-foreground">
          {AXES.map((a) => (
            <span key={a.key} className="flex items-center gap-1">
              <span aria-hidden className="h-1.5 w-3 rounded-full" style={{ background: a.color }} />
              {a.label} share of score
            </span>
          ))}
        </div>
        <ol className="space-y-1.5">
          {eligible.map((c, i) => (
            <CandidateRow key={c.model} c={c} rank={i + 1} max={max} color={color(c.model)} final={c.model === d.final_model} />
          ))}
          {filtered.map((c) => (
            <CandidateRow key={c.model} c={c} rank={0} max={max} color={color(c.model)} final={false} />
          ))}
        </ol>
      </Section>

      <Section title={`Attempts (${d.attempts})`} hint="fallback">
        <Attempts chain={chain} />
      </Section>

      <Section title="Details">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[12.5px]">
          {(
            [
              ["Policy", `${d.policy_name ?? "passthrough"} (${STRATEGY_LABEL[d.strategy] ?? d.strategy})`],
              ["Requested", d.requested_model],
              ["Project", d.project_name ?? "n/a"],
              ["Tokens", `${formatNumber(d.input_tokens)} in, ${formatNumber(d.output_tokens)} out`],
              ["Total latency", formatLatency(d.latency_ms)],
              ["Upstream", formatLatency(d.upstream_ms)],
              ["Routing", `${formatNumber(d.routing_us)} µs`],
              ["Stream", d.stream ? "Yes" : "No"],
              ["Upstream request", d.upstream_request_id ?? "n/a"],
              ["Feedback", d.feedback == null ? "None" : d.feedback > 0 ? "Helpful" : "Not helpful"],
            ] as const
          ).map(([k, v]) => (
            <div key={k} className="min-w-0">
              <dt className="text-[11.5px] text-subtle">{k}</dt>
              <dd className="truncate font-medium" title={String(v)}>
                {k === "Feedback" && d.feedback != null ? (
                  <span className="inline-flex items-center gap-1">
                    {d.feedback > 0 ? <ThumbsUpIcon className="size-3.5 text-savings" /> : <ThumbsDownIcon className="size-3.5 text-danger" />}
                    {v}
                  </span>
                ) : (
                  v
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}

export function DecisionDrawer({ requestId, onClose }: { requestId: string | null; onClose: () => void }) {
  const q = useDecision(requestId);
  const d = q.data;
  const chip = statusChip(d?.status);
  return (
    <Sheet open={!!requestId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2">
            Routing decision
            {d && (
              <Chip tone={chip.tone} title={chip.hint}>
                {d.status === "SUCCESS" ? <CheckIcon /> : <XIcon />}
                {chip.label}
              </Chip>
            )}
          </SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[12px]">{requestId}</span>
            {requestId && <CopyButton value={requestId} label="Copy request id" iconOnly variant="subtle" />}
            {d && <span>· {formatDateTime(d.created_at)}</span>}
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          {q.isPending ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-20 rounded-xl" />
              ))}
            </div>
          ) : q.isError || !d ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          ) : (
            <Body d={d} />
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
