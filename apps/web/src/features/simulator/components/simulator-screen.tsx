"use client";

import { FlaskConicalIcon, PlayIcon, SparklesIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { AXES, CandidateRow } from "@/features/decisions/components/decision-drawer";
import { useModels } from "@/features/models/api";
import { usePolicies } from "@/features/policies/api";
import type { Candidate, SimulateIn, SimulateOut } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatNumber, formatPercent, formatUsd } from "@/lib/format";
import { formatQuality, modelColors, STRATEGY_LABEL, TASKS, taskLabel } from "@/lib/models";

import { useSimulate } from "../api";

const EXAMPLES = [
  { label: "Classify a review", prompt: "Classify the sentiment as positive or negative. One word.\n\nReview: The battery lasts all day and the screen is gorgeous." },
  { label: "Extract JSON", prompt: "Extract the order id and the customer email as JSON.\n\nOrder A-7741 was placed by ana.lima@example.com on 3 October." },
  { label: "Hard coding task", prompt: "Implement a production-ready, thread-safe LRU cache class in Python with O(1) get and put. Handle edge cases and explain the concurrency trade-offs." },
  { label: "Maths", prompt: "Solve 3x + 7 = 22 and show each step." },
  { label: "Translate", prompt: "Translate into French: Could we move our meeting to Thursday afternoon?" },
];
const AUTO = "auto";
const DETECT = "__detect";

