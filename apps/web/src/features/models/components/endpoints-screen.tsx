"use client";

import { CheckCircle2Icon, FlaskConicalIcon, PencilIcon, PlugIcon, PlusIcon, RouteIcon, ServerIcon, ShieldIcon, XCircleIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { usePermissions } from "@/features/auth/api";
import { errorMessage } from "@/lib/api/errors";
import type { Provider, ProviderTest } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatDuration, formatLatency } from "@/lib/format";
import { KIND_LABEL } from "@/lib/models";

import { useProviders, useTestProvider, useUpdateProvider } from "../api";
import { EndpointDialog } from "./endpoint-dialog";

function EndpointCard({ endpoint, canWrite, onEdit }: { endpoint: Provider; canWrite: boolean; onEdit: () => void }) {
  const test = useTestProvider();
  const update = useUpdateProvider();
  const [result, setResult] = React.useState<ProviderTest | { ok: false; message: string; latency_ms?: number } | null>(null);
  const Icon = endpoint.kind === "tokenshield" ? ShieldIcon : ServerIcon;

  function runTest() {
    setResult(null);
    test.mutate(endpoint.id, {
      onSuccess: (r) => setResult(r),
      onError: (e) => setResult({ ok: false, message: errorMessage(e) }),
    });
  }

  return (
    <article className={cn("surface flex flex-col gap-4 p-4", !endpoint.enabled && "opacity-60")}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-[14px] font-semibold">{endpoint.name}</h3>
            <div className="truncate font-mono text-[11.5px] text-muted-foreground">{endpoint.base_url}</div>
          </div>
        </div>
        {canWrite && (
          <Button variant="subtle" size="icon-xs" aria-label={`Edit ${endpoint.name}`} onClick={onEdit}>
            <PencilIcon />
          </Button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Chip tone="primary">{KIND_LABEL[endpoint.kind] ?? endpoint.kind}</Chip>
        {endpoint.simulated && (
          <Chip tone="info" title="May receive the benchmark answer key">
            <FlaskConicalIcon />
            Simulated
          </Chip>
        )}
        <Chip tone={endpoint.has_key ? "success" : "neutral"}>{endpoint.has_key ? `Key ${endpoint.key_hint ?? "sealed"}` : "No key"}</Chip>
        <Chip tone="neutral">
          {endpoint.model_count ?? 0} model{endpoint.model_count === 1 ? "" : "s"}
        </Chip>
        {endpoint.timeout_s != null && <Chip tone="neutral">Timeout {formatDuration(endpoint.timeout_s)}</Chip>}
      </div>
      {result && (
        <div role="status" className={cn("flex items-start gap-2 rounded-xl px-3 py-2.5 text-[12.5px]", result.ok ? "bg-savings-soft" : "bg-danger-soft")}>
          {result.ok ? <CheckCircle2Icon className="mt-px size-4 shrink-0 text-savings" /> : <XCircleIcon className="mt-px size-4 shrink-0 text-danger" />}
          <div className="min-w-0">
            <span className={cn("font-semibold", result.ok ? "text-savings" : "text-danger")}>
              {result.ok ? "Connected" : "Connection failed"}
              {result.latency_ms != null && result.ok ? ` in ${formatLatency(result.latency_ms)}` : ""}
            </span>
            <p className="mt-0.5 break-words text-muted-foreground">{result.message}</p>
          </div>
        </div>
      )}
      <div className="-mx-4 -mb-4 mt-auto flex items-center justify-between gap-3 rounded-b-[14px] bg-muted/50 px-4 py-2.5">
        <Button variant="outline" size="sm" onClick={runTest} disabled={test.isPending}>
          <PlugIcon />
          {test.isPending ? "Testing..." : "Test connection"}
        </Button>
        {canWrite && (
          <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
            {endpoint.enabled ? "Enabled" : "Disabled"}
            <Switch
              checked={endpoint.enabled}
              onCheckedChange={(enabled) =>
                update.mutate(
                  { id: endpoint.id, body: { enabled } },
                  {
                    onSuccess: () => toast.success(enabled ? `${endpoint.name} enabled.` : `${endpoint.name} disabled: its models leave routing.`),
                    onError: (e) => toast.error(errorMessage(e)),
                  },
                )
              }
            />
          </label>
        )}
      </div>
    </article>
  );
}

export function EndpointsScreen() {
  const { canWrite } = usePermissions();
  const q = useProviders();
  const [dialog, setDialog] = React.useState<{ open: boolean; endpoint: Provider | null }>({ open: false, endpoint: null });
  const add = () => setDialog({ open: true, endpoint: null });

  let body: React.ReactNode;
  if (q.isPending)
    body = (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-44 rounded-[14px]" />
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
          icon={RouteIcon}
          title="No endpoints yet"
          description="An endpoint is an upstream API the router can call: OpenAI, Anthropic, any OpenAI-compatible server such as Ollama or vLLM, or a TokenShield gateway. Add at least three to give the router real choices."
          action={canWrite ? <Button size="sm" onClick={add}><PlusIcon />Add an endpoint</Button> : undefined}
        />
      </div>
    );
  else
    body = (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {q.data.map((p) => (
          <EndpointCard key={p.id} endpoint={p} canWrite={canWrite} onEdit={() => setDialog({ open: true, endpoint: p })} />
        ))}
      </div>
    );

  return (
    <>
      <PageHeader
        title="Endpoints"
        description="The upstream APIs behind every model. Route through a TokenShield endpoint and its budget ledger stays the single source of spend."
        actions={
          canWrite && (
            <Button onClick={add}>
              <PlusIcon />
              Add endpoint
            </Button>
          )
        }
      />
      {body}
      <EndpointDialog open={dialog.open} provider={dialog.endpoint} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} />
    </>
  );
}
