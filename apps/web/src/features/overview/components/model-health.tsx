"use client";

import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";

import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Chip } from "@/components/ui/chip";
import { Skeleton } from "@/components/ui/skeleton";
import { useModelHealth, useModels } from "@/features/models/api";
import { circuitChip } from "@/lib/chips";
import { formatLatency, formatRatio } from "@/lib/format";
import { modelColors } from "@/lib/models";

/** Live circuit state per model, so an outage is visible from the landing page. */
export function ModelHealthPanel() {
  const q = useModelHealth();
  const models = useModels();
  const color = modelColors((models.data ?? []).map((m) => m.alias));
  const unhealthy = (q.data ?? []).filter((h) => h.state !== "closed").length;
  return (
    <Surface className="flex h-full flex-col">
      <SurfaceHeader
        title="Model health"
        titleExtra={<Hint term="circuit" />}
        description={q.data ? (unhealthy ? `${unhealthy} model${unhealthy === 1 ? "" : "s"} skipped by the router` : "Every model is taking traffic") : "Live circuit breakers"}
        action={
          <Link href="/models" className="inline-flex items-center gap-1 text-[12.5px] font-medium text-primary hover:underline">
            Models
            <ArrowRightIcon className="size-3.5" />
          </Link>
        }
      />
      <div className="flex-1 px-5 pb-5">
        {q.isPending ? (
          <div className="space-y-2.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </div>
        ) : q.isError && !q.data ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
        ) : !q.data?.length ? (
          <p className="py-6 text-center text-[13px] text-muted-foreground">
            No models yet. <Link href="/models" className="font-medium text-primary hover:underline">Add one</Link> to start routing.
          </p>
        ) : (
          <ul className="space-y-1">
            {q.data.map((h) => {
              const chip = circuitChip(h.state);
              return (
                <li key={h.model} className="flex items-center justify-between gap-3 rounded-lg px-2 py-2 hover:bg-muted/50">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: color(h.model) }} />
                      <span className="truncate font-mono text-[12.5px] font-medium">{h.model}</span>
                    </div>
                    <div className="mt-0.5 pl-[18px] text-[11.5px] text-subtle tabular">
                      {h.samples ? `p50 ${formatLatency(h.p50_ms)} · ${formatRatio(h.error_rate)} errors` : "No recent traffic"}
                    </div>
                  </div>
                  <Chip tone={chip.tone} dot title={chip.hint}>
                    {chip.label}
                  </Chip>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Surface>
  );
}