function Result({ r, color }: { r: SimulateOut; color: (a: string) => string }) {
  const candidates = r.candidates as unknown as Candidate[];
  const eligible = candidates.filter((c) => c.eligible);
  const filtered = candidates.filter((c) => !c.eligible);
  const max = Math.max(0.0001, ...eligible.map((c) => c.score));
  const a = r.analysis;
  return (
    <div className="space-y-4">
      <Surface className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[12px] font-medium text-muted-foreground">
              {r.passthrough
                ? "Passed through unrouted"
                : `Policy ${r.policy}${r.policy === r.strategy ? "" : ` (${STRATEGY_LABEL[r.strategy] ?? r.strategy})`} would choose`}
            </div>
            <div className="mt-1 flex items-center gap-2">
              {r.selected && <span aria-hidden className="size-3 rounded-full" style={{ background: color(r.selected) }} />}
              <span className="font-mono text-[22px] font-semibold tracking-[-0.02em]">{r.selected ?? "No eligible model"}</span>
              {r.floor_relaxed && <Chip tone="warning">Floor relaxed</Chip>}
            </div>
            <p className="mt-1 max-w-xl text-[13px] text-muted-foreground">{eligible[0]?.reason ?? "Nothing passed the policy's filters; see the reasons below."}</p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-right">
            {(
              [
                ["Estimated", formatUsd(r.estimated_cost_usd), ""],
                ["Baseline", formatUsd(r.baseline_cost_usd), r.baseline_model ?? ""],
                ["Saving", r.estimated_savings_pct != null ? formatPercent(r.estimated_savings_pct) : "n/a", ""],
              ] as const
            ).map(([label, value, sub]) => (
              <div key={label} className="rounded-xl bg-muted/60 px-3 py-2">
                <div className="text-[11.5px] text-muted-foreground">{label}</div>
                <div className={cn("text-[15px] font-semibold tabular", label === "Saving" && (r.estimated_savings_pct ?? 0) > 0 && "text-savings")}>{value}</div>
                {sub && <div className="font-mono text-[11px] text-subtle">{sub}</div>}
              </div>
            ))}
          </div>
        </div>
      </Surface>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <Surface>
          <SurfaceHeader title="What the analyzer saw" titleExtra={<Hint term="complexity" />} />
          <div className="space-y-3 px-5 pb-5">
            <div className="flex flex-wrap gap-1.5">
              <Chip tone="primary">{taskLabel(a.task)}</Chip>
              <Chip tone={a.complexity >= 4 ? "warning" : "neutral"}>Complexity {a.complexity} of 5</Chip>
              <Chip tone="neutral">{a.band === "high" ? "Hard" : "Easy"} band</Chip>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-[12.5px]">
              <div>
                <dt className="text-[11.5px] text-subtle">Input tokens</dt>
                <dd className="font-medium tabular">~{formatNumber(a.input_tokens)}</dd>
              </div>
              <div>
                <dt className="text-[11.5px] text-subtle">Output allowance</dt>
                <dd className="font-medium tabular">{formatNumber(a.output_tokens)}</dd>
              </div>
              <div>
                <dt className="text-[11.5px] text-subtle">Quality floor</dt>
                <dd className="font-medium tabular">{formatQuality(r.floor)}</dd>
              </div>
              <div>
                <dt className="text-[11.5px] text-subtle">Routing time</dt>
                <dd className="font-medium tabular">{formatNumber(r.routing_us)} µs</dd>
              </div>
            </dl>
            <div className="flex flex-wrap gap-1.5">
              {a.needs_tools && <Chip tone="info">Needs tools</Chip>}
              {a.needs_vision && <Chip tone="info">Needs vision</Chip>}
              {a.needs_json && <Chip tone="info">Needs JSON</Chip>}
            </div>
            <ul className="space-y-1 text-[12.5px] text-muted-foreground">
              {a.signals.map((s) => (
                <li key={s} className="flex gap-2">
                  <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-faint" />
                  {s}
                </li>
              ))}
            </ul>
          </div>
        </Surface>
        <Surface>
          <SurfaceHeader
            title={`Ranking: ${eligible.length} eligible of ${candidates.length}`}
            titleExtra={<Hint term="pHard" />}
            description="The first eligible model answers; the next ones are the fallback order."
            action={
              <div className="hidden flex-wrap gap-3 text-[11.5px] text-muted-foreground sm:flex">
                {AXES.map((ax) => (
                  <span key={ax.key} className="flex items-center gap-1">
                    <span aria-hidden className="h-1.5 w-3 rounded-full" style={{ background: ax.color }} />
                    {ax.label}
                  </span>
                ))}
              </div>
            }
          />
          <ol className="space-y-1.5 px-5 pb-5">
            {eligible.map((c, i) => (
              <CandidateRow key={c.model} c={c} rank={i + 1} max={max} color={color(c.model)} final={i === 0} />
            ))}
            {filtered.map((c) => (
              <CandidateRow key={c.model} c={c} rank={0} max={max} color={color(c.model)} final={false} />
            ))}
          </ol>
        </Surface>
      </div>
    </div>
  );
}

