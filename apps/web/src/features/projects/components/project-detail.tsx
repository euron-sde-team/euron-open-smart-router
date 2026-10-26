"use client";

import { ArchiveIcon, ArrowLeftIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { ErrorState } from "@/components/error-state";
import { Hint } from "@/components/hint";
import { PageHeader } from "@/components/page-header";
import { Surface, SurfaceHeader } from "@/components/surface";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { Field, FieldError } from "@/components/ui/label";
import { SimpleSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermissions } from "@/features/auth/api";
import { CreateKeyDialog } from "@/features/keys/components/create-key-dialog";
import { KeysTable } from "@/features/keys/components/keys-table";
import { useOverview } from "@/features/overview/api";
import { usePolicies } from "@/features/policies/api";
import { ActivityPanel } from "@/features/overview/components/activity-panel";
import { BreakdownPanel } from "@/features/overview/components/breakdown-panel";
import { KpiGrid } from "@/features/overview/components/kpi-grid";
import { useTeams } from "@/features/teams/api";
import { errorMessage } from "@/lib/api/errors";
import type { Project } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { useUiPrefs } from "@/lib/store";

import { useProject, useUpdateProject } from "../api";

function RoutingSettings({ project, canWrite }: { project: Project; canWrite: boolean }) {
  const policies = usePolicies();
  const teams = useTeams();
  const update = useUpdateProject();
  const [policy, setPolicy] = React.useState(project.default_policy ?? "");
  const [teamId, setTeamId] = React.useState(project.team_id ?? "");
  const [error, setError] = React.useState<string | null>(null);
  const globalDefault = policies.data?.find((x) => x.is_default)?.name;

  React.useEffect(() => {
    setPolicy(project.default_policy ?? "");
    setTeamId(project.team_id ?? "");
  }, [project.default_policy, project.team_id]);

  const dirty = policy !== (project.default_policy ?? "") || teamId !== (project.team_id ?? "");

  function save() {
    setError(null);
    update.mutate(
      { id: project.id, body: { default_policy: policy || null, team_id: teamId || null } },
      { onSuccess: () => toast.success("Routing settings saved."), onError: (e) => setError(errorMessage(e)) },
    );
  }

  return (
    <Surface>
      <SurfaceHeader
        title="Routing"
        titleExtra={<Hint term="auto" />}
        description='Which policy model "auto" uses for this project. A request can still name another with "auto:<policy>".'
      />
      <div className="grid gap-4 px-5 pb-5 sm:grid-cols-2">
        <Field label="Default policy" htmlFor="project-policy" hint={policy ? undefined : `Uses the global default${globalDefault ? ` (${globalDefault})` : ""}.`}>
          <SimpleSelect
            id="project-policy"
            value={policy}
            disabled={!canWrite}
            onValueChange={setPolicy}
            options={[{ value: "", label: "Global default" }, ...(policies.data ?? []).map((x) => ({ value: x.name, label: x.name }))]}
          />
        </Field>
        <Field label="Team" htmlFor="project-team">
          <SimpleSelect
            id="project-team"
            value={teamId}
            disabled={!canWrite}
            onValueChange={setTeamId}
            options={[{ value: "", label: "No team" }, ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))]}
          />
        </Field>
        {error && <FieldError className="sm:col-span-2">{error}</FieldError>}
        {canWrite && (
          <div className="sm:col-span-2">
            <Button size="sm" disabled={!dirty || update.isPending} onClick={save}>
              {update.isPending ? "Saving..." : "Save routing"}
            </Button>
          </div>
        )}
      </div>
    </Surface>
  );
}

