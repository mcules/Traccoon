import { useState } from "react";
import { tr } from "../i18n";
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
      return v && typeof v === "object" && !Array.isArray(v) ? "" : tr("jobs_panel.objekt_erwartet");
    } catch { return tr("jobs_panel.kein_gueltiges_json"); }
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
      <p className="mb-3 text-sm text-muted">{tr("jobs_panel.einleitung")}</p>
      <div className="mb-4 space-y-2">
        {jobs?.map((j) => (
          <div key={j.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-line bg-card p-2 text-sm ${j.enabled ? "" : "opacity-50"}`}>
            <span>{j.name}</span>
            {!j.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("jobs_panel.aus")}</span>}
            {j.enabled && j.paused && <span className="rounded bg-surface px-1 text-xs text-amber-400">{tr("jobs_panel.pausiert")}</span>}
            <span className="text-xs text-muted font-mono">{j.type}:{j.schedule} · {j.kind}</span>
            {j.last_run_at && <span className="text-xs text-muted">{tr("jobs_panel.zuletzt")} {new Date(j.last_run_at).toLocaleString()}</span>}
            <div className="hidden flex-1 sm:block" />
            <button title={tr(j.enabled ? "jobs_panel.deaktivieren" : "jobs_panel.aktivieren")} onClick={() => toggle.mutate(j)}
              className={ico}>{j.enabled ? "⏸" : "⏵"}</button>
            <button title={tr("jobs_panel.jetzt_ausfuehren")} onClick={() => run.mutate(j.id)} className={ico + " hover:text-brand"}>▶</button>
            <button title={tr("jobs_panel.bearbeiten")} onClick={() => edit(j)} className={ico}>✎</button>
            <button title={tr("jobs_panel.loeschen")} onClick={() => del.mutate(j.id)} className={ico + " hover:text-red-400"}>🗑</button>
          </div>
        ))}
        {jobs?.length === 0 && <div className="text-xs text-muted">{tr("jobs_panel.keine_jobs")}</div>}
      </div>
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-card p-3 text-sm">
        {editId && <div className="col-span-2 text-xs text-brand">Bearbeite Job #{editId} —
          <button onClick={() => { setEditId(null); setF(EMPTY); setParamText(""); }} className="ml-1 underline">{tr("common.abbrechen")}</button></div>}
        {!editId && !!templates?.length && (
          <select value="" onChange={(e) => e.target.value && useTemplate(e.target.value)}
            className={inp + " col-span-2"} title={tr("jobs_panel.fuellt_das_formular_vor")}>
            <option value="">{tr("jobs_panel.aus_vorlage")}</option>
            {templates.map((t) => <option key={t.key} value={t.key}>{t.label}: {t.beschreibung}</option>)}
          </select>
        )}
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={tr("jobs_panel.name")} className={inp} />
        <div className="flex flex-wrap gap-2">
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={inp + " flex-1"}>
            <option value="cron">cron</option><option value="interval">interval</option><option value="once">once</option></select>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className={inp + " flex-1"}>
            <option value="prompt">prompt</option><option value="script">script</option>
            <option value="workflow">workflow</option><option value="film">film</option></select>
        </div>
        <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })} placeholder={tr("jobs_panel.schedule")} className={inp} />
        {f.kind === "prompt" && (
          <input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} placeholder={tr("jobs_panel.agent_z_b_news")} className={inp} />
        )}
        {f.kind === "script" && (
          <input value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} placeholder={tr("jobs_panel.script_datei")} className={inp} />
        )}
        {f.kind === "workflow" && (
          <select value={f.workflow_definition_id ?? ""}
            onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value ? Number(e.target.value) : null })}
            className={inp}>
            <option value="">{tr("jobs_panel.prozess_waehlen")}</option>
            {defs?.filter((d) => d.current_version_id).map((d) => (
              <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
            ))}
          </select>
        )}
        {f.kind === "prompt" && (
          <textarea value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} rows={6}
            placeholder={tr("jobs_panel.prompt_platzhalter")} className={inp + " col-span-2 font-mono text-xs"} />
        )}
        {/* Parameter gibt es für beide: beim Prompt füllen sie die Platzhalter, beim
            Ablauf sind sie sein Startkontext — derselbe Ablauf, andere Messreihe. */}
        {(f.kind === "prompt" || f.kind === "workflow") && (
          <div className="col-span-2">
            <textarea value={paramText} onChange={(e) => setParams(e.target.value)} rows={4}
              placeholder={tr(f.kind === "workflow"
                ? "jobs_panel.startkontext_platzhalter" : "jobs_panel.parameter_platzhalter")}
              className={inp + " w-full font-mono text-xs"} />
            <div className="mt-1 text-xs">
              {paramFehler
                ? <span className="text-red-400">{paramFehler} — Parameter werden nicht übernommen.</span>
                : f.kind === "workflow"
                  ? <span className="text-muted">Steht im Ablauf als Kontext zur Verfügung — abfragbar in Weichen und als {"{{name}}"}.</span>
                  : fehlend.length
                    ? <span className="text-amber-400">Ohne Wert: {fehlend.join(", ")} — bleibt wörtlich im Prompt stehen.</span>
                    : <span className="text-muted">{tr("jobs_panel.eingebaut_heute_jetzt_seit_zeitfenster")}</span>}
            </div>
          </div>
        )}
        <select value={f.notify_mode} onChange={(e) => setF({ ...f, notify_mode: e.target.value })} className={inp}>
          <option value="on_output">{tr("jobs_panel.notify_bei_output")}</option><option value="always">{tr("jobs_panel.notify_immer")}</option>
          <option value="on_error">{tr("jobs_panel.notify_fehler")}</option><option value="never">{tr("jobs_panel.notify_nie")}</option></select>
        <label className="flex items-center gap-1"><input type="checkbox" checked={f.result_html} onChange={(e) => setF({ ...f, result_html: e.target.checked })} />{tr("jobs_panel.html_digest")}</label>
        <button onClick={() => f.name && save.mutate()} className="col-span-2 rounded bg-brand py-1.5 text-white">
          {editId ? tr("common.speichern") : "+ Job"}
        </button>
      </div>
    </div>
  );
}
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-ink";
const ico = "text-base leading-none text-muted hover:text-ink";
