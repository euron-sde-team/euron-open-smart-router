"use client";

import * as React from "react";
import { toast } from "sonner";

import { Segmented } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { errorMessage } from "@/lib/api/errors";
import type { Model, Tier } from "@/lib/api/types";
import { TIER_HINT, TIER_LABEL } from "@/lib/models";

import { useCreateModel, useProviders, useUpdateModel } from "../api";

const TIERS: Tier[] = ["advanced", "fast", "low-cost", "local"];

interface Form {
  alias: string;
  providerId: string;
  upstream: string;
  description: string;
  tier: Tier;
  ctx: string;
  maxOut: string;
  pin: string;
  pout: string;
  latency: string;
  tools: boolean;
  vision: boolean;
  json: boolean;
}

function initial(model: Model | null | undefined, providerId: string): Form {
  return {
    alias: model?.alias ?? "",
    providerId: model?.provider_id ?? providerId,
    upstream: model?.upstream_model ?? "",
    description: model?.description ?? "",
    tier: (model?.tier as Tier) ?? "advanced",
    ctx: String(model?.context_window ?? 128000),
    maxOut: String(model?.max_output_tokens ?? 16384),
    pin: model ? String(model.input_per_m) : "",
    pout: model ? String(model.output_per_m) : "",
    latency: String(model?.latency_prior_ms ?? 1000),
    tools: model?.supports_tools ?? true,
    vision: model?.supports_vision ?? false,
    json: model?.supports_json ?? true,
  };
}

function Capability({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-4 rounded-xl bg-muted/60 px-3.5 py-2.5">
      <span>
        <span className="block text-[13px] font-medium">{label}</span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

export function ModelDialog({ open, onOpenChange, model }: { open: boolean; onOpenChange: (o: boolean) => void; model?: Model | null }) {
  const editing = !!model;
  const providers = useProviders();
  const create = useCreateModel();
  const update = useUpdateModel();
  const pending = create.isPending || update.isPending;
  const [f, setF] = React.useState<Form>(() => initial(model, ""));
  const [error, setError] = React.useState<string | null>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));

  React.useEffect(() => {
    if (open) {
      setF(initial(model, providers.data?.[0]?.id ?? ""));
      setError(null);
    }
  }, [open, model, providers.data]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!editing && !/^[A-Za-z0-9._/-]+$/.test(f.alias.trim()))
      return setError("Alias: letters, numbers, dots, slashes, dashes or underscores. Clients can send it as the model.");
    if (!editing && f.alias.trim() === "auto") return setError('"auto" is reserved for routing. Pick another alias.');
    if (!f.providerId) return setError("Pick the endpoint that serves this model.");
    if (!f.upstream.trim()) return setError("Enter the endpoint's model name, for example gpt-4o-mini.");
    const ctx = Number(f.ctx);
    const maxOut = Number(f.maxOut);
    const latency = Number(f.latency);
    if (![ctx, maxOut, latency].every((n) => Number.isInteger(n) && n > 0))
      return setError("Context window, max output and latency must be whole numbers above 0.");
    const pin = Number(f.pin || 0);
    const pout = Number(f.pout || 0);
    if (!Number.isFinite(pin) || !Number.isFinite(pout) || pin < 0 || pout < 0)
      return setError("Prices are USD per 1M tokens, 0 or more. Use 0 for a free local model.");
    setError(null);
    const body = {
      upstream_model: f.upstream.trim(),
      description: f.description.trim(),
      tier: f.tier,
      context_window: ctx,
      max_output_tokens: maxOut,
      input_per_m: pin,
      output_per_m: pout,
      supports_tools: f.tools,
      supports_vision: f.vision,
      supports_json: f.json,
      latency_prior_ms: latency,
    };
    const done = {
      onSuccess: () => {
        toast.success(editing ? `Model ${model?.alias} updated.` : `Model ${f.alias.trim()} added. Run a benchmark to measure its quality.`);
        onOpenChange(false);
      },
      onError: (err: unknown) => setError(errorMessage(err)),
    };
    if (editing && model) update.mutate({ id: model.id, body }, done);
    else create.mutate({ ...body, alias: f.alias.trim(), provider_id: f.providerId, enabled: true }, done);
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${model?.alias}` : "Add model"}</DialogTitle>
          <DialogDescription>
            A model the router can choose. Price and capabilities are hard facts it filters on; quality comes from benchmarks.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Alias" htmlFor="m-alias" hint={editing ? "Fixed once created." : undefined}>
                <Input id="m-alias" className="font-mono text-[12.5px]" value={f.alias} disabled={editing} onChange={(e) => set("alias", e.target.value)} placeholder="gpt-fast" />
              </Field>
              <Field label="Endpoint" htmlFor="m-provider">
                <SimpleSelect
                  id="m-provider"
                  value={f.providerId}
                  onValueChange={(v) => set("providerId", v)}
                  disabled={editing}
                  options={(providers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
                  placeholder="Choose"
                />
              </Field>
            </div>
            <Field label="Upstream model" htmlFor="m-upstream">
              <Input id="m-upstream" className="font-mono text-[12.5px]" value={f.upstream} onChange={(e) => set("upstream", e.target.value)} placeholder="gpt-4o-mini" />
            </Field>
            <Field label="Description" htmlFor="m-desc" hint="Shown to people choosing policies.">
              <Input id="m-desc" value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="Quick general model for short answers" />
            </Field>
            <Field label="Tier" hint={`${TIER_HINT[f.tier]} Until benchmarked, the router assumes a quality from the tier.`}>
              <Segmented ariaLabel="Tier" value={f.tier} onChange={(t) => set("tier", t)} options={TIERS.map((t) => ({ value: t, label: TIER_LABEL[t] }))} />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Input $ / 1M tokens" htmlFor="m-pin">
                <Input id="m-pin" inputMode="decimal" className="tabular" value={f.pin} onChange={(e) => set("pin", e.target.value)} placeholder="0.15" />
              </Field>
              <Field label="Output $ / 1M tokens" htmlFor="m-pout">
                <Input id="m-pout" inputMode="decimal" className="tabular" value={f.pout} onChange={(e) => set("pout", e.target.value)} placeholder="0.60" />
              </Field>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <Field label="Context window" htmlFor="m-ctx">
                <Input id="m-ctx" inputMode="numeric" className="tabular" value={f.ctx} onChange={(e) => set("ctx", e.target.value)} />
              </Field>
              <Field label="Max output" htmlFor="m-max">
                <Input id="m-max" inputMode="numeric" className="tabular" value={f.maxOut} onChange={(e) => set("maxOut", e.target.value)} />
              </Field>
              <Field label="Typical latency (ms)" htmlFor="m-lat">
                <Input id="m-lat" inputMode="numeric" className="tabular" value={f.latency} onChange={(e) => set("latency", e.target.value)} />
              </Field>
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              <Capability label="Tools" hint="Function calling" checked={f.tools} onChange={(v) => set("tools", v)} />
              <Capability label="Vision" hint="Image input" checked={f.vision} onChange={(v) => set("vision", v)} />
              <Capability label="JSON" hint="JSON output mode" checked={f.json} onChange={(v) => set("json", v)} />
            </div>
            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Saving..." : editing ? "Save changes" : "Add model"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
