"use client";

import { DollarSignIcon, GitForkIcon, PercentIcon, PiggyBankIcon, TimerIcon, ZapIcon } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { KpiTile } from "@/components/kpi-tile";
import type { Overview, RangeKey, Totals } from "@/lib/api/types";
import { formatNumber, formatPercent, formatRatio, formatUsd } from "@/lib/format";
import { previousPeriodLabel } from "@/lib/range";

export function KpiGrid({
  data,
  range,
  loading,
  error,
  onRetry,
}: {
  data: Overview | undefined;
  range: RangeKey;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  if (error && !data) {
    return (
      <div className="surface">
        <ErrorState error={error} onRetry={onRetry} title="Could not load the headline numbers" compact />
      </div>
    );
  }
  const t: Partial<Totals> = data?.totals ?? {};
  const p: Partial<Totals> = data?.previous ?? {};
  const cmp = previousPeriodLabel(range);
  const overhead = data?.routing_overhead_us_p50;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <KpiTile
        label="Saved"
        icon={PiggyBankIcon}
        hint="savings"
        accent="savings"
        value={formatUsd(t.savings_usd ?? 0)}
        current={t.savings_usd}
        previous={p.savings_usd}
        goodWhen="up"
        comparison={cmp}
        loading={loading}
      />
      <KpiTile
        label="Savings rate"
        icon={PercentIcon}
        hint="baseline"
        accent="savings"
        value={formatPercent(t.savings_pct ?? 0)}
        current={t.savings_pct}
        previous={p.savings_pct}
        goodWhen="up"
        comparison={cmp}
        loading={loading}
      />
      <KpiTile label="Spend" icon={DollarSignIcon} value={formatUsd(t.cost_usd ?? 0)} current={t.cost_usd} previous={p.cost_usd} goodWhen="down" comparison={cmp} loading={loading} />
      <KpiTile label="Requests" icon={ZapIcon} value={formatNumber(t.requests ?? 0)} current={t.requests} previous={p.requests} goodWhen="neutral" comparison={cmp} loading={loading} />
      <KpiTile
        label="Fallback rate"
        icon={GitForkIcon}
        hint="fallback"
        accent="warning"
        value={formatRatio(t.fallback_rate ?? 0)}
        current={t.fallback_rate}
        previous={p.fallback_rate}
        goodWhen="down"
        comparison={cmp}
        loading={loading}
      />
      <KpiTile
        label="Routing time"
        icon={TimerIcon}
        hint="routingOverhead"
        value={overhead != null ? `${formatNumber(overhead)} µs` : "n/a"}
        goodWhen="neutral"
        comparison="p50 per request"
        loading={loading}
      />
    </div>
  );
}
