"use client";

import { PlusIcon, XIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Hint } from "@/components/hint";
import { Segmented } from "@/components/segmented";
import { ToggleChips } from "@/components/toggle-chips";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useModels } from "@/features/models/api";
import { errorMessage } from "@/lib/api/errors";
import type { Policy, PolicyIn, PolicyUpdate, Strategy } from "@/lib/api/types";
import { STRATEGY_LABEL, TASKS, taskLabel } from "@/lib/models";

import { useCreatePolicy, useUpdatePolicy } from "../api";

const STRATEGIES: Strategy[] = ["balanced", "cost", "latency", "quality"];
const STRATEGY_HINT: Record<Strategy, string> = {
  balanced: "Weighs cost 35%, latency 25%, quality 40%.",
  cost: "Cheapest model above the floor (cost 70%).",
  latency: "Fastest model above the floor (latency 70%).",
  quality: "Best model; price and speed only break near ties.",
};

interface Form {
  name: string;
  description: string;
  strategy: Strategy;
  floor: string;
  boost: string;
  taskFloors: [string, string][];
  maxLatency: string;
  maxCost: string;
  restrict: boolean;
  allowed: string[];
  excluded: string[];
  fallback: boolean;
  attempts: string;
  downgrade: boolean;
  onNoEligible: "best_quality" | "reject";
  baseline: string;
}

function initial(p: Policy | null | undefined): Form {
  return {
    name: p?.name ?? "",
    description: p?.description ?? "",
    strategy: (p?.strategy as Strategy) ?? "balanced",
    floor: String(p?.quality_floor ?? 0.8),
    boost: String(p?.hard_floor_boost ?? 0.1),
    taskFloors: Object.entries(p?.task_floors ?? {}).map(([k, v]) => [k, String(v)]),
    maxLatency: p?.max_latency_ms != null ? String(p.max_latency_ms) : "",
    maxCost: p?.max_cost_usd != null ? String(p.max_cost_usd) : "",
    restrict: p?.allowed_models != null,
    allowed: p?.allowed_models ?? [],
    excluded: p?.excluded_models ?? [],
    fallback: p?.allow_fallback ?? true,
    attempts: String(p?.max_attempts ?? 3),
    downgrade: p?.budget_downgrade ?? false,
    onNoEligible: (p?.on_no_eligible as Form["onNoEligible"]) ?? "best_quality",
    baseline: p?.baseline_model ?? "",
  };
}

