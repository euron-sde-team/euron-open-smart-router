"use client";

import { MoreHorizontalIcon, PencilIcon, PlusIcon, SlidersHorizontalIcon, StarIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { CopyButton } from "@/components/copy-button";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermissions } from "@/features/auth/api";
import { errorMessage } from "@/lib/api/errors";
import type { Policy } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatLatency, formatNumber, formatUsd } from "@/lib/format";
import { formatQuality, STRATEGY_LABEL, taskLabel } from "@/lib/models";

import { useDeletePolicy, usePolicies, useUpdatePolicy } from "../api";
import { PolicyDialog } from "./policy-dialog";

const AXES = [
  { key: "cost", label: "Cost", color: "var(--chart-cost)" },
  { key: "latency", label: "Latency", color: "var(--series-4)" },
  { key: "quality", label: "Quality", color: "var(--chart-saved)" },
] as const;

/** The policy's effective weights as one stacked bar with labels beneath. */
function Weights({ weights }: { weights: Record<string, number> }) {
  return (
    <div>
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-full" role="img" aria-label={AXES.map((a) => `${a.label} ${Math.round((weights[a.key] ?? 0) * 100)}%`).join(", ")}>
        {AXES.map((a) => {
          const w = weights[a.key] ?? 0;
          return w > 0 ? <div key={a.key} style={{ width: `${w * 100}%`, background: a.color }} className="first:rounded-l-full last:rounded-r-full" /> : null;
        })}
      </div>
      <div className="mt-1.5 flex gap-3 text-[11.5px] text-muted-foreground">
        {AXES.map((a) => (
          <span key={a.key} className="flex items-center gap-1">
            <span aria-hidden className="size-2 rounded-full" style={{ background: a.color }} />
            {a.label} <span className="font-medium text-foreground tabular">{Math.round((weights[a.key] ?? 0) * 100)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function PolicyCard({ p, canWrite, onEdit, onDelete }: { p: Policy; canWrite: boolean; onEdit: () => void; onDelete: () => void }) {
  const update = useUpdatePolicy();
  const route = `auto:${p.name}`;
  const limits: string[] = [];
  if (p.max_latency_ms != null) limits.push(`Under ${formatLatency(p.max_latency_ms)}`);
  if (p.max_cost_usd != null) limits.push(`At most ${formatUsd(Number(p.max_cost_usd))} per request`);
  if (p.allowed_models) limits.push(`Only ${p.allowed_models.join(", ")}`);
  if (p.excluded_models.length) limits.push(`Never ${p.excluded_models.join(", ")}`);

  return (
    <article className={cn("surface flex flex-col gap-4 p-4", !p.enabled && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="font-mono text-[14px] font-semibold">{p.name}</h3>
            {p.is_default && (
              <Chip tone="primary">
                <StarIcon />
                Default
              </Chip>
            )}
            {!p.enabled && <Chip tone="neutral">Disabled</Chip>}
          </div>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">{p.description || STRATEGY_LABEL[p.strategy]}</p>
        </div>
        {canWrite && (
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="subtle" size="icon-xs" aria-label={`Actions for ${p.name}`} />}>
              <MoreHorizontalIcon />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="min-w-48">
              <DropdownMenuItem onClick={onEdit}>
                <PencilIcon />
                Edit
              </DropdownMenuItem>
              {!p.is_default && (
                <DropdownMenuItem
                  onClick={() =>
                    update.mutate(
                      { id: p.id, body: { is_default: true } },
                      { onSuccess: () => toast.success(`${p.name} is now the default for model "auto".`), onError: (e) => toast.error(errorMessage(e)) },
                    )
                  }
                >
                  <StarIcon />
                  Make default
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onDelete} disabled={p.is_default} className="text-danger">
                <Trash2Icon />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      <Weights weights={p.effective_weights ?? {}} />
      <div className="flex flex-wrap gap-1.5">
        <Chip tone="neutral" title="Minimum benchmark score for a model to be eligible">
          Floor {formatQuality(p.quality_floor)}
          {p.hard_floor_boost > 0 && ` (+${formatQuality(p.hard_floor_boost)} hard)`}
        </Chip>
        {Object.entries(p.task_floors).map(([task, v]) => (
          <Chip key={task} tone="info">
            {taskLabel(task)} {formatQuality(v)}
          </Chip>
        ))}
        {!p.allow_fallback && <Chip tone="warning">No fallback</Chip>}
        {p.budget_downgrade && <Chip tone="savings">Budget downgrade</Chip>}
        {p.on_no_eligible === "reject" && <Chip tone="warning">Rejects when nothing qualifies</Chip>}
      </div>
      {limits.length > 0 && <ul className="space-y-0.5 text-[12.5px] text-muted-foreground">{limits.map((l) => <li key={l}>{l}</li>)}</ul>}
      <div className="-mx-4 -mb-4 mt-auto flex items-center justify-between gap-3 rounded-b-[14px] bg-muted/50 px-4 py-2.5 text-[12.5px]">
        <span className="flex min-w-0 items-center gap-1.5">
          <code className="truncate font-mono text-foreground">{route}</code>
          <CopyButton value={route} label={`Copy ${route}`} iconOnly variant="subtle" />
        </span>
        <span className="shrink-0 text-muted-foreground tabular">{formatNumber(p.requests_24h ?? 0)} requests in 24h</span>
      </div>
    </article>
  );
}

export function PoliciesScreen() {
  const { canWrite } = usePermissions();
  const q = usePolicies();
  const del = useDeletePolicy();
  const [dialog, setDialog] = React.useState<{ open: boolean; policy: Policy | null }>({ open: false, policy: null });
  const [deleting, setDeleting] = React.useState<Policy | null>(null);
  const add = () => setDialog({ open: true, policy: null });

  let body: React.ReactNode;
  if (q.isPending)
    body = (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-60 rounded-[14px]" />
        ))}
      </div>
    );
  else if (q.isError && !q.data)
    body = (
      <div className="surface">
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      </div>
    );
  else if (!q.data?.length)
    body = (
      <div className="surface">
        <EmptyState
          icon={SlidersHorizontalIcon}
          title="No policies yet"
          description='A policy tells the router how to choose: cheapest, fastest or best, above a quality floor. Create one and mark it default so model "auto" works.'
          action={canWrite ? <Button size="sm" onClick={add}><PlusIcon />Create a policy</Button> : undefined}
        />
      </div>
    );
  else
    body = (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {q.data.map((p) => (
          <PolicyCard key={p.id} p={p} canWrite={canWrite} onEdit={() => setDialog({ open: true, policy: p })} onDelete={() => setDeleting(p)} />
        ))}
      </div>
    );

  return (
    <>
      <PageHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Policies <Hint term="policy" />
          </span>
        }
        description='How the router trades cost, speed and quality. Clients pick one with model "auto:<name>" or the x-smartrouter-policy header; model "auto" uses the project default, then the global default.'
        actions={
          canWrite && (
            <Button onClick={add}>
              <PlusIcon />
              New policy
            </Button>
          )
        }
      />
      {body}
      <PolicyDialog open={dialog.open} policy={dialog.policy} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name ?? "policy"}?`}
        description={`Requests for "auto:${deleting?.name ?? ""}" will fail with policy_not_found, and projects using it fall back to the global default.`}
        confirmLabel="Delete policy"
        pending={del.isPending}
        onConfirm={() =>
          deleting &&
          del.mutate(deleting.id, {
            onSuccess: () => {
              toast.success(`Policy ${deleting.name} deleted.`);
              setDeleting(null);
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
    </>
  );
}