export function SimulatorScreen() {
  const policies = usePolicies();
  const models = useModels();
  const sim = useSimulate();
  const color = modelColors((models.data ?? []).map((m) => m.alias));
  const [prompt, setPrompt] = React.useState(EXAMPLES[2]?.prompt ?? "");
  const [system, setSystem] = React.useState("");
  const [route, setRoute] = React.useState(AUTO);
  const [task, setTask] = React.useState(DETECT);
  const [complexity, setComplexity] = React.useState(DETECT);
  const [maxTokens, setMaxTokens] = React.useState("");
  const [tools, setTools] = React.useState(false);
  const [json, setJson] = React.useState(false);

  function run(e?: React.FormEvent) {
    e?.preventDefault();
    if (!prompt.trim()) return;
    const messages: SimulateIn["messages"] = [];
    if (system.trim()) messages.push({ role: "system", content: system.trim() });
    messages.push({ role: "user", content: prompt });
    const max = Number(maxTokens);
    sim.mutate({
      model: route,
      messages,
      max_tokens: maxTokens.trim() && Number.isInteger(max) && max > 0 ? max : null,
      task: task === DETECT ? null : task,
      complexity: complexity === DETECT ? null : Number(complexity),
      tools: tools ? [{ type: "function", function: { name: "lookup", parameters: { type: "object" } } }] : null,
      response_format: json ? { type: "json_object" } : null,
    });
  }

  const routeOptions = [
    { value: AUTO, label: "auto (default policy)" },
    ...(policies.data ?? []).map((p) => ({ value: `auto:${p.name}`, label: `auto:${p.name}` })),
    ...(models.data ?? []).map((m) => ({ value: m.alias, label: `${m.alias} (pinned)` })),
  ];

  return (
    <>
      <PageHeader
        title="Route simulator"
        description="Ask the router what it would do with a request, without calling any model or spending anything. The same engine and data the gateway uses."
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
        <Surface className="h-fit">
          <form onSubmit={run} className="space-y-4 p-5">
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  type="button"
                  onClick={() => setPrompt(ex.prompt)}
                  className={cn(
                    "inline-flex h-7 items-center gap-1 rounded-full bg-muted px-2.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground",
                    prompt === ex.prompt && "bg-primary-soft text-primary",
                  )}
                >
                  <SparklesIcon className="size-3" />
                  {ex.label}
                </button>
              ))}
            </div>
            <Field label="Prompt" htmlFor="sim-prompt">
              <textarea
                id="sim-prompt"
                rows={7}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="w-full resize-y rounded-lg bg-card px-3 py-2 text-[13px] shadow-[var(--elev-1)] ring-1 ring-input outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </Field>
            <Field label="System prompt" htmlFor="sim-system" hint="Optional. Counts at half weight when detecting the task.">
              <Input id="sim-system" value={system} onChange={(e) => setSystem(e.target.value)} placeholder="You are a helpful support agent." />
            </Field>
            <Field label="Model" htmlFor="sim-route" hint="A policy routes; a model name is passed through as is.">
              <SimpleSelect id="sim-route" value={route} onValueChange={setRoute} options={routeOptions} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Task" htmlFor="sim-task">
                <SimpleSelect id="sim-task" value={task} onValueChange={setTask} options={[{ value: DETECT, label: "Detect" }, ...TASKS.map((t) => ({ value: t, label: taskLabel(t) }))]} />
              </Field>
              <Field label="Complexity" htmlFor="sim-complexity">
                <SimpleSelect
                  id="sim-complexity"
                  value={complexity}
                  onValueChange={setComplexity}
                  options={[{ value: DETECT, label: "Detect" }, ...["1", "2", "3", "4", "5"].map((c) => ({ value: c, label: c }))]}
                />
              </Field>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Max tokens" htmlFor="sim-max">
                <Input id="sim-max" inputMode="numeric" className="tabular" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="Auto" />
              </Field>
              <label className="flex flex-col gap-2 text-[13px] font-medium">
                Tools
                <Switch checked={tools} onCheckedChange={setTools} aria-label="Request needs tools" />
              </label>
              <label className="flex flex-col gap-2 text-[13px] font-medium">
                JSON
                <Switch checked={json} onCheckedChange={setJson} aria-label="Request needs JSON output" />
              </label>
            </div>
            <Button type="submit" className="w-full" disabled={sim.isPending || !prompt.trim()}>
              <PlayIcon />
              {sim.isPending ? "Ranking..." : "Simulate routing"}
            </Button>
          </form>
        </Surface>
        <div className="min-w-0">
          {sim.isError ? (
            <Surface>
              <ErrorState error={sim.error} onRetry={() => run()} />
            </Surface>
          ) : sim.data ? (
            <Result r={sim.data} color={color} />
          ) : (
            <Surface>
              <EmptyState
                icon={FlaskConicalIcon}
                title="See a routing decision before you send it"
                description="Pick an example or paste your own prompt, then simulate. You get the detected task and difficulty, every model's score and the reason it was chosen or filtered."
                action={
                  <Button size="sm" onClick={() => run()}>
                    <PlayIcon />
                    Simulate this prompt
                  </Button>
                }
              />
            </Surface>
          )}
        </div>
      </div>
    </>
  );
}
