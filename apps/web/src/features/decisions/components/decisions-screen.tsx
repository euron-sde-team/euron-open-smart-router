"use client";

import { ArrowRightIcon, ListTreeIcon, RotateCcwIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Surface } from "@/components/surface";
import { TableSkeleton } from "@/components/table-skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useModels } from "@/features/models/api";
import { usePolicies } from "@/features/policies/api";
import type { Decision, DecisionFilters } from "@/lib/api/types";
import { DECISION_STATUSES, errorLabel, statusChip } from "@/lib/chips";
import { cn } from "@/lib/cn";
import { formatLatency, formatRelative, formatTime, formatUsd } from "@/lib/format";
import { modelColors, TASKS, taskLabel } from "@/lib/models";
import { useUiPrefs } from "@/lib/store";

import { useDecisions } from "../api";
import { DecisionDrawer } from "./decision-drawer";

const ALL = "__all";

function Saved({ d }: { d: Decision }) {
  if (d.status !== "SUCCESS" || d.baseline_cost_usd <= 0) return <span className="text-faint">n/a</span>;
  const pct = 1 - d.actual_cost_usd / d.baseline_cost_usd;
  return (
    <span className={cn("tabular", pct > 0.005 ? "text-savings" : "text-muted-foreground")}>
      {pct > 0.005 ? `${Math.round(pct * 100)}%` : "0%"}
    </span>
  );
}

function ModelCell({ d, color }: { d: Decision; color: (a: string) => string }) {
  if (!d.final_model && !d.selected_model) return <span className="text-faint">none</span>;
  const fellBack = d.selected_model && d.final_model && d.selected_model !== d.final_model;
  return (
    <span className="flex items-center gap-1.5 font-mono text-[12.5px]">
      {fellBack && (
        <>
          <span className="text-faint line-through">{d.selected_model}</span>
          <ArrowRightIcon className="size-3 text-subtle" aria-label="fell back to" />
        </>
      )}
      <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: color(d.final_model ?? d.selected_model ?? "") }} />
      <span className={cn(!d.final_model && "text-danger")}>{d.final_model ?? d.selected_model}</span>
    </span>
  );
}

