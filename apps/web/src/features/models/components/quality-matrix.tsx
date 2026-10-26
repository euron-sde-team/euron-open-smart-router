"use client";

import { GaugeIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { Segmented } from "@/components/segmented";
import { Surface, SurfaceHeader } from "@/components/surface";
import { TableSkeleton } from "@/components/table-skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/errors";
import type { QualityCell } from "@/lib/api/types";
import { qualityTone, type Tone } from "@/lib/chips";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { formatQuality, TASKS, taskLabel } from "@/lib/models";

import { useModels, useQuality, useSetQuality } from "../api";

const TONE_CELL: Record<Tone, string> = {
  success: "bg-savings-soft text-savings",
  savings: "bg-savings-soft text-savings",
  info: "bg-info-soft text-info",
  primary: "bg-primary-soft text-primary",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  neutral: "bg-muted text-faint",
};

type Band = "low" | "high";
interface Target {
  model: string;
  task: string;
  band: Band;
  cell?: QualityCell;
}

function Score({ cell, onClick, label }: { cell: QualityCell | undefined; onClick?: () => void; label: string }) {
  const tone: Tone = cell ? qualityTone(cell.score) : "neutral";
  const title = cell
    ? `${label}: ${formatQuality(cell.score)} from ${cell.source === "manual" ? "a manual override" : `${cell.samples} benchmark cases`}, ${formatDateTime(cell.updated_at)}`
    : `${label}: not measured; the router uses the tier prior`;
  const content = (
    <>
      {cell ? formatQuality(cell.score) : "n/a"}
      {cell?.source === "manual" && <span className="ml-0.5 align-super text-[8px]">M</span>}
    </>
  );
  const cls = cn("flex h-7 min-w-11 items-center justify-center rounded-md px-1.5 text-[12px] font-semibold tabular", TONE_CELL[tone]);
  return onClick ? (
    <button type="button" title={title} aria-label={title} onClick={onClick} className={cn(cls, "transition hover:brightness-95 dark:hover:brightness-125")}>
      {content}
    </button>
  ) : (
    <span title={title} className={cls}>
      {content}
    </span>
  );
}

function OverrideDialog({ target, onClose }: { target: Target | null; onClose: () => void }) {
  const set = useSetQuality();
  const [score, setScore] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (target) {
      setScore(target.cell ? String(target.cell.score) : "");
      setError(null);
    }
  }, [target]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!target) return;
    const n = Number(score);
    if (!Number.isFinite(n) || n < 0 || n > 1 || score.trim() === "") return setError("Enter a score between 0 and 1, such as 0.82.");
    set.mutate(
      { model_alias: target.model, task: target.task, band: target.band, score: n },
      {
        onSuccess: () => {
          toast.success(`${target.model} ${target.task} (${target.band === "high" ? "hard" : "easy"}) set to ${n.toFixed(2)}. The next benchmark run replaces it.`);
          onClose();
        },
        onError: (err) => setError(errorMessage(err)),
      },
    );
  }

  return (
    <Dialog open={!!target} onOpenChange={(o) => !o && !set.isPending && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Set quality by hand</DialogTitle>
          <DialogDescription>
            For a model a benchmark cannot reach. {target && `${target.model}, ${taskLabel(target.task)}, ${target.band === "high" ? "hard" : "easy"} requests.`}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-h-0 flex-col" noValidate>
          <DialogBody className="space-y-3">
            <Field label="Score (0 to 1)" htmlFor="q-score" hint="Share of tasks of this kind the model gets right.">
              <Input id="q-score" inputMode="decimal" className="tabular" value={score} onChange={(e) => setScore(e.target.value)} autoFocus />
            </Field>
            {error && <FieldError>{error}</FieldError>}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={set.isPending}>
              {set.isPending ? "Saving..." : "Save score"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function QualityMatrix({ canWrite }: { canWrite: boolean }) {
  const quality = useQuality();
  const models = useModels();
  const [band, setBand] = React.useState<"both" | Band>("both");
  const [target, setTarget] = React.useState<Target | null>(null);
  const cells = React.useMemo(() => {
    const m = new Map<string, QualityCell>();
    for (const c of quality.data ?? []) m.set(`${c.model_alias}|${c.task}|${c.band}`, c);
    return m;
  }, [quality.data]);

  if (quality.isPending || models.isPending) return <Surface><TableSkeleton rows={5} cols={8} /></Surface>;
  if (quality.isError && !quality.data) return <Surface><ErrorState error={quality.error} onRetry={() => void quality.refetch()} /></Surface>;
  const aliases = (models.data ?? []).map((m) => m.alias);
  if (!quality.data?.length)
    return (
      <Surface>
        <EmptyState
          icon={GaugeIcon}
          title="Nothing measured yet"
          description="Quality comes from benchmark runs: every model answers graded cases per task and difficulty. Until then the router ranks by each model's tier prior."
          action={<Link href="/benchmarks" className={buttonVariants({ size: "sm" })}>Run a benchmark</Link>}
        />
      </Surface>
    );

  const bands: Band[] = band === "both" ? ["low", "high"] : [band];
  return (
    <Surface className="overflow-hidden">
      <SurfaceHeader
        title="Quality by task and difficulty"
        titleExtra={<Hint term="qualitySource" />}
        description={
          band === "both"
            ? "Each cell: easy requests (complexity 1 to 3) on the left, hard (4 and 5) on the right. M marks a manual score."
            : `Scores on ${band === "high" ? "hard" : "easy"} requests. M marks a manual score.`
        }
        action={
          <Segmented
            ariaLabel="Difficulty"
            value={band}
            onChange={setBand}
            options={[
              { value: "both", label: "Both" },
              { value: "low", label: "Easy" },
              { value: "high", label: "Hard" },
            ]}
          />
        }
      />
      <div className="overflow-x-auto px-5 pb-5">
        <table className="w-full min-w-[720px] border-separate border-spacing-y-1.5 text-[13px]">
          <caption className="sr-only">Quality scores by model, task and difficulty</caption>
          <thead>
            <tr className="text-left text-[11.5px] font-medium text-muted-foreground">
              <th className="pr-3 font-medium">Model</th>
              {TASKS.map((t) => (
                <th key={t} className="px-1 text-center font-medium">
                  {taskLabel(t)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {aliases.map((alias) => (
              <tr key={alias}>
                <td className="pr-3 font-mono text-[12.5px] font-medium whitespace-nowrap">{alias}</td>
                {TASKS.map((task) => (
                  <td key={task} className="px-1">
                    <div className="flex justify-center gap-1">
                      {bands.map((b) => {
                        const cell = cells.get(`${alias}|${task}|${b}`);
                        const label = `${alias}, ${task}, ${b === "high" ? "hard" : "easy"}`;
                        return (
                          <Score
                            key={b}
                            cell={cell}
                            label={label}
                            onClick={canWrite ? () => setTarget({ model: alias, task, band: b, cell }) : undefined}
                          />
                        );
                      })}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
          {(
            [
              ["success", "0.80 and above"],
              ["info", "0.70 to 0.79"],
              ["warning", "0.55 to 0.69"],
              ["danger", "below 0.55"],
            ] as const
          ).map(([tone, text]) => (
            <span key={tone} className="flex items-center gap-1.5">
              <span className={cn("size-3 rounded-sm", TONE_CELL[tone])} aria-hidden />
              {text}
            </span>
          ))}
          <span>Policies set their own floors; the default policy needs 0.80.</span>
        </div>
      </div>
      <OverrideDialog target={target} onClose={() => setTarget(null)} />
    </Surface>
  );
}
