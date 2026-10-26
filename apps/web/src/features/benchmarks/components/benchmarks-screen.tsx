"use client";

import { CheckCircle2Icon, CircleDashedIcon, DownloadIcon, GaugeIcon, Loader2Icon, PiggyBankIcon, PlayIcon, ScaleIcon, TargetIcon, XCircleIcon } from "lucide-react";
import dynamic from "next/dynamic";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Surface, SurfaceHeader } from "@/components/surface";
import { TableSkeleton } from "@/components/table-skeleton";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { usePermissions } from "@/features/auth/api";
import { useModels } from "@/features/models/api";
import { errorMessage } from "@/lib/api/errors";
import type { BenchmarkRun, BenchmarkSummary } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatDateTime, formatLatency, formatRatio, formatRelative, formatUsd } from "@/lib/format";
import { modelColors } from "@/lib/models";

import { useBenchmarkRuns, useDownloadCases, useStartBenchmark, useSuite } from "../api";

const FrontierChart = dynamic(() => import("./frontier-chart"), { ssr: false, loading: () => <Skeleton className="h-[320px] rounded-xl" /> });

function points(delta: number | undefined): string {
  if (delta == null) return "n/a";
  const pts = delta * 100;
  return `${pts >= 0 ? "+" : ""}${pts.toFixed(1)} pts`;
}

function Headline({ icon: Icon, label, value, sub, tone }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string; sub: string; tone?: "savings" | "primary" }) {
  return (
    <div className="surface flex flex-col gap-2 p-4">
      <div className="flex items-center gap-2 text-[12.5px] font-medium text-muted-foreground">
        <span className={cn("flex size-7 items-center justify-center rounded-lg", tone === "savings" ? "bg-savings-soft text-savings" : "bg-primary-soft text-primary")}>
          <Icon className="size-3.5" />
        </span>
        {label}
      </div>
      <div className={cn("text-[24px] font-semibold tracking-[-0.02em] tabular", tone === "savings" && "text-savings")}>{value}</div>
      <div className="text-[12px] text-muted-foreground">{sub}</div>
    </div>
  );
}