function Toggle({ label, hint, term, checked, onChange }: { label: string; hint: string; term?: "budgetDowngrade" | "fallback"; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-4 rounded-xl bg-muted/60 px-3.5 py-2.5">
      <span>
        <span className="flex items-center gap-1 text-[13px] font-medium">
          {label}
          {term && <Hint term={term} />}
        </span>
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

const unit = (s: string) => {
  const n = Number(s);
  return s.trim() !== "" && Number.isFinite(n) && n >= 0 && n <= 1 ? n : null;
};

export function PolicyDialog({ open, onOpenChange, policy }: { open: boolean; onOpenChange: (o: boolean) => void; policy?: Policy | null }) {
  const editing = !!policy;
  const models = useModels();
  const create = useCreatePolicyBridge();
  const pending = create.pending;
  const [f, setF] = React.useState<Form>(() => initial(policy));
  const [error, setError] = React.useState<string | null>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));
  const aliases = (models.data ?? []).map((m) => ({ value: m.alias, label: m.alias }));

  React.useEffect(() => {
    if (open) {
      setF(initial(policy));
      setError(null);
    }
  }, [open, policy]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!editing && !/^[a-z0-9][a-z0-9-]{0,62}$/.test(f.name.trim()))
      return setError('Name: lowercase letters, numbers and dashes, like "support-bot". Clients send model "auto:<name>".');
    const floor = unit(f.floor);
    const boost = Number(f.boost);
    if (floor == null) return setError("The quality floor is a score between 0 and 1, such as 0.8.");
    if (!Number.isFinite(boost) || boost < 0 || boost > 0.5) return setError("The hard-request boost is between 0 and 0.5.");
    const taskFloors: Record<string, number> = {};
    for (const [task, value] of f.taskFloors) {
      const v = unit(value);
      if (v == null) return setError(`The ${task} floor must be between 0 and 1.`);
      taskFloors[task] = v;
    }
    const maxLatency = f.maxLatency.trim() ? Number(f.maxLatency) : null;
    if (maxLatency != null && (!Number.isInteger(maxLatency) || maxLatency <= 0)) return setError("The latency ceiling is whole milliseconds above 0, or empty.");
    const maxCost = f.maxCost.trim() ? Number(f.maxCost) : null;
    if (maxCost != null && (!Number.isFinite(maxCost) || maxCost <= 0)) return setError("The cost ceiling is USD per request above 0, or empty.");
    const attempts = Number(f.attempts);
    if (!Number.isInteger(attempts) || attempts < 1 || attempts > 6) return setError("Attempts are 1 to 6.");
    if (f.restrict && f.allowed.length === 0) return setError("Pick at least one allowed model, or switch off the restriction.");
    setError(null);
    const body = {
      description: f.description.trim(),
      strategy: f.strategy,
      quality_floor: floor,
      hard_floor_boost: boost,
      task_floors: taskFloors,
      max_latency_ms: maxLatency,
      max_cost_usd: maxCost,
      allowed_models: f.restrict ? f.allowed : null,
      excluded_models: f.excluded,
      allow_fallback: f.fallback,
      max_attempts: attempts,
      budget_downgrade: f.downgrade,
      on_no_eligible: f.onNoEligible,
      baseline_model: f.baseline || null,
    };
    create.save(
      editing && policy ? { id: policy.id, body } : { body: { ...body, name: f.name.trim() } as PolicyIn },
      () => {
        toast.success(editing ? `Policy ${policy?.name} saved.` : `Policy ${f.name.trim()} created. Send model "auto:${f.name.trim()}" to use it.`);
        onOpenChange(false);
      },
      (err) => setError(errorMessage(err)),
    );
  }

  const unusedTasks = TASKS.filter((t) => !f.taskFloors.some(([k]) => k === t));

  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${policy?.name}` : "New routing policy"}</DialogTitle>
          <DialogDescription>How to trade cost, speed and quality for one kind of traffic.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-[200px_1fr]">
              <Field label="Name" htmlFor="p-name" hint={editing ? "Fixed once created." : `Clients send "auto:${f.name || "name"}".`}>
                <Input id="p-name" className="font-mono text-[12.5px]" value={f.name} disabled={editing} onChange={(e) => set("name", e.target.value.toLowerCase())} placeholder="support-bot" />
              </Field>
              <Field label="Description" htmlFor="p-desc">
                <Input id="p-desc" value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="Customer support: cheap and fast" />
              </Field>
            </div>
            <Field label="Strategy" hint={STRATEGY_HINT[f.strategy]}>
              <Segmented ariaLabel="Strategy" value={f.strategy} onChange={(v) => set("strategy", v)} options={STRATEGIES.map((s) => ({ value: s, label: STRATEGY_LABEL[s] }))} />
            </Field>

            <section className="space-y-3">
              <h3 className="flex items-center gap-1 text-[13px] font-semibold">
                Quality <Hint term="qualityFloor" />
              </h3>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Quality floor" htmlFor="p-floor" hint="0.80 kept quality within about 3 points of the best model in our benchmark.">
                  <Input id="p-floor" inputMode="decimal" className="tabular" value={f.floor} onChange={(e) => set("floor", e.target.value)} />
                </Field>
                <Field label="Hard-request boost" htmlFor="p-boost" hint="Added to the floor for complexity 4 and 5.">
                  <Input id="p-boost" inputMode="decimal" className="tabular" value={f.boost} onChange={(e) => set("boost", e.target.value)} />
                </Field>
              </div>
              <div>
                <div className="mb-1.5 flex items-center gap-1 text-[13px] font-medium">
                  Task floors <Hint term="taskFloors" />
                </div>
                <div className="space-y-2">
                  {f.taskFloors.map(([task, value], i) => (
                    <div key={task} className="flex items-center gap-2">
                      <span className="w-32 text-[13px]">{taskLabel(task)}</span>
                      <Input
                        aria-label={`${task} floor`}
                        inputMode="decimal"
                        className="w-24 tabular"
                        value={value}
                        onChange={(e) => set("taskFloors", f.taskFloors.map((p, j) => (j === i ? [p[0], e.target.value] : p)))}
                      />
                      <Button type="button" variant="subtle" size="icon-xs" aria-label={`Remove ${task} floor`} onClick={() => set("taskFloors", f.taskFloors.filter((_, j) => j !== i))}>
                        <XIcon />
                      </Button>
                    </div>
                  ))}
                  {unusedTasks.length > 0 && (
                    <div className="w-56">
                      <SimpleSelect
                        aria-label="Add a task floor"
                        value=""
                        placeholder="Add a task floor"
                        onValueChange={(t) => t && set("taskFloors", [...f.taskFloors, [t, f.floor]])}
                        options={unusedTasks.map((t) => ({ value: t, label: taskLabel(t) }))}
                      />
                    </div>
                  )}
                </div>
              </div>
              <Field label="When nothing clears the floor" hint={f.onNoEligible === "best_quality" ? "Use the best available models, and record that the floor was relaxed." : "Refuse with 422 no_eligible_model."}>
                <Segmented
                  ariaLabel="When nothing clears the floor"
                  value={f.onNoEligible}
                  onChange={(v) => set("onNoEligible", v)}
                  options={[
                    { value: "best_quality", label: "Use the best available" },
                    { value: "reject", label: "Reject the request" },
                  ]}
                />
              </Field>
            </section>

            <section className="space-y-3">
              <h3 className="text-[13px] font-semibold">Limits</h3>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Latency ceiling (ms)" htmlFor="p-lat" hint="Optional. Skips slower models.">
                  <Input id="p-lat" inputMode="numeric" className="tabular" value={f.maxLatency} onChange={(e) => set("maxLatency", e.target.value)} placeholder="None" />
                </Field>
                <Field label="Cost ceiling per request ($)" htmlFor="p-cost" hint="Optional. Estimated before the call.">
                  <Input id="p-cost" inputMode="decimal" className="tabular" value={f.maxCost} onChange={(e) => set("maxCost", e.target.value)} placeholder="None" />
                </Field>
              </div>
              <Toggle label="Only allow some models" hint="Everything else is never chosen by this policy." checked={f.restrict} onChange={(v) => set("restrict", v)} />
              {f.restrict && <ToggleChips ariaLabel="Allowed models" options={aliases} value={f.allowed} onChange={(v) => set("allowed", v)} />}
              <Field label="Excluded models">
                <ToggleChips ariaLabel="Excluded models" options={aliases} value={f.excluded} onChange={(v) => set("excluded", v)} />
              </Field>
            </section>

            <section className="space-y-3">
              <h3 className="text-[13px] font-semibold">Failures and savings</h3>
              <Toggle label="Fall back on failure" term="fallback" hint="Off: the chosen model's error goes straight back to the caller." checked={f.fallback} onChange={(v) => set("fallback", v)} />
              <div className="grid grid-cols-2 gap-4">
                <Field label="Max attempts" htmlFor="p-attempts">
                  <Input id="p-attempts" inputMode="numeric" className="tabular" value={f.attempts} disabled={!f.fallback} onChange={(e) => set("attempts", e.target.value)} />
                </Field>
                <Field label="Baseline model" hint="What savings are measured against. Default: the priciest capable model.">
                  <SimpleSelect
                    aria-label="Baseline model"
                    value={f.baseline}
                    onValueChange={(v) => set("baseline", v === "__auto" ? "" : v)}
                    placeholder="Most expensive capable"
                    options={[{ value: "__auto", label: "Most expensive capable" }, ...aliases]}
                  />
                </Field>
              </div>
              <Toggle label="Downgrade on a TokenShield budget refusal" term="budgetDowngrade" hint="Retry only cheaper models that still clear the floor." checked={f.downgrade} onChange={(v) => set("downgrade", v)} />
            </section>
            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Saving..." : editing ? "Save policy" : (
                <>
                  <PlusIcon />
                  Create policy
                </>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Create and update behind one save call, so the dialog has a single pending state. */
function useCreatePolicyBridge() {
  const create = useCreatePolicy();
  const update = useUpdatePolicy();
  return {
    pending: create.isPending || update.isPending,
    save(
      req: { id: string; body: PolicyUpdate } | { body: PolicyIn },
      onSuccess: () => void,
      onError: (e: unknown) => void,
    ) {
      if ("id" in req) update.mutate(req, { onSuccess, onError });
      else create.mutate(req.body, { onSuccess, onError });
    },
  };
}
