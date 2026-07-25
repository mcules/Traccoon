import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Project } from "../api";

const EMPTY = {
  route: "", mode: "task", secret: "", project_id: "", agent: "", classify_agent: "", prompt_tmpl: "",
  auto_run: false, title_template: "{title}", body_template: "{body}",
  event_header: "", event_filter: "", event_key_header: "",
  event_cooldowns: "", alert_events: "", ref_field: "", notify_chat: "",
  workflow_definition_id: "", context_map: "",
};

/** "push:300, issue:60" → {push: 300, issue: 60} */
function parseCooldowns(s: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of s.split(",")) {
    const [k, v] = part.split(":").map((x) => x.trim());
    if (k && v && !isNaN(+v)) out[k] = +v;
  }
  return out;
}
function fmtCooldowns(o: Record<string, number> | undefined): string {
  return Object.entries(o || {}).map(([k, v]) => `${k}:${v}`).join(", ");
}

/** "asset_id: data.id, ort: data.location.name" → {asset_id: "data.id", ort: "data.location.name"} */
function parseContextMap(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of s.split(",")) {
    const i = part.indexOf(":");
    if (i <= 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k && v) out[k] = v;
  }
  return out;
}
function fmtContextMap(o: Record<string, string> | undefined): string {
  return Object.entries(o || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
}

export default function WebhooksPanel() {
  const qc = useQueryClient();
  const { data: hooks } = useQuery({ queryKey: ["webhooks"], queryFn: () => api.get<any[]>("/webhooks") });
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  // Für mode=workflow: veröffentlichte Prozess-Definitionen zur Auswahl.
  const { data: defs } = useQuery({
    queryKey: ["workflow-defs"],
    queryFn: () => api.get<{ id: number; name: string; key: string; current_version_id: number | null }[]>("/workflows"),
  });
  const [f, setF] = useState(EMPTY);
  const [editId, setEditId] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const inv = () => qc.invalidateQueries({ queryKey: ["webhooks"] });

  const body = () => ({
    route: f.route, mode: f.mode, secret: f.secret,
    project_id: f.project_id ? +f.project_id : null, agent: f.agent || null,
    classify_agent: f.classify_agent || null, prompt_tmpl: f.prompt_tmpl || null, auto_run: f.auto_run,
    title_template: f.title_template, body_template: f.body_template,
    event_header: f.event_header || null, event_filter: f.event_filter || null,
    event_key_header: f.event_key_header || null,
    event_cooldowns: parseCooldowns(f.event_cooldowns),
    alert_events: f.alert_events.split(",").map((x) => x.trim()).filter(Boolean),
    ref_field: f.ref_field || null, notify_chat: f.notify_chat || null,
    workflow_definition_id: f.workflow_definition_id ? +f.workflow_definition_id : null,
    context_map: parseContextMap(f.context_map),
  });
  const save = useMutation({
    mutationFn: () => editId ? api.put(`/webhooks/${editId}`, body()) : api.post("/webhooks", body()),
    onSuccess: () => { setF(EMPTY); setEditId(null); inv(); },
  });
  const toggle = useMutation({
    mutationFn: (w: any) => api.post(`/webhooks/${w.id}/enabled`, { enabled: !w.enabled }), onSuccess: inv });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/webhooks/${id}`), onSuccess: inv });

  const edit = (w: any) => {
    setEditId(w.id);
    setOpen(true);
    setF({
      route: w.route, mode: w.mode, secret: "", project_id: w.project_id ? String(w.project_id) : "",
      agent: w.agent || "", classify_agent: w.classify_agent || "", prompt_tmpl: w.prompt_tmpl || "",
      auto_run: !!w.auto_run, title_template: w.title_template || "{title}",
      body_template: w.body_template || "{body}", event_header: w.event_header || "",
      event_filter: w.event_filter || "", event_key_header: w.event_key_header || "",
      event_cooldowns: fmtCooldowns(w.event_cooldowns),
      alert_events: (w.alert_events || []).join(", "),
      ref_field: w.ref_field || "", notify_chat: w.notify_chat || "",
      workflow_definition_id: w.workflow_definition_id ? String(w.workflow_definition_id) : "",
      context_map: fmtContextMap(w.context_map),
    });
  };

  return (
    <div>
      <p className="mb-3 text-sm text-muted">Deine Webhooks. Aufruf-URL trägt eine <b>GUID</b> (kein Nutzername):
        {" "}<span className="font-mono">POST /api/hooks/&lt;guid&gt;</span> mit HMAC-SHA256 im Header
        {" "}<span className="font-mono">X-Webhook-Signature</span>. Modus <b>task</b> legt ein Ticket an,
        {" "}<b>notify</b> benachrichtigt dich, <b>assistant</b> nimmt E-Mails an (lokale Vorklassifizierung
        durch den gewählten Klassifizier-Agenten → Assistent-Inbox), <b>workflow</b> startet eine
        Prozess-Instanz aus dem Prozess-Editor.</p>
      <div className="mb-4 space-y-2">
        {hooks?.map((w) => (
          <div key={w.id} className={`rounded border border-line bg-card p-2 text-sm ${w.enabled ? "" : "opacity-50"}`}>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-medium">{w.route}</span><span className="text-muted">{w.mode}</span>
            {!w.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">aus</span>}
            {w.secret_set && <span className="text-xs">🔒</span>}
            {w.agent && <span className="rounded bg-surface px-1 text-xs">→ {w.agent}</span>}
            {w.classify_agent && <span className="rounded bg-surface px-1 text-xs">🔒 {w.classify_agent}</span>}
            {w.event_filter && <span className="rounded bg-surface px-1 text-xs">Filter: {w.event_filter}</span>}
            {(w.alert_events || []).length > 0 &&
              <span className="rounded bg-surface px-1 text-xs">🚨 {w.alert_events.join(", ")}</span>}
            {Object.keys(w.event_cooldowns || {}).length > 0 &&
              <span className="rounded bg-surface px-1 text-xs">⏳ {fmtCooldowns(w.event_cooldowns)}</span>}
            {w.ref_field && <span className="rounded bg-surface px-1 text-xs">ref: {w.ref_field}</span>}
            <div className="flex-1" />
            <button title={w.enabled ? "Deaktivieren" : "Aktivieren"} onClick={() => toggle.mutate(w)} className={ico}>{w.enabled ? "⏸" : "⏵"}</button>
            <button title="Bearbeiten" onClick={() => edit(w)} className={ico}>✎</button>
            <button title="Löschen" onClick={() => del.mutate(w.id)} className={ico + " hover:text-red-400"}>🗑</button>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 truncate rounded bg-surface px-1.5 py-0.5 text-xs">
              {location.origin}/api/hooks/{w.public_id}
            </code>
            <button title="URL kopieren" onClick={() => navigator.clipboard?.writeText(`${location.origin}/api/hooks/${w.public_id}`)}
              className={ico + " hover:text-brand"}>⧉</button>
          </div>
          </div>
        ))}
        {hooks?.length === 0 && <div className="text-xs text-muted">Keine Webhooks.</div>}
      </div>
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-card p-3 text-sm">
        {editId && <div className="col-span-2 text-xs text-brand">Bearbeite Webhook #{editId} —
          <button onClick={() => { setEditId(null); setF(EMPTY); }} className="ml-1 underline">abbrechen</button></div>}
        <input value={f.route} onChange={(e) => setF({ ...f, route: e.target.value })} placeholder="route" className={inp} />
        <select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} className={inp}>
          <option value="task">task (Ticket)</option><option value="notify">notify</option>
          <option value="assistant">assistant (Mail)</option>
          <option value="workflow">workflow (Prozess starten)</option></select>
        <input value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })}
          placeholder={editId ? "HMAC-Secret (leer = unverändert)" : "HMAC-Secret"} className={inp} />
        {f.mode === "assistant" ? (
          <input value={f.classify_agent} onChange={(e) => setF({ ...f, classify_agent: e.target.value })}
            placeholder="Klassifizier-Agent (z. B. mail_classifier)" className={inp} />
        ) : (
          <select value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })} className={inp}>
            <option value="">— Projekt (für task) —</option>
            {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        )}
        <input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} placeholder="Agent (optional)" className={inp} />
        {f.mode === "assistant" ? (
          <textarea value={f.prompt_tmpl} onChange={(e) => setF({ ...f, prompt_tmpl: e.target.value })}
            placeholder="Task-Prompt (Mail-Verarbeitungs-Wissen) — leer = Standard. Platzhalter {account} {uid} {from} {subject} {body_text} …"
            rows={8} className={`col-span-2 ${inp} font-mono text-xs`} />
        ) : (
          <input value={f.title_template} onChange={(e) => setF({ ...f, title_template: e.target.value })} placeholder="Titel-Template {feld}" className={inp} />
        )}
        {f.mode === "workflow" && (
          <>
            <select value={f.workflow_definition_id}
              onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value })} className={inp}>
              <option value="">— Prozess wählen —</option>
              {defs?.filter((d) => d.current_version_id).map((d) => (
                <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
              ))}
            </select>
            <input value={f.context_map} onChange={(e) => setF({ ...f, context_map: e.target.value })}
              placeholder="Kontext-Mapping: asset_id: data.id, ort: data.location.name" className={inp} />
            <div className="col-span-2 text-xs text-muted">
              Ohne Mapping wandert der komplette Payload als Kontext in die Instanz.
            </div>
          </>
        )}
        {f.mode === "assistant" && (
          <label className="col-span-2 flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={f.auto_run} onChange={(e) => setF({ ...f, auto_run: e.target.checked })} />
            Chatlos sofort ausführen (ohne Freigabe) — z. B. paperless-linked Link-back
          </label>
        )}

        <button onClick={() => setOpen(!open)} className="col-span-2 text-left text-xs text-muted hover:text-ink">
          {open ? "▾" : "▸"} Filter, Alarme &amp; Zusammenfassung
        </button>
        {open && (
          <>
            <input value={f.event_header} onChange={(e) => setF({ ...f, event_header: e.target.value })}
              placeholder="Event-Header (z. B. X-GitHub-Event)" className={inp} />
            <input value={f.event_filter} onChange={(e) => setF({ ...f, event_filter: e.target.value })}
              placeholder="nur diese Events: push, issues" className={inp} />
            <input value={f.alert_events} onChange={(e) => setF({ ...f, alert_events: e.target.value })}
              placeholder="Sofort-Alarm bei: outage, alert" className={inp} />
            <input value={f.event_cooldowns} onChange={(e) => setF({ ...f, event_cooldowns: e.target.value })}
              placeholder="Zusammenfassen: push:300, issues:60" className={inp} />
            <input value={f.event_key_header} onChange={(e) => setF({ ...f, event_key_header: e.target.value })}
              placeholder="Gruppier-Header (optional)" className={inp} />
            <input value={f.ref_field} onChange={(e) => setF({ ...f, ref_field: e.target.value })}
              placeholder="Idempotenz-Feld im Payload (z. B. id)" className={inp} />
            <input value={f.notify_chat} onChange={(e) => setF({ ...f, notify_chat: e.target.value })}
              placeholder="Telegram-Chat-ID (optional)" className={inp} />
            <div className="text-xs text-muted">Cooldown: erste Zustellung läuft durch, Folge-Events im Fenster
              kommen als eine Sammel-Meldung.</div>
          </>
        )}
        <button onClick={() => f.route && save.mutate()} className="col-span-2 rounded bg-brand py-1.5 text-white">
          {editId ? "Speichern" : "+ Webhook"}
        </button>
      </div>
    </div>
  );
}
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-ink";
const ico = "text-base leading-none text-muted hover:text-ink";