/** Model mix of one policy: a stacked bar plus a labelled legend (light slots need labels). */
function Mix({ mix, color }: { mix: Record<string, number>; color: (a: string) => string }) {
  const total = Object.values(mix).reduce((a, b) => a + b, 0) || 1;
  const entries = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  return (
    <div className="min-w-44">
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-full" role="img" aria-label={entries.map(([m, n]) => `${m} ${Math.round((n / total) * 100)}%`).join(", ")}>
        {entries.map(([m, n]) => (
          <div key={m} style={{ width: `${(n / total) * 100}%`, background: color(m) }} title={`${m}: ${n} cases`} />
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
        {entries.slice(0, 3).map(([m, n]) => (
          <span key={m} className="flex items-center gap-1">
            <span aria-hidden className="size-1.5 rounded-full" style={{ background: color(m) }} />
            <span className="font-mono">{m}</span> {Math.round((n / total) * 100)}%
          </span>
        ))}
        {entries.length > 3 && <span>+{entries.length - 3} more</span>}
      </div>
    </div>
  );
}

/** Every graded answer of the run, from object storage: audit the grading yourself. */
function DownloadCases({ runId, available }: { runId: string; available: boolean }) {
  const download = useDownloadCases();
  if (!available) return null;
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={download.isPending}
      onClick={() => download.mutate(runId, { onError: (e) => toast.error(errorMessage(e)) })}
      title="Gzipped JSON lines: one row per case per model and policy, with the answer and grade"
    >
      <DownloadIcon />
      {download.isPending ? "Preparing..." : "Download every answer"}
    </Button>
  );
}

function RunStatus({ run }: { run: BenchmarkRun }) {
  switch (run.status) {
    case "done":
      return (
        <Chip tone="success">
          <CheckCircle2Icon />
          Done
        </Chip>
      );
    case "failed":
      return (
        <Chip tone="danger" title={run.error ?? undefined}>
          <XCircleIcon />
          Failed
        </Chip>
      );
    case "running":
      return (
        <Chip tone="primary">
          <Loader2Icon className="animate-spin" />
          Running
        </Chip>
      );
    default:
      return (
        <Chip tone="neutral">
          <CircleDashedIcon />
          Queued
        </Chip>
      );
  }
}

function Results({ run, color }: { run: BenchmarkRun; color: (a: string) => string }) {
  const s = run.summary as unknown as BenchmarkSummary;
  const policies = Object.entries(s.policies ?? {});
  const baseline = s.baseline ? s.models[s.baseline] : undefined;
  // The best saving among policies that stay within 3 points of the baseline.
  const safe = policies.filter(([, p]) => (p.quality_delta ?? -1) >= -0.03).sort((a, b) => (b[1].savings_pct ?? 0) - (a[1].savings_pct ?? 0))[0];
  const best = policies.sort((a, b) => b[1].quality - a[1].quality)[0];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Headline icon={TargetIcon} label="Baseline quality" value={baseline ? formatRatio(baseline.quality) : "n/a"} sub={`${s.baseline ?? "No baseline"} on all ${s.cases} cases`} tone="primary" />
        <Headline
          icon={PiggyBankIcon}
          label="Saved within 3 points"
          value={safe ? `${(safe[1].savings_pct ?? 0).toFixed(1)}%` : "None"}
          sub={safe ? `auto:${safe[0]} at ${points(safe[1].quality_delta)} quality` : "No policy stayed within 3 points"}
          tone="savings"
        />
        <Headline icon={ScaleIcon} label="Best routed quality" value={best ? formatRatio(best[1].quality) : "n/a"} sub={best ? `auto:${best[0]}, ${points(best[1].quality_delta)} vs baseline` : ""} tone="primary" />
        <Headline icon={GaugeIcon} label="Suite" value={`${s.cases} cases`} sub={`Version ${run.suite_version}, finished ${formatRelative(run.finished_at)}`} tone="primary" />
      </div>
      <Surface>
        <SurfaceHeader
          title="Quality and cost, side by side"
          titleExtra={<Hint term="benchmark" />}
          description="Long on the left, short on the right is the goal. Policies are cross-fitted: each case is routed with quality measured on the other half of the suite."
        />
        <div className="px-5 pb-5">
          <FrontierChart summary={s} />
        </div>
      </Surface>
      <Surface className="overflow-hidden">
        <SurfaceHeader title="Policies" description={`Compared with sending every case to ${s.baseline ?? "the baseline"}.`} />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Policy</TableHead>
              <TableHead className="text-right">Quality</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Saved</TableHead>
              <TableHead className="hidden text-right md:table-cell">Latency</TableHead>
              <TableHead className="hidden lg:table-cell">Models chosen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(s.policies).map(([name, p]) => (
              <TableRow key={name}>
                <TableCell className="font-mono text-[12.5px] font-medium">auto:{name}</TableCell>
                <TableCell className="text-right tabular">{formatRatio(p.quality)}</TableCell>
                <TableCell className={cn("text-right tabular", (p.quality_delta ?? 0) >= -0.03 ? "text-savings" : "text-warning")}>{points(p.quality_delta)}</TableCell>
                <TableCell className="text-right tabular">{formatUsd(p.cost_usd)}</TableCell>
                <TableCell className="text-right font-medium text-savings tabular">{p.savings_pct != null ? `${p.savings_pct.toFixed(1)}%` : "n/a"}</TableCell>
                <TableCell className="hidden text-right text-muted-foreground tabular md:table-cell">{formatLatency(p.latency_ms)}</TableCell>
                <TableCell className="hidden lg:table-cell">
                  <Mix mix={p.model_mix} color={color} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Surface>
      <Surface className="overflow-hidden">
        <SurfaceHeader
          title="Models"
          description="Each model answered every case. These scores became the quality table the router ranks by."
          action={<DownloadCases runId={run.id} available={!!(run.summary as { results_file?: unknown }).results_file} />}
        />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Quality</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Latency</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(s.models)
              .sort((a, b) => b[1].quality - a[1].quality)
              .map(([name, m]) => (
                <TableRow key={name}>
                  <TableCell>
                    <span className="flex items-center gap-2 font-mono text-[12.5px] font-medium">
                      <span aria-hidden className="size-2.5 rounded-full" style={{ background: color(name) }} />
                      {name}
                      {name === s.baseline && <Chip tone="neutral">Baseline</Chip>}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular">{formatRatio(m.quality)}</TableCell>
                  <TableCell className="text-right tabular">{formatUsd(m.cost_usd)}</TableCell>
                  <TableCell className="text-right text-muted-foreground tabular">{formatLatency(m.latency_ms)}</TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
      </Surface>
    </div>
  );
}

export function BenchmarksScreen() {
  const { canWrite } = usePermissions();
  const runs = useBenchmarkRuns();
  const suite = useSuite();
  const models = useModels();
  const start = useStartBenchmark();
  const color = modelColors((models.data ?? []).map((m) => m.alias));
  const [selected, setSelected] = React.useState<string | null>(null);
  const list = runs.data ?? [];
  const running = list.find((r) => r.status === "queued" || r.status === "running");
  const shown = list.find((r) => r.id === selected) ?? list.find((r) => r.status === "done");

  function run() {
    start.mutate(
      { models: null, policies: null },
      {
        onSuccess: () => toast.success("Benchmark queued. Every model, then every policy, runs the suite; results appear here."),
        onError: (e) => toast.error(errorMessage(e)),
      },
    );
  }

  let body: React.ReactNode;
  if (runs.isPending)
    body = (
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 rounded-[14px]" />
          ))}
        </div>
        <Skeleton className="h-96 rounded-[14px]" />
      </div>
    );
  else if (runs.isError && !runs.data) body = <Surface><ErrorState error={runs.error} onRetry={() => void runs.refetch()} /></Surface>;
  else if (!shown)
    body = (
      <Surface>
        <EmptyState
          icon={GaugeIcon}
          title={running ? "The first benchmark is running" : "No benchmark results yet"}
          description={`A run sends all ${suite.data?.length ?? ""} graded cases to every model, then routes them through every policy, and shows what routing saves and what it costs in quality. It also fills the quality table the router uses.`}
          action={
            canWrite && !running ? (
              <Button size="sm" onClick={run} disabled={start.isPending}>
                <PlayIcon />
                Run the benchmark
              </Button>
            ) : undefined
          }
        />
      </Surface>
    );
  else body = <Results run={shown} color={color} />;

  return (
    <>
      <PageHeader
        title="Benchmarks"
        description="Reproducible proof of what routing saves. Same suite, every model, every policy; the numbers are graded answers, not estimates."
        actions={
          canWrite && (
            <Button onClick={run} disabled={start.isPending || !!running}>
              {running ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
              {running ? (running.status === "running" ? "Running..." : "Queued...") : "Run benchmark"}
            </Button>
          )
        }
      />
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">{body}</div>
        <Surface className="h-fit overflow-hidden">
          <SurfaceHeader title="Runs" description="Select a run to see its results." />
          {runs.isPending ? (
            <div className="px-5 pb-5"><TableSkeleton rows={4} cols={2} /></div>
          ) : list.length ? (
            <ul className="pb-2">
              {list.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(r.id)}
                    disabled={r.status !== "done"}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 px-5 py-2.5 text-left text-[12.5px] transition-colors hover:bg-muted/60 disabled:cursor-default disabled:hover:bg-transparent",
                      shown?.id === r.id && "bg-primary-soft/60",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block font-medium">{formatDateTime(r.created_at)}</span>
                      <span className="block truncate text-[11.5px] text-subtle">
                        {r.status === "failed" ? r.error : `Suite ${r.suite_version} · ${r.models.length || "all"} models · ${r.policies.length} policies`}
                      </span>
                    </span>
                    <RunStatus run={r} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 pb-5 text-[12.5px] text-muted-foreground">No runs yet.</p>
          )}
        </Surface>
      </div>
    </>
  );
}
