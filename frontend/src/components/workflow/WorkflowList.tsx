import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi, type Project, type WorkflowSubjectKind } from "../../api";

const SUBJECT_LABEL: Record<WorkflowSubjectKind, string> = {
  issue: "Ticket",
  hardware_asset: "Hardware",
  standalone: "eigenständig",
};

const EMPTY = { key: "", name: "", subject_kind: "issue" as WorkflowSubjectKind, description: "" };

/** CRUD-Panel für Workflow-Definitionen eines Projekts. */
export default function WorkflowList({ project }: { project: Project }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { data: alle } = useQuery({
    queryKey: ["workflows", project.id],
    queryFn: () => workflowApi.list(project.id),
  });
  // Abläufe mit Slot stehen oben in der Slot-Übersicht (mit Herkunft und Zurücksetzen) —
  // hier nur die frei angelegten.
  const defs = alle?.filter((d) => !d.slot);
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
      nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: `/projects/${project.key}?tab=workflows` } });
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
        Zusätzliche Prozesse dieses Projekts — etwa für Webhooks oder geplante Jobs. Ein Prozess
        wird als <b>Entwurf</b> bearbeitet und muss <b>veröffentlicht</b> werden, bevor Vorgänge
        daraus starten können.
      </p>

      <div className="mb-4 space-y-2">
        {defs?.map((d) => (
          <div
            key={d.id}
            className={`flex flex-wrap items-center gap-3 rounded border border-line bg-card p-2 text-sm ${
              d.enabled ? "" : "opacity-50"
            }`}
          >
            <span className="font-mono text-xs text-muted">{d.key}</span>
            <span className="font-medium">{d.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
              {SUBJECT_LABEL[d.subject_kind]}
            </span>
            {d.current_version_id ? (
              <span className="rounded bg-green-500/15 px-1.5 py-0.5 text-xs text-green-400">veröffentlicht</span>
            ) : (
              <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-yellow-400">Entwurf</span>
            )}
            {!d.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">aus</span>}
            <div className="flex-1" />
            <button
              title={d.enabled ? "Deaktivieren" : "Aktivieren"}
              onClick={() => toggle.mutate({ id: d.id, enabled: d.enabled })}
              className={ico}
            >
              {d.enabled ? "⏸" : "⏵"}
            </button>
            <button
              title="Bearbeiten"
              onClick={() => nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: `/projects/${project.key}?tab=workflows` } })}
              className={ico + " hover:text-brand"}
            >
              ✎
            </button>
            <button title="Löschen" onClick={() => del.mutate(d.id)} className={ico + " hover:text-red-400"}>
              🗑
            </button>
          </div>
        ))}
        {defs?.length === 0 && <div className="text-xs text-muted">Noch keine eigenen Prozesse.</div>}
      </div>

      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-card p-3">
        <input
          value={f.key}
          onChange={(e) => setF({ ...f, key: e.target.value })}
          placeholder="Schlüssel (z. B. onboarding)"
          className={inp + " font-mono"}
        />
        <input
          value={f.name}
          onChange={(e) => setF({ ...f, name: e.target.value })}
          placeholder="Name"
          className={inp}
        />
        <select
          value={f.subject_kind}
          onChange={(e) => setF({ ...f, subject_kind: e.target.value as WorkflowSubjectKind })}
          className={inp}
        >
          <option value="issue">Ticket-Prozess</option>
          <option value="hardware_asset">Hardware-Prozess</option>
          <option value="standalone">Eigenständig</option>
        </select>
        <input
          value={f.description}
          onChange={(e) => setF({ ...f, description: e.target.value })}
          placeholder="Beschreibung (optional)"
          className={inp}
        />
        <button
          onClick={() => f.key.trim() && f.name.trim() && create.mutate()}
          className="col-span-2 rounded bg-brand py-1.5 text-white"
        >
          + Prozess anlegen &amp; bearbeiten
        </button>
        {err && <div className="col-span-2 text-sm text-red-400">{err}</div>}
      </div>
    </div>
  );
}

const ico = "text-base leading-none text-muted hover:text-ink";
