"use client";

import { ChartLegend } from "@/components/charts/chart-tooltip";
import { Chip } from "@/components/ui/chip";
import type { BenchmarkSummary } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatRatio, formatUsd } from "@/lib/format";

interface Row {
  name: string;
  kind: "baseline" | "policy" | "model";
  quality: number;
  cost: number;
  costShare: number;
}

const QUALITY = "var(--primary)";
const COST = "var(--chart-baseline)";

function Bar({ value, max, color, label, title }: { value: number; max: number; color: string; label: string; title: string }) {
  const width = max > 0 ? Math.max(value > 0 ? 1 : 0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="flex items-center gap-2" title={title}>
      <div className="h-2.5 flex-1 rounded-full bg-muted">
        <div className="h-full rounded-full" style={{ width: `${width}%`, background: color }} />
      </div>
      <span className="w-14 shrink-0 text-right text-[12px] font-medium tabular">{label}</span>
    </div>
  );
}

/**
 * Every model and policy from one run as aligned small multiples: quality on one shared scale,
 * cost as a share of the baseline on another. The best rows are long on the left bar and short
 * on the right. Values are printed, so nothing depends on reading a bar length or a color.
 */
export default function FrontierChart({ summary }: { summary: BenchmarkSummary }) {
  const base = summary.baseline ? summary.models[summary.baseline] : undefined;
  const baseCost = base?.cost_usd ?? Math.max(...Object.values(summary.models).map((m) => m.cost_usd), 0);
  const share = (c: number) => (baseCost > 0 ? c / baseCost : 0);
  const policies: Row[] = Object.entries(summary.policies)
    .map(([name, p]) => ({ name: `auto:${name}`, kind: "policy" as const, quality: p.quality, cost: p.cost_usd, costShare: share(p.cost_usd) }))
    .sort((a, b) => a.costShare - b.costShare);
  const models: Row[] = Object.entries(summary.models)
    .filter(([name]) => name !== summary.baseline)
    .map(([name, m]) => ({ name, kind: "model" as const, quality: m.quality, cost: m.cost_usd, costShare: share(m.cost_usd) }))
    .sort((a, b) => b.quality - a.quality);
  const rows: Row[] = [
    ...(base && summary.baseline ? [{ name: summary.baseline, kind: "baseline" as const, quality: base.quality, cost: base.cost_usd, costShare: 1 }] : []),
    ...policies,
    ...models,
  ];
  const maxShare = Math.max(1, ...rows.map((r) => r.costShare));

  return (
    <div className="flex flex-col gap-3">
      <ChartLegend
        items={[
          { label: "Quality: share of graded cases passed", color: QUALITY },
          { label: "Cost: share of the baseline's cost", color: COST },
        ]}
      />
      <div role="table" aria-label="Quality and cost for every model and policy" className="text-[13px]">
        <div role="row" className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-4 pb-2 text-[11.5px] font-medium text-muted-foreground">
          <span role="columnheader">Target</span>
          <span role="columnheader">Quality</span>
          <span role="columnheader">Cost vs baseline</span>
        </div>
        {rows.map((r, i) => (
          <div
            role="row"
            key={r.name}
            className={cn(
              "grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-4 py-1.5",
              i > 0 && rows[i - 1]?.kind !== r.kind && "mt-2 border-t border-hairline pt-3",
            )}
          >
            <span role="cell" className="flex min-w-0 items-center gap-2">
              <span className={cn("truncate font-mono text-[12.5px]", r.kind === "policy" ? "font-semibold" : "text-muted-foreground")}>{r.name}</span>
              {r.kind === "baseline" && <Chip tone="neutral">Baseline</Chip>}
            </span>
            <span role="cell">
              <Bar value={r.quality} max={1} color={QUALITY} label={formatRatio(r.quality)} title={`${r.name}: ${formatRatio(r.quality)} of cases passed`} />
            </span>
            <span role="cell">
              <Bar
                value={r.costShare}
                max={maxShare}
                color={COST}
                label={formatRatio(r.costShare, 0)}
                title={`${r.name}: ${formatUsd(r.cost)} for the suite, ${formatRatio(r.costShare)} of the baseline`}
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
