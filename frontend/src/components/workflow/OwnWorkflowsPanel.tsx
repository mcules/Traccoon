import { useState } from "react";
import { tr } from "../../i18n";
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
      <p className="text-sm text-muted">{tr("own_workflows_panel.einleitung")}</p>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      {eigene.length > 0 ? (
        /* Keine Tabelle: auf einem Handy stünden fünf Spalten über den Rand hinaus, und was
           man nicht sieht, sucht man auch nicht. Die Zeile bricht stattdessen um. */
        <div className="space-y-2">
          {eigene.map((d) => (
            <div key={d.id}
              className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-card p-2 text-sm ${
                d.enabled ? "" : "opacity-50"}`}>
              <span className="font-mono text-xs text-muted">{d.key}</span>
              <span className="min-w-0 flex-1 basis-full truncate sm:basis-auto">{d.name}</span>
              <span className="text-xs text-muted">{d.subject_kind}</span>
              <span className="text-xs text-muted">
                {tr(d.current_version_id ? "proc.veroeffentlicht" : "own_workflows.nur_entwurf")}
              </span>
              <div className="ml-auto flex shrink-0 gap-1">
                <button onClick={() => nav(`/workflows/${d.id}`, { state: { from: "/processes/eigene" } })}
                  className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
                  {tr("own_workflows_panel.editor")}
                </button>
                <button onClick={() => umschalten.mutate(d)}
                  className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
                  {tr(d.enabled ? "own_workflows_panel.aus" : "own_workflows_panel.an")}
                </button>
                <button onClick={() => { if (confirm(tr("own_workflows.loeschen_frage", { key: d.key }))) loeschen.mutate(d.id); }}
                  className="rounded border border-line px-2 py-1 text-xs text-red-400 hover:bg-surface">
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-sm text-muted">{tr("own_workflows_panel.noch_keine_eigenen_prozesse")}</div>
      )}

      <div className="space-y-2 border-t border-line pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })}
            placeholder={tr("own_workflows_panel.key_platzhalter")} className={inp} />
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder={tr("own_workflows_panel.name")} className={inp} />
          <select value={f.template} className={inp}
            onChange={(e) => setF({ ...f, template: e.target.value })}>
            <option value="">{tr("own_workflows.leeres_geruest")}</option>
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
            placeholder={tr("own_workflows_panel.beschreibung_optional")} className={`${inp} min-w-48 flex-1`} />
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