function RenameForm({ project, canWrite }: { project: Project; canWrite: boolean }) {
  const update = useUpdateProject();
  const [name, setName] = React.useState(project.name);
  const [archiveOpen, setArchiveOpen] = React.useState(false);
  React.useEffect(() => setName(project.name), [project.name]);
  if (!canWrite) return null;
  return (
    <Surface>
      <SurfaceHeader title="Project settings" />
      <div className="flex flex-col gap-4 px-5 pb-5 sm:flex-row sm:items-end">
        <Field label="Name" htmlFor="project-rename" className="flex-1">
          <Input id="project-rename" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={!name.trim() || name.trim() === project.name || update.isPending}
            onClick={() =>
              update.mutate({ id: project.id, body: { name: name.trim() } }, { onSuccess: () => toast.success("Renamed."), onError: (e) => toast.error(errorMessage(e)) })
            }
          >
            Rename
          </Button>
          <Button variant="destructive-soft" onClick={() => setArchiveOpen(true)}>
            <ArchiveIcon />
            {project.archived ? "Unarchive" : "Archive"}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={archiveOpen}
        onOpenChange={setArchiveOpen}
        title={project.archived ? `Unarchive "${project.name}"?` : `Archive "${project.name}"?`}
        description={
          project.archived
            ? "Its keys start working again."
            : "Its keys stop working and it disappears from pickers. History and spend stay intact, and you can unarchive later."
        }
        confirmLabel={project.archived ? "Unarchive" : "Archive project"}
        destructive={!project.archived}
        pending={update.isPending}
        onConfirm={() =>
          update.mutate(
            { id: project.id, body: { archived: !project.archived } },
            {
              onSuccess: () => {
                toast.success(project.archived ? "Project restored." : "Project archived.");
                setArchiveOpen(false);
              },
              onError: (e) => toast.error(errorMessage(e)),
            },
          )
        }
      />
    </Surface>
  );
}

export function ProjectDetail({ id }: { id: string }) {
  const q = useProject(id);
  const range = useUiPrefs((s) => s.range);
  const overview = useOverview(range, id);
  const { canWrite } = usePermissions();
  const [keyOpen, setKeyOpen] = React.useState(false);

  const back = (
    <Link href="/projects" className="inline-flex items-center gap-1 text-[12.5px] font-medium text-muted-foreground hover:text-foreground">
      <ArrowLeftIcon className="size-3.5" />
      Projects
    </Link>
  );

  if (q.isPending) {
    return (
      <>
        <div className="mb-6 space-y-2">
          {back}
          <Skeleton className="h-7 w-56" />
          <Skeleton className="h-4 w-80" />
        </div>
        <Skeleton className="h-32 rounded-[14px]" />
      </>
    );
  }
  if (q.isError) {
    return (
      <>
        <div className="mb-4">{back}</div>
        <div className="surface">
          <ErrorState error={q.error} onRetry={() => void q.refetch()} title="Could not load this project" />
        </div>
      </>
    );
  }
  const p = q.data;
  return (
    <>
      <PageHeader
        eyebrow={back}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {p.name}
            {p.archived && <Chip tone="neutral">Archived</Chip>}
          </span>
        }
        description={
          <>
            {[p.description || "No description", p.team_name ? `Team ${p.team_name}` : null, `Created ${formatDate(p.created_at)}`]
              .filter(Boolean)
              .map((part) => String(part).replace(/[.\s]+$/, ""))
              .join(" · ")}
          </>
        }
        actions={
          canWrite && (
            <Button onClick={() => setKeyOpen(true)}>
              <PlusIcon />
              New key
            </Button>
          )
        }
      />
      <div className="flex flex-col gap-4">
        <KpiGrid data={overview.data} range={range} loading={overview.isPending} error={overview.error} onRetry={() => void overview.refetch()} />
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <ActivityPanel range={range} projectId={id} />
          </div>
          <BreakdownPanel
            range={range}
            projectId={id}
            tabs={[
              { by: "model", label: "Model" },
              { by: "task", label: "Task" },
              { by: "policy", label: "Policy" },
            ]}
          />
        </div>
        <Surface className="overflow-hidden">
          <SurfaceHeader title="API keys" description="Keys that bill to this project." />
          <KeysTable projectId={id} includeRevoked={false} canWrite={canWrite} onCreate={() => setKeyOpen(true)} showProject={false} />
        </Surface>
        <RoutingSettings project={p} canWrite={canWrite} />
        <RenameForm project={p} canWrite={canWrite} />
      </div>
      <CreateKeyDialog open={keyOpen} onOpenChange={setKeyOpen} projectId={id} />
    </>
  );
}
