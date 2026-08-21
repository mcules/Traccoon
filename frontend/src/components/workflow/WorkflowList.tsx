import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi, type Project, type WorkflowSubjectKind } from "../../api";
import { projectPath } from "../../projectTabs";
import { Listing, ListingEmpty, ListenLine, BUTTON} from "../ui";

const SUBJECT_LABEL: Record<WorkflowSubjectKind, string> = {
  issue: "Ticket",
  hardware_asset: "Hardware",
  standalone: "workflow_list.eigenstaendig",
};

const EMPTY = { key: "", name: "", subject_kind: "issue" as WorkflowSubjectKind, description: "" };

/** CRUD panel for the workflow definitions of a project. */
export default function WorkflowList({ project }: { project: Project }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data: all } = useQuery({
    queryKey: ["workflows", project.id],
    queryFn: () => workflowApi.list(project.id),
  });
  // Flows with a slot stand at the top in the slot overview (with the origin and a reset);
  // here only the freely created ones.
  const defs = all?.filter((d) => !d.slot);
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["workflows", project.id] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const create = useMutation({
    mutationFn: () =>
      workflowApi.create({
        project_id: project.id,
        key: f.key,
        name: f.name,
        subject_kind: f.subject_kind,
        description: f.description || undefined,
      }),
    onSuccess: (d) => {
      setF(EMPTY);
      setErr("");
      inv();
      nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: projectPath(project.key, "settings", "processes") } });
    },
    onError: fail,
  });
  const toggle = useMutation({
    mutationFn: (d: { id: number; enabled: boolean }) => workflowApi.update(d.id, { enabled: !d.enabled }),
    onSuccess: inv,
    onError: fail,
  });
  const del = useMutation({
    mutationFn: (id: number) => workflowApi.del(id),
    onSuccess: inv,
    onError: fail,
  });

  const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";

  return (
    <div>
      <p className="mb-3 text-sm text-muted">
        {tr("workflow_list.einleitung")}
      </p>

      <Listing className="mb-4">
        {defs?.map((d) => (
          <ListenLine key={d.id} dimmed={!d.enabled}>
            <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-xs text-muted">{d.key}</span>
            <span className="font-medium">{d.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
              {SUBJECT_LABEL[d.subject_kind]}
            </span>
            {d.current_version_id ? (
              <span className="rounded bg-green-500/15 px-1.5 py-0.5 text-xs text-green-400">{tr("proc.veroeffentlicht")}</span>
            ) : (
              <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-yellow-400">{tr("workflow_list.entwurf")}</span>
            )}
            {!d.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("jobs_panel.aus")}</span>}
            <div className="flex-1" />
            <button
              title={d.enabled ? "Deaktivieren" : "Aktivieren"}
              onClick={() => toggle.mutate({ id: d.id, enabled: d.enabled })}
              className={ico}
            >
              {d.enabled ? "⏸" : "⏵"}
            </button>
            <button
              title={tr("workflow_list.bearbeiten")}
              onClick={() => nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: projectPath(project.key, "settings", "processes") } })}
              className={ico + " hover:opacity-80"}
            >
              ✎
            </button>
            <button title={tr("workflow_list.loeschen")} onClick={() => del.mutate(d.id)} className={"text-base leading-none text-red-400 hover:text-red-300"}>
              🗑
            </button>
            </div>
          </ListenLine>
        ))}
        {defs?.length === 0 && <ListingEmpty>{tr("workflow_list.noch_keine_eigenen_prozesse")}</ListingEmpty>}
      </Listing>

      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-card p-3">
        <input
          value={f.key}
          onChange={(e) => setF({ ...f, key: e.target.value })}
          placeholder={tr("workflow_list.schluessel_z_b_onboarding")}
          className={inp + " font-mono"}
        />
        <input
          value={f.name}
          onChange={(e) => setF({ ...f, name: e.target.value })}
          placeholder={tr("workflow_list.name")}
          className={inp}
        />
        <select
          value={f.subject_kind}
          onChange={(e) => setF({ ...f, subject_kind: e.target.value as WorkflowSubjectKind })}
          className={inp}
        >
          <option value="issue">{tr("workflow_list.ticket_prozess")}</option>
          <option value="hardware_asset">{tr("workflow_list.hardware_prozess")}</option>
          <option value="standalone">{tr("workflow_list.eigenstaendig")}</option>
        </select>
        <input
          value={f.description}
          onChange={(e) => setF({ ...f, description: e.target.value })}
          placeholder={tr("workflow_list.beschreibung_optional")}
          className={inp}
        />
        <button
          onClick={() => f.key.trim() && f.name.trim() && create.mutate()}
          className={BUTTON.primary}
        >
          + Prozess anlegen &amp; bearbeiten
        </button>
        {err && <div className="col-span-2 text-sm text-red-400">{err}</div>}
      </div>
    </div>
  );
}

// Auch hier: eine Handlung ist blau, Grau bliebe fuer abgeschaltet.
const ico = "text-base leading-none text-brand transition-colors";
