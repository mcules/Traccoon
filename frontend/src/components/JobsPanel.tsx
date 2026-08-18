import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

const EMPTY = { name: "", type: "cron", schedule: "0 8 * * *", kind: "prompt",
                agent: "", prompt: "", command: "", notify_mode: "on_output", notify_chat: "",
                result_html: false, pause_on_success: false, run_timeout: 600,
                // Liste = Script-Argumente, Objekt = Parameter eines prompt-Jobs.
                args: [] as any[] | Record<string, any>,
                project_id: null as number | null,
                workflow_definition_id: null as number | null };

export default function JobsPanel() {
  const qc = useQueryClient();
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<any[]>("/jobs") });
  // Für kind=workflow: veröffentlichte Definitionen zur Auswahl.
  const { data: defs } = useQuery({
    queryKey: ["workflow-defs"],
    queryFn: () => api.get<{ id: number; name: string; key: string; current_version_id: number | null }[]>("/workflows"),
  });
  // Vorlagen füllen das Formular nur vor — der Job trägt danach seine eigenen Felder.
  const { data: templates } = useQuery({
    queryKey: ["job-templates"],
    queryFn: () => api.get<{ key: string; label: string; beschreibung: string;
                             params: Record<string, any>; felder: Record<string, any> }[]>("/jobs/templates"),
  });
  const [f, setF] = useState(EMPTY);
  const [editId, setEditId] = useState<number | null>(null);
  // Parameter als JSON-Text, damit ein Tippfehler beim Bearbeiten nicht sofort den Wert frisst.
  const [paramText, setParamText] = useState("");
  const paramFehler = (() => {
    if (!paramText.trim()) return "";
    try {
      const v = JSON.parse(paramText);
      return v && typeof v === "object" && !Array.isArray(v) ? "" : "Objekt erwartet, z.B. {\"thema\": \"…\"}";
    } catch { return "Kein gültiges JSON"; }
  })();
  const setParams = (text: string) => {
    setParamText(text);
    if (!text.trim()) { setF((p) => ({ ...p, args: [] })); return; }
    try {
      const v = JSON.parse(text);
      if (v && typeof v === "object" && !Array.isArray(v)) setF((p) => ({ ...p, args: v }));
    } catch { /* ungültig: Text stehen lassen, Job-Feld unverändert */ }
  };
  // Platzhalter ohne Wert — dieselbe Regel wie serverseitig (services/job_params).
  const EINGEBAUT = ["heute", "jetzt", "seit", "zeitfenster"];
  const fehlend = Array.from(new Set(
    [...(f.prompt || "").matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g)].map((m) => m[1]),
  )).filter((k) => !EINGEBAUT.includes(k) && !(f.args && !Array.isArray(f.args) && k in (f.args as any)));
  const useTemplate = (key: string) => {
    const t = templates?.find((x) => x.key === key);
    if (!t) return;
    setF((p) => ({ ...p, ...t.felder, args: t.params }));
    setParamText(JSON.stringify(t.params, null, 2));
  };
  const inv = () => qc.invalidateQueries({ queryKey: ["jobs"] });
  const save = useMutation({
    mutationFn: () => editId ? api.put(`/jobs/${editId}`, f) : api.post("/jobs", f),
    onSuccess: () => { setF(EMPTY); setEditId(null); setParamText(""); inv(); },
  });
  const run = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/run`), onSuccess: inv });
  const toggle = useMutation({
    mutationFn: (j: any) => api.post(`/jobs/${j.id}/enabled`, { enabled: !j.enabled }), onSuccess: inv });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/jobs/${id}`), onSuccess: inv });

  const edit = (j: any) => {
    setEditId(j.id);
    const p = j.args && !Array.isArray(j.args) ? j.args : null;
    setParamText(p ? JSON.stringify(p, null, 2) : "");
    setF({ name: j.name, type: j.type, schedule: j.schedule, kind: j.kind, agent: j.agent || "",
           prompt: j.prompt || "", command: j.command || "", notify_mode: j.notify_mode,
           notify_chat: j.notify_chat || "", result_html: !!j.result_html,
           pause_on_success: !!j.pause_on_success, run_timeout: j.run_timeout ?? 600,
           args: j.args || [], project_id: j.project_id ?? null,
           workflow_definition_id: j.workflow_definition_id ?? null });
  };

  return (
    <div>
      <p className="mb-3 text-sm text-muted">Geplante Jobs: <b>cron</b> (z.B. <span className="font-mono">0 8 * * *</span>),
        <b> interval</b> (Sekunden) oder <b>once</b> (ISO-Zeit). <b>prompt</b> = Agenten-Lauf,
        <b> script</b> = Programm, <b>workflow</b> = startet eine Prozess-Instanz.</p>
      <div className="mb-4 space-y-2">
        {jobs?.map((j) => (
          <div key={j.id} className={`flex items-center gap-3 rounded border border-line bg-card p-2 text-sm ${j.enabled ? "" : "opacity-50"}`}>
            <span>{j.name}</span>
            {!j.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">aus</span>}
            {j.enabled && j.paused && <span className="rounded bg-surface px-1 text-xs text-amber-400">pausiert</span>}
            <span className="text-xs text-muted font-mono">{j.type}:{j.schedule} · {j.kind}</span>
            {j.last_run_at && <span className="text-xs text-muted">zuletzt {new Date(j.last_run_at).toLocaleString()}</span>}
            <div className="flex-1" />
            <button title={j.enabled ? "Deaktivieren" : "Aktivieren"} onClick={() => toggle.mutate(j)}
              className={ico}>{j.enabled ? "⏸" : "⏵"}</button>
            <button title="Jetzt ausführen" onClick={() => run.mutate(j.id)} className={ico + " hover:text-brand"}>▶</button>
            <button title="Bearbeiten" onClick={() => edit(j)} className={ico}>✎</button>
            <button title="Löschen" onClick={() => del.mutate(j.id)} className={ico + " hover:text-red-400"}>🗑</button>
          </div>
        ))}
        {jobs?.length === 0 && <div className="text-xs text-muted">Keine Jobs.</div>}
      </div>
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-card p-3 text-sm">
        {editId && <div className="col-span-2 text-xs text-brand">Bearbeite Job #{editId} —
          <button onClick={() => { setEditId(null); setF(EMPTY); setParamText(""); }} className="ml-1 underline">abbrechen</button></div>}
        {!editId && !!templates?.length && (
          <select value="" onChange={(e) => e.target.value && useTemplate(e.target.value)}
            className={inp + " col-span-2"} title="Füllt das Formular vor">
            <option value="">— aus Vorlage —</option>
            {templates.map((t) => <option key={t.key} value={t.key}>{t.label}: {t.beschreibung}</option>)}
          </select>
        )}
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Name" className={inp} />
        <div className="flex gap-2">
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={inp + " flex-1"}>
            <option value="cron">cron</option><option value="interval">interval</option><option value="once">once</option></select>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className={inp + " flex-1"}>
            <option value="prompt">prompt</option><option value="script">script</option>
            <option value="workflow">workflow</option><option value="film">film</option></select>
        </div>
        <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })} placeholder="Schedule" className={inp} />
        {f.kind === "prompt" && (
          <input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} placeholder="Agent (z.B. news)" className={inp} />
        )}
        {f.kind === "script" && (
          <input value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} placeholder="Script-Datei" className={inp} />
        )}
        {f.kind === "workflow" && (
          <select value={f.workflow_definition_id ?? ""}
            onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value ? Number(e.target.value) : null })}
            className={inp}>
            <option value="">— Prozess wählen —</option>
            {defs?.filter((d) => d.current_version_id).map((d) => (
              <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
            ))}
          </select>
        )}
        {f.kind === "prompt" && (
          <textarea value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} rows={6}
            placeholder="Prompt — {{platzhalter}} kommen aus den Parametern" className={inp + " col-span-2 font-mono text-xs"} />
        )}
        {/* Parameter gibt es für beide: beim Prompt füllen sie die Platzhalter, beim
            Ablauf sind sie sein Startkontext — derselbe Ablauf, andere Messreihe. */}
        {(f.kind === "prompt" || f.kind === "workflow") && (
          <div className="col-span-2">
            <textarea value={paramText} onChange={(e) => setParams(e.target.value)} rows={4}
              placeholder={f.kind === "workflow"
                ? 'Startkontext (JSON), z.B. {"reihe": "akku.shelter", "still_stunden": 26}'
                : 'Parameter (JSON), z.B. {"thema": "IT-Sicherheit"}'}
              className={inp + " w-full font-mono text-xs"} />
            <div className="mt-1 text-xs">
              {paramFehler
                ? <span className="text-red-400">{paramFehler} — Parameter werden nicht übernommen.</span>
                : f.kind === "workflow"
                  ? <span className="text-muted">Steht im Ablauf als Kontext zur Verfügung — abfragbar in Weichen und als {"{{name}}"}.</span>
                  : fehlend.length
                    ? <span className="text-amber-400">Ohne Wert: {fehlend.join(", ")} — bleibt wörtlich im Prompt stehen.</span>
                    : <span className="text-muted">Eingebaut: heute, jetzt, seit, zeitfenster.</span>}
            </div>
          </div>
        )}
        <select value={f.notify_mode} onChange={(e) => setF({ ...f, notify_mode: e.target.value })} className={inp}>
          <option value="on_output">notify bei Output</option><option value="always">immer</option>
          <option value="on_error">nur Fehler</option><option value="never">nie</option></select>
        <label className="flex items-center gap-1"><input type="checkbox" checked={f.result_html} onChange={(e) => setF({ ...f, result_html: e.target.checked })} />HTML-Digest</label>
        <button onClick={() => f.name && save.mutate()} className="col-span-2 rounded bg-brand py-1.5 text-white">
          {editId ? "Speichern" : "+ Job"}
        </button>
      </div>
    </div>
  );
}
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-ink";
const ico = "text-base leading-none text-muted hover:text-ink";