export function DecisionsScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const open = params.get("id");
  const autoRefresh = useUiPrefs((s) => s.autoRefresh);
  const setAutoRefresh = useUiPrefs((s) => s.setAutoRefresh);
  const policies = usePolicies();
  const models = useModels();
  const color = modelColors((models.data ?? []).map((m) => m.alias));

  const filters: Omit<DecisionFilters, "cursor"> = {
    policy: params.get("policy") ?? undefined,
    task: params.get("task") ?? undefined,
    status: params.get("status") ?? undefined,
    model: params.get("model") ?? undefined,
    project_id: params.get("project") ?? undefined,
    fallback: params.get("fallback") === "1" ? true : undefined,
    q: params.get("q") ?? undefined,
  };
  const [search, setSearch] = React.useState(filters.q ?? "");
  const q = useDecisions(filters);
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = Object.values(filters).some((v) => v !== undefined);

  const setParam = React.useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params.toString());
      if (value == null || value === ALL || value === "") next.delete(key);
      else next.set(key, value);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  React.useEffect(() => {
    const t = setTimeout(() => {
      if ((filters.q ?? "") !== search.trim()) setParam("q", search.trim() || null);
    }, 300);
    return () => clearTimeout(t);
  }, [search, filters.q, setParam]);

  const select = (key: string, label: string, options: { value: string; label: string }[], width = "w-40") => (
    <div className={width}>
      <SimpleSelect aria-label={label} value={params.get(key) ?? ALL} onValueChange={(v) => setParam(key, v)} options={[{ value: ALL, label }, ...options]} />
    </div>
  );

  let body: React.ReactNode;
  if (q.isPending) body = <TableSkeleton rows={10} cols={7} />;
  else if (q.isError && !q.data) body = <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  else if (!rows.length)
    body = filtered ? (
      <EmptyState
        icon={SearchIcon}
        title="No decisions match these filters"
        description="Every gateway request is logged here, so try a wider filter."
        action={
          <Button size="sm" variant="outline" onClick={() => router.replace(pathname)}>
            <RotateCcwIcon />
            Clear filters
          </Button>
        }
      />
    ) : (
      <EmptyState
        icon={ListTreeIcon}
        title="No routing decisions yet"
        description='Every request through the gateway lands here with the reason its model was chosen. Create an API key and send model "auto" to see the first one.'
        action={<Link href="/keys" className={buttonVariants({ size: "sm" })}>Create an API key</Link>}
      />
    );
  else
    body = (
      <>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Task</TableHead>
              <TableHead className="hidden md:table-cell">Policy</TableHead>
              <TableHead>Model</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Cost</TableHead>
              <TableHead className="hidden text-right lg:table-cell">Saved</TableHead>
              <TableHead className="hidden text-right lg:table-cell">Latency</TableHead>
              <TableHead className="text-right">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((d) => {
              const chip = statusChip(d.status);
              return (
                <TableRow
                  key={d.request_id}
                  tabIndex={0}
                  aria-label={`Decision ${d.request_id}`}
                  onClick={() => setParam("id", d.request_id)}
                  onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setParam("id", d.request_id))}
                  className={cn("cursor-pointer", open === d.request_id && "bg-primary-soft/50")}
                >
                  <TableCell className="whitespace-nowrap">
                    <div className="tabular">{formatTime(d.created_at)}</div>
                    <div className="text-[11.5px] text-subtle">{formatRelative(d.created_at)}</div>
                  </TableCell>
                  <TableCell>
                    <div>{taskLabel(d.task)}</div>
                    <div className="text-[11.5px] text-subtle">complexity {d.complexity}</div>
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <span className="font-mono text-[12.5px]">{d.policy_name ?? "passthrough"}</span>
                    {d.project_name && <div className="text-[11.5px] text-subtle">{d.project_name}</div>}
                  </TableCell>
                  <TableCell>
                    <ModelCell d={d} color={color} />
                  </TableCell>
                  <TableCell className="hidden text-right tabular sm:table-cell">{formatUsd(d.actual_cost_usd)}</TableCell>
                  <TableCell className="hidden text-right lg:table-cell">
                    <Saved d={d} />
                  </TableCell>
                  <TableCell className="hidden text-right text-muted-foreground tabular lg:table-cell">{formatLatency(d.latency_ms)}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end gap-1">
                      <Chip tone={chip.tone} title={chip.hint}>
                        {chip.label}
                      </Chip>
                      {d.attempts > 1 && <span className="text-[11px] text-warning">{d.attempts} attempts</span>}
                      {d.error_code && <span className="text-[11px] text-subtle">{errorLabel(d.error_code)}</span>}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between gap-3 px-5 py-3 text-xs text-muted-foreground">
          <span>
            {rows.length} decision{rows.length === 1 ? "" : "s"} shown
          </span>
          {q.hasNextPage && (
            <Button size="sm" variant="outline" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
              {q.isFetchingNextPage ? "Loading..." : "Load older"}
            </Button>
          )}
        </div>
      </>
    );

  return (
    <>
      <PageHeader
        title="Decisions"
        description="Every routed request: what the analyzer saw, which models qualified, which one answered and what it saved. Open one to see the full reasoning."
        actions={
          <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
            Live
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} aria-label="Refresh every 10 seconds" />
          </label>
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-56">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-faint" aria-hidden />
          <Input aria-label="Search by request id" className="pl-8 font-mono text-[12.5px]" placeholder="req_..." value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {select("policy", "All policies", (policies.data ?? []).map((p) => ({ value: p.name, label: p.name })))}
        {select("task", "All tasks", TASKS.map((t) => ({ value: t, label: taskLabel(t) })))}
        {select("model", "All models", (models.data ?? []).map((m) => ({ value: m.alias, label: m.alias })))}
        {select("status", "Any status", DECISION_STATUSES.map((s) => ({ value: s, label: statusChip(s).label })), "w-36")}
        <label className="flex h-9 items-center gap-2 rounded-lg bg-muted/60 px-3 text-[13px] text-muted-foreground">
          <Switch checked={filters.fallback === true} onCheckedChange={(v) => setParam("fallback", v ? "1" : null)} />
          Fallbacks only
        </label>
      </div>
      <Surface className="overflow-hidden">{body}</Surface>
      <DecisionDrawer requestId={open} onClose={() => setParam("id", null)} />
    </>
  );
}
