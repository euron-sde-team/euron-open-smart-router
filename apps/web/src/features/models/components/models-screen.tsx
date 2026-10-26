"use client";

import {
  BracesIcon,
  CpuIcon,
  EyeIcon,
  GaugeIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  PowerIcon,
  RotateCcwIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Surface } from "@/components/surface";
import { TableSkeleton } from "@/components/table-skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePermissions } from "@/features/auth/api";
import { errorMessage } from "@/lib/api/errors";
import type { Model, ModelHealth, Tier } from "@/lib/api/types";
import { circuitChip } from "@/lib/chips";
import { cn } from "@/lib/cn";
import { formatLatency, formatRatio, formatTokens } from "@/lib/format";
import { modelColors, perMillion, TIER_LABEL } from "@/lib/models";

import { useModelHealth, useModels, useResetCircuit, useUpdateModel } from "../api";
import { ModelDialog } from "./model-dialog";
import { QualityMatrix } from "./quality-matrix";

function Capabilities({ m }: { m: Model }) {
  const caps = [
    { on: m.supports_tools, icon: WrenchIcon, label: "Tools" },
    { on: m.supports_vision, icon: EyeIcon, label: "Vision" },
    { on: m.supports_json, icon: BracesIcon, label: "JSON" },
  ];
  return (
    <div className="flex gap-1">
      {caps.map(({ on, icon: Icon, label }) => (
        <span
          key={label}
          title={`${label}: ${on ? "supported" : "not supported"}`}
          className={cn("flex size-6 items-center justify-center rounded-md", on ? "bg-primary-soft text-primary" : "bg-muted text-faint")}
        >
          <Icon className="size-3.5" aria-hidden />
          <span className="sr-only">
            {label} {on ? "supported" : "not supported"}
          </span>
        </span>
      ))}
    </div>
  );
}

function HealthCell({ h }: { h: ModelHealth | undefined }) {
  const chip = circuitChip(h?.state);
  return (
    <div>
      <Chip tone={chip.tone} dot title={chip.hint}>
        {chip.label}
      </Chip>
      <div className="mt-1 text-[11.5px] text-subtle tabular">
        {h && h.samples > 0
          ? `p50 ${formatLatency(h.p50_ms)} · p95 ${formatLatency(h.p95_ms)} · ${formatRatio(h.error_rate)} errors`
          : `No traffic yet · prior ${formatLatency(h?.latency_prior_ms)}`}
      </div>
    </div>
  );
}

