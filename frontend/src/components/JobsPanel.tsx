import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

const EMPTY = { name: "", type: "cron", schedule: "0 8 * * *", kind: "prompt",
                agent: "", prompt: "", command: "", notify_mode: "on_output", notify_chat: "",
                result_html: false, pause_on_success: false, run_timeout: 600, args: [] as any[], project_id: null as number | null };

export default function JobsPanel() {
  const qc = useQueryClient();
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<any[]>("/jobs") });
  const [f, setF] = useState(EMPTY);
  const [editId, setEditId] = useState<number | null>(null);
  const inv = () => qc.invalidateQueries({ queryKey: ["jobs"] });
  const save = useMutation({
    mutationFn: () => editId ? api.put(`/jobs/${editId}`, f) : api.post("/jobs", f),
    onSuccess: () => { setF(EMPTY); setEditId(null); inv(); },
  });
  const run = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/run`), onSuccess: inv });
  const toggle = useMutation({
    mutationFn: (j: any) => api.post(`/jobs/${j.id}/enabled`, { enabled: !j.enabled }), onSuccess: inv });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/jobs/${id}`), onSuccess: inv });

  const edit = (j: any) => {
    setEditId(j.id);
    setF({ name: j.name, type: j.type, schedule: j.schedule, kind: j.kind, agent: j.agent || "",
           prompt: j.prompt || "", command: j.command || "", notify_mode: j.notify_mode,
           notify_chat: j.notify_chat || "", result_html: !!j.result_html,
           pause_on_success: !!j.pause_on_success, run_timeout: j.run_timeout ?? 600,
           args: j.args || [], project_id: j.project_id ?? null });
  };

  return (
    <div>
      <p className="mb-3 text-sm text-muted">Geplante Jobs: <b>cron</b> (z.B. <span className="font-mono">0 8 * * *</span>),
        <b> interval</b> (Sekunden) oder <b>once</b> (ISO-Zeit). <b>prompt</b> = Agenten-Lauf, <b>script</b> = Programm.</p>
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
          <button onClick={() => { setEditId(null); setF(EMPTY); }} className="ml-1 underline">abbrechen</button></div>}
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Name" className={inp} />
        <div className="flex gap-2">
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={inp + " flex-1"}>
            <option value="cron">cron</option><option value="interval">interval</option><option value="once">once</option></select>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className={inp + " flex-1"}>
            <option value="prompt">prompt</option><option value="script">script</option></select>
        </div>
        <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })} placeholder="Schedule" className={inp} />
        {f.kind === "prompt"
          ? <input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} placeholder="Agent (z.B. news)" className={inp} />
          : <input value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} placeholder="Script-Datei" className={inp} />}
        {f.kind === "prompt" && (
          <textarea value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} rows={2} placeholder="Prompt" className={inp + " col-span-2"} />
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
