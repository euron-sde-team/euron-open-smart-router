"use client";

import { PieChartIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Skeleton } from "@/components/ui/skeleton";
import type { BreakdownBy, BreakdownRow, RangeKey } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { useModels } from "@/features/models/api";
import { formatLatency, formatNumber, formatRatio, formatUsd } from "@/lib/format";
import { modelColors, taskLabel } from "@/lib/models";

import { useBreakdown } from "../api";

const TABS: { by: BreakdownBy; label: string }[] = [
  { by: "model", label: "Model" },
  { by: "task", label: "Task" },
  { by: "policy", label: "Policy" },
  { by: "project", label: "Project" },
];

/**
 * Traffic share per row (bar length) with routed cost and saving beside it. By model, each bar
 * wears the model's identity color; other dimensions use the single cost hue.
 */
export function BreakdownList({ rows, by, linkProjects = true }: { rows: BreakdownRow[]; by: BreakdownBy; linkProjects?: boolean }) {
  const models = useModels();
  const color = modelColors((models.data ?? []).map((m) => m.alias));
  return (
    <ul className="space-y-3.5">
      {rows.slice(0, 8).map((r) => {
        const width = Math.max(1.5, r.share * 100);
        const saved = r.baseline_cost_usd > 0 ? r.savings_usd / r.baseline_cost_usd : 0;
        const label =
          by === "project" && linkProjects ? (
            <Link href={`/projects/${r.key}`} className="truncate font-medium text-foreground hover:text-primary hover:underline">
              {r.label}
            </Link>
          ) : (
            <span className={cn("truncate font-medium text-foreground", (by === "model" || by === "policy") && "font-mono text-[12.5px]")}>
              {by === "task" ? taskLabel(r.label) : r.label}
            </span>
          );
        return (
          <li key={r.key} className="group">
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <div className="flex min-w-0 items-baseline gap-2">
                {label}
                <span className="shrink-0 text-[11.5px] text-subtle tabular">{formatNumber(r.requests)} req</span>
              </div>
              <div className="flex shrink-0 items-baseline gap-2 tabular">
                <span className="font-semibold text-foreground">{formatUsd(r.cost_usd)}</span>
                <span className="w-16 text-right text-[11.5px] text-savings">{saved > 0.005 ? `${formatRatio(saved, 0)} saved` : ""}</span>
                <span className="w-9 text-right text-[11.5px] text-subtle">{formatRatio(r.share, 0)}</span>
              </div>
            </div>
            <div className="mt-1.5 h-1.5 w-full rounded-full bg-muted" title={`${formatRatio(r.share)} of requests, ${formatUsd(r.savings_usd)} saved, ${formatLatency(r.avg_latency_ms)} average`}>
              <div
                className="h-full rounded-full transition-[width] duration-500"
                style={{ width: `${width}%`, background: by === "model" ? color(r.key) : "var(--chart-cost)" }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function BreakdownPanel({ range, projectId, tabs = TABS }: { range: RangeKey; projectId?: string; tabs?: typeof TABS }) {
  const [by, setBy] = React.useState<BreakdownBy>(tabs[0]?.by ?? "model");
  const q = useBreakdown(by, range, projectId);
  return (
    <Surface className="flex h-full flex-col">
      <SurfaceHeader
        title="Where traffic goes"
        description="Share of requests, routed cost and saving"
        action={
          <div role="tablist" aria-label="Breakdown dimension" className="inline-flex gap-0.5 rounded-lg bg-muted p-0.5">
            {tabs.map((t) => (
              <button
                key={t.by}
                role="tab"
                type="button"
                aria-selected={by === t.by}
                onClick={() => setBy(t.by)}
                className={cn(
                  "h-7 rounded-md px-2.5 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
                  by === t.by && "bg-card text-foreground shadow-[var(--elev-1)] dark:bg-accent",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
        }
      />
      <div className="flex-1 px-5 pb-5">
        {q.isPending ? (
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className="h-3.5 w-2/3" />
                <Skeleton className="h-1.5 w-full" />
              </div>
            ))}
          </div>
        ) : q.isError && !q.data ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
        ) : !q.data?.length ? (
          <EmptyState compact icon={PieChartIcon} title="Nothing to break down yet" description="Once requests flow, they split by model, task, policy and project here." />
        ) : (
          <BreakdownList rows={q.data} by={by} />
        )}
      </div>
    </Surface>
  );
}
