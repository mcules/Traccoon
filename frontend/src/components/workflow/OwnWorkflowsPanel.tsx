import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowDefinition, WorkflowSubjectKind } from "./types";

const EMPTY = { key: "", name: "", subject_kind: "standalone" as WorkflowSubjectKind,
                description: "", template: "" };
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";

/**
 * Eigene, projektlose Prozesse: alles, was zu keinem Projekt und zu keinem Slot gehört.
 *
 * Gedacht für Abläufe, die kein Ticket als Gegenstand haben (Gegenstand `standalone`) und von
 * einem Job, einem Webhook oder einem Agenten angestoßen werden — z. B. ein nächtlicher
 * Preisabgleich mit Freigabe-Schritt.
 *
 * Angelegt wird entweder ein Gerüst (Start + Ende) oder eine **Vorlage**: ein fertiger
 * Ablauf zum Umbauen. Das Gerüst beantwortet nicht, wie man aus zwei Knoten etwas macht,
 * das wirklich läuft — die Vorlagen zeigen die vier Muster, aus denen fast jeder eigene
 * Ablauf besteht. Veröffentlicht wird in beiden Fällen erst im Editor.
 */
export default function OwnWorkflowsPanel() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState("");

  const { data: alle } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  const { data: vorlagen } = useQuery({
    queryKey: ["workflow-templates"], queryFn: workflowApi.templates, staleTime: 30 * 60_000 });
  const gewaehlt = (vorlagen || []).find((v) => v.key === f.template);
  // Slot-Abläufe stehen oben im Prozess-Satz, Projekt-Abläufe im jeweiligen Projekt.
  const eigene = (alle || []).filter((d) => d.project_id === null && !d.slot && !d.archived_at);

  const inv = () => qc.invalidateQueries({ queryKey: ["workflows-all"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const anlegen = useMutation({
    mutationFn: () => workflowApi.create({
      project_id: null, key: f.key.trim(), name: f.name.trim(),
      subject_kind: f.subject_kind, description: f.description.trim() || undefined,
      template: f.template || undefined,
    }),
    onSuccess: (d) => { setF(EMPTY); setErr(""); inv(); nav(`/workflows/${d.id}`, { state: { from: "/processes/eigene" } }); },
    onError: fail,
  });
  const umschalten = useMutation({
    mutationFn: (d: WorkflowDefinition) => workflowApi.update(d.id, { enabled: !d.enabled }),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => workflowApi.del(id),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });

  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-sm text-muted">
        <b>Eigene Prozesse</b> — projektlos, ohne Slot. Auslösen kannst du sie über einen{" "}
        <b>Job</b> (Einstellungen → Jobs, Art <code>workflow</code>), einen <b>Webhook</b>{" "}
        (Modus <code>workflow</code>) oder einen <b>Agenten</b> (<code>traccoon_start_workflow</code>,
        einmalige Freigabe nötig). Startbar erst mit veröffentlichter Version.
      </p>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      {eigene.length > 0 ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase text-muted">
              <th className="py-2">Key</th><th>Name</th><th>Gegenstand</th><th>Version</th><th />
            </tr>
          </thead>
          <tbody>
            {eigene.map((d) => (
              <tr key={d.id} className={`border-b border-line ${d.enabled ? "" : "opacity-50"}`}>
                <td className="py-2 pr-2 font-mono text-xs">{d.key}</td>
                <td className="pr-2">{d.name}</td>
                <td className="pr-2 text-muted">{d.subject_kind}</td>
                <td className="pr-2 text-muted">
                  {d.current_version_id ? "veröffentlicht" : "nur Entwurf"}
                </td>
                <td className="whitespace-nowrap py-2 text-right">
                  <button onClick={() => nav(`/workflows/${d.id}`, { state: { from: "/processes/eigene" } })}
                    className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
                    Editor
                  </button>
                  <button onClick={() => umschalten.mutate(d)}
                    className="ml-1 rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
                    {d.enabled ? "Aus" : "An"}
                  </button>
                  <button onClick={() => { if (confirm(`Prozess '${d.key}' löschen?`)) loeschen.mutate(d.id); }}
                    className="ml-1 rounded border border-line px-2 py-1 text-xs text-red-400 hover:bg-surface">
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="text-sm text-muted">Noch keine eigenen Prozesse.</div>
      )}

      <div className="space-y-2 border-t border-line pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })}
            placeholder="key (z. B. preis-abgleich)" className={inp} />
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder="Name" className={inp} />
          <select value={f.template} className={inp}
            onChange={(e) => setF({ ...f, template: e.target.value })}>
            <option value="">leeres Gerüst (Start + Ende)</option>
            {(vorlagen || []).map((v) => (
              <option key={v.key} value={v.key}>Vorlage: {v.name}</option>
            ))}
          </select>
          {!f.template && (
            <select value={f.subject_kind} className={inp}
              onChange={(e) => setF({ ...f, subject_kind: e.target.value as WorkflowSubjectKind })}>
              <option value="standalone">standalone (kein Gegenstand)</option>
              <option value="issue">issue (Ticket)</option>
              <option value="hardware_asset">hardware_asset</option>
            </select>
          )}
          <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })}
            placeholder="Beschreibung (optional)" className={`${inp} min-w-48 flex-1`} />
          <button onClick={() => anlegen.mutate()}
            disabled={!f.key.trim() || !f.name.trim() || anlegen.isPending}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white disabled:opacity-50">
            Anlegen
          </button>
        </div>
        {gewaehlt && (
          <p className="text-xs text-muted">
            <b>{gewaehlt.name}</b> — {gewaehlt.description}{" "}
            <span className="text-brand">{gewaehlt.hinweis}</span>
          </p>
        )}
      </div>
    </div>
  );
}