function CatalogTable({ canWrite, onAdd, onEdit }: { canWrite: boolean; onAdd: () => void; onEdit: (m: Model) => void }) {
  const models = useModels();
  const health = useModelHealth();
  const update = useUpdateModel();
  const reset = useResetCircuit();
  const byAlias = React.useMemo(() => new Map((health.data ?? []).map((h) => [h.model, h])), [health.data]);
  const color = modelColors((models.data ?? []).map((m) => m.alias));

  if (models.isPending) return <div className="pt-3"><TableSkeleton rows={5} cols={6} /></div>;
  if (models.isError && !models.data) return <ErrorState error={models.error} onRetry={() => void models.refetch()} />;
  if (!models.data?.length)
    return (
      <EmptyState
        icon={CpuIcon}
        title="No models yet"
        description="A model is something the router can choose: an endpoint's model with a price, a tier and its capabilities. Add a cheap one, a strong one and a local one to see routing pay off."
        action={canWrite ? <Button size="sm" onClick={onAdd}><PlusIcon />Add a model</Button> : undefined}
      />
    );

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Model</TableHead>
          <TableHead className="hidden md:table-cell">Tier</TableHead>
          <TableHead className="text-right">In / 1M</TableHead>
          <TableHead className="text-right">Out / 1M</TableHead>
          <TableHead className="hidden lg:table-cell">Capabilities</TableHead>
          <TableHead className="hidden sm:table-cell">
            <span className="inline-flex items-center gap-1">
              Health <Hint term="circuit" />
            </span>
          </TableHead>
          <TableHead className="text-right"><span className="sr-only">Actions</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {models.data.map((m) => {
          const h = byAlias.get(m.alias);
          return (
            <TableRow key={m.id} className={cn(!m.enabled && "opacity-55")}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: color(m.alias) }} />
                  <span className="font-mono text-[12.5px] font-medium">{m.alias}</span>
                  {!m.enabled && <Chip tone="neutral">Disabled</Chip>}
                </div>
                <div className="mt-0.5 pl-[18px] text-[11.5px] text-subtle">
                  {m.provider_name} · <span className="font-mono">{m.upstream_model}</span> · {formatTokens(m.context_window)} context
                </div>
              </TableCell>
              <TableCell className="hidden md:table-cell">
                <Chip tone={m.tier === "advanced" ? "primary" : m.tier === "local" ? "info" : "neutral"}>{TIER_LABEL[m.tier as Tier] ?? m.tier}</Chip>
              </TableCell>
              <TableCell className="text-right tabular">{perMillion(m.input_per_m)}</TableCell>
              <TableCell className="text-right tabular">{perMillion(m.output_per_m)}</TableCell>
              <TableCell className="hidden lg:table-cell">
                <Capabilities m={m} />
              </TableCell>
              <TableCell className="hidden sm:table-cell">
                <HealthCell h={h} />
              </TableCell>
              <TableCell className="text-right">
                {canWrite && (
                  <DropdownMenu>
                    <DropdownMenuTrigger render={<Button variant="subtle" size="icon-xs" aria-label={`Actions for ${m.alias}`} />}>
                      <MoreHorizontalIcon />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent className="min-w-52">
                      <DropdownMenuItem onClick={() => onEdit(m)}>
                        <PencilIcon />
                        Edit price and capabilities
                      </DropdownMenuItem>
                      {h && h.state !== "closed" && (
                        <DropdownMenuItem
                          onClick={() =>
                            reset.mutate(m.alias, {
                              onSuccess: () => toast.success(`Circuit for ${m.alias} closed. It receives traffic again.`),
                              onError: (e) => toast.error(errorMessage(e)),
                            })
                          }
                        >
                          <RotateCcwIcon />
                          Close circuit now
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onClick={() =>
                          update.mutate(
                            { id: m.id, body: { enabled: !m.enabled } },
                            {
                              onSuccess: () => toast.success(m.enabled ? `${m.alias} disabled: the router will not choose it.` : `${m.alias} enabled.`),
                              onError: (e) => toast.error(errorMessage(e)),
                            },
                          )
                        }
                      >
                        <PowerIcon />
                        {m.enabled ? "Disable" : "Enable"}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function ModelsScreen() {
  const { canWrite } = usePermissions();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tab = params.get("tab") === "quality" ? "quality" : "catalog";
  const [dialog, setDialog] = React.useState<{ open: boolean; model: Model | null }>({ open: false, model: null });

  return (
    <>
      <PageHeader
        title="Models"
        description="Everything the router can choose from: hard facts it filters on (price, context, capabilities, health) and the measured quality it ranks by."
        actions={
          <>
            <Link href="/benchmarks" className={buttonVariants({ variant: "outline" })}>
              <GaugeIcon />
              Benchmarks
            </Link>
            {canWrite && (
              <Button onClick={() => setDialog({ open: true, model: null })}>
                <PlusIcon />
                Add model
              </Button>
            )}
          </>
        }
      />
      <Tabs value={tab} onValueChange={(v) => router.replace(v === "quality" ? `${pathname}?tab=quality` : pathname, { scroll: false })}>
        <TabsList>
          <TabsTrigger value="catalog">
            <CpuIcon />
            Catalog and health
          </TabsTrigger>
          <TabsTrigger value="quality">
            <GaugeIcon />
            Quality
          </TabsTrigger>
        </TabsList>
        <TabsContent value="catalog">
          <Surface className="overflow-hidden">
            <CatalogTable canWrite={canWrite} onAdd={() => setDialog({ open: true, model: null })} onEdit={(m) => setDialog({ open: true, model: m })} />
          </Surface>
        </TabsContent>
        <TabsContent value="quality">
          <QualityMatrix canWrite={canWrite} />
        </TabsContent>
      </Tabs>
      <ModelDialog open={dialog.open} model={dialog.model} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} />
    </>
  );
}
