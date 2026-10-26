"use client";

import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { CHART, ChartLegend, ChartTooltipCard } from "@/components/charts/chart-tooltip";
import type { RangeKey, SeriesPoint } from "@/lib/api/types";
import { formatNumber, formatTokens, formatUsd, formatUsdAxis } from "@/lib/format";
import { formatBucket, formatBucketLong } from "@/lib/range";

export type ActivityMetric = "cost" | "requests";

interface Row {
  t: string;
  cost: number;
  baseline: number;
  ok: number;
  fallbacks: number;
  failed: number;
  requests: number;
}

function toRows(points: SeriesPoint[]): Row[] {
  return points.map((p) => ({
    t: p.t,
    cost: p.cost_usd,
    baseline: p.baseline_cost_usd,
    failed: p.failed,
    fallbacks: p.fallbacks,
    ok: Math.max(0, p.requests - p.failed - p.fallbacks),
    requests: p.requests,
  }));
}

const axisProps = {
  stroke: CHART.axis,
  tick: { fill: CHART.axis, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

/**
 * Routed cost against what the same traffic would have cost on the baseline model (one dollar
 * axis; the gap is the saving), or requests split by outcome (stacked bars). Never dual-axis.
 */
export default function ActivityChart({ points, range, metric }: { points: SeriesPoint[]; range: RangeKey; metric: ActivityMetric }) {
  const rows = React.useMemo(() => toRows(points), [points]);
  const ticks = React.useMemo(() => {
    const n = rows.length;
    if (n <= 8) return rows.map((r) => r.t);
    const step = Math.ceil(n / 7);
    return rows.filter((_, i) => i % step === 0).map((r) => r.t);
  }, [rows]);
  const totals = React.useMemo(
    () =>
      rows.reduce(
        (a, r) => ({ cost: a.cost + r.cost, baseline: a.baseline + r.baseline, ok: a.ok + r.ok, fallbacks: a.fallbacks + r.fallbacks, failed: a.failed + r.failed }),
        { cost: 0, baseline: 0, ok: 0, fallbacks: 0, failed: 0 },
      ),
    [rows],
  );

  return (
    <div className="flex flex-1 flex-col gap-3">
      {metric === "cost" ? (
        <ChartLegend
          items={[
            { label: "Routed cost", color: CHART.cost, value: formatUsd(totals.cost) },
            { label: "Baseline cost", color: CHART.baseline, value: formatUsd(totals.baseline) },
          ]}
        />
      ) : (
        <ChartLegend
          items={[
            { label: "Answered first time", color: CHART.cost, value: formatTokens(totals.ok) },
            { label: "After a fallback", color: CHART.fallback, value: formatTokens(totals.fallbacks) },
            { label: "Failed", color: "var(--danger)", value: formatTokens(totals.failed) },
          ]}
        />
      )}
      <div className="min-h-[260px] w-full flex-1" role="img" aria-label={metric === "cost" ? "Routed cost against baseline cost over time" : "Requests by outcome over time"}>
        <ResponsiveContainer width="100%" height="100%">
          {metric === "cost" ? (
            <AreaChart data={rows} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="fill-cost" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART.cost} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={CHART.cost} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke={CHART.grid} />
              <XAxis dataKey="t" ticks={ticks} tickFormatter={(t: string) => formatBucket(t, range)} {...axisProps} minTickGap={16} />
              <YAxis tickFormatter={formatUsdAxis} width={52} {...axisProps} />
              <Tooltip
                cursor={{ stroke: CHART.axis, strokeDasharray: "3 3" }}
                content={({ active, payload }) => {
                  const row = payload?.[0]?.payload as Row | undefined;
                  if (!active || !row) return null;
                  return (
                    <ChartTooltipCard
                      title={formatBucketLong(row.t, range)}
                      rows={[
                        { label: "Routed", value: formatUsd(row.cost), color: CHART.cost },
                        { label: "Baseline", value: formatUsd(row.baseline), color: CHART.baseline },
                        { label: "Saved", value: formatUsd(Math.max(0, row.baseline - row.cost)), color: CHART.saved },
                      ]}
                      footer={`${formatNumber(row.requests)} requests`}
                    />
                  );
                }}
              />
              <Line type="monotone" dataKey="baseline" stroke={CHART.baseline} strokeWidth={2} strokeDasharray="5 4" dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }} />
              <Area type="monotone" dataKey="cost" stroke={CHART.cost} strokeWidth={2} fill="url(#fill-cost)" activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }} />
            </AreaChart>
          ) : (
            <BarChart data={rows} margin={{ top: 6, right: 4, bottom: 0, left: 0 }} barCategoryGap="18%">
              <CartesianGrid vertical={false} stroke={CHART.grid} />
              <XAxis dataKey="t" ticks={ticks} tickFormatter={(t: string) => formatBucket(t, range)} {...axisProps} minTickGap={16} />
              <YAxis tickFormatter={(v: number) => formatTokens(v)} width={44} {...axisProps} allowDecimals={false} />
              <Tooltip
                cursor={{ fill: "var(--muted)", opacity: 0.6 }}
                content={({ active, payload }) => {
                  const row = payload?.[0]?.payload as Row | undefined;
                  if (!active || !row) return null;
                  return (
                    <ChartTooltipCard
                      title={formatBucketLong(row.t, range)}
                      rows={[
                        { label: "First time", value: formatNumber(row.ok), color: CHART.cost },
                        { label: "After fallback", value: formatNumber(row.fallbacks), color: CHART.fallback },
                        { label: "Failed", value: formatNumber(row.failed), color: "var(--danger)" },
                      ]}
                      footer={`${formatNumber(row.requests)} total`}
                    />
                  );
                }}
              />
              <Bar dataKey="ok" stackId="r" fill={CHART.cost} stroke="var(--card)" strokeWidth={1} />
              <Bar dataKey="fallbacks" stackId="r" fill={CHART.fallback} stroke="var(--card)" strokeWidth={1} />
              <Bar dataKey="failed" stackId="r" fill="var(--danger)" stroke="var(--card)" strokeWidth={1} radius={[3, 3, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
