import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListenLine, DeleteDialog, BUTTON } from "./ui";

const EMPTY = {
  route: "", mode: "workflow", secret: "", project_id: "",
  event_header: "", event_filter: "", event_key_header: "",
  event_cooldowns: "", alert_events: "", ref_field: "",
  workflow_definition_id: "", context_map: "", context_fixed: "",
  event_name: "", response_timeout: "", response_map: "",
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

/** Eine Zuweisung je Zeile: `ziel = wert`.
 *
 * Kommagetrennt geht hier nicht: In festen Werten steht Text, und Text enthält Kommata
 * („Konto {account}, Nachricht {uid}“). Ein Wert, der als JSON durchgeht, wird auch als
 * JSON übernommen — so kommen `true` und Zahlen in den Kontext und nicht ihre Schreibweise.
 */
function parseAssignments(s: string): Record<string, any> {
  const out: Record<string, any> = {};
  for (const line of s.split("\n")) {
    const i = line.indexOf("=");
    if (i <= 0) continue;
    const k = line.slice(0, i).trim();
    const raw = line.slice(i + 1).trim();
    if (!k) continue;
    try { out[k] = JSON.parse(raw); } catch { out[k] = raw; }
  }
  return out;
}
function fmtAssignments(o: Record<string, any> | undefined): string {
  return Object.entries(o || {})
    .map(([k, v]) => `${k} = ${typeof v === "string" ? v : JSON.stringify(v)}`).join("\n");
}

/**
 * Webhooks: what an outside call sets off in here.
 *
 * A hook carries a lot (mode, secret, filters, alarms, summarising, mapping), and all of it
 * used to stand permanently open under the list, with a collapsible half. In the dialog the
 * rare half stays folded away, and the list shows what one looks for: route, mode and the
 * public URL.
 */
export default function WebhooksPanel() {
  const qc = useQueryClient();
  const { data: hooks } = useQuery({ queryKey: ["webhooks"], queryFn: () => api.get<any[]>("/webhooks") });
  const [dialog, setDialog] = useState<any | null>(null);     // {} = neuer Webhook
  const [deleteHook, setDeleteHook] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["webhooks"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const save = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: any }) =>
      id ? api.put(`/webhooks/${id}`, body) : api.post("/webhooks", body),
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const toggle = useMutation({
    mutationFn: (w: any) => api.post(`/webhooks/${w.id}/enabled`, { enabled: !w.enabled }),
    onSuccess: inv, onError: fail });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/webhooks/${id}`),
    onSuccess: () => { setDeleteHook(null); inv(); }, onError: fail });

  return (
    <Area hint={tr("webhooks_panel.einleitung")}>
      <Errorrow text={err} />
      <Listing className="mb-4">
        {hooks?.map((w) => (
          <ListenLine key={w.id} dimmed={!w.enabled}>
            <div className="flex items-center gap-2">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-medium">{w.route}</span><span className="text-muted">{w.mode}</span>
                {!w.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("webhooks_panel.aus")}</span>}
                {w.secret_set && <span className="text-xs" title={tr("webhooks_panel.secret")}>🔒</span>}
                {w.agent && <span className="rounded bg-surface px-1 text-xs">→ {w.agent}</span>}
                {w.classify_agent && <span className="rounded bg-surface px-1 text-xs">🔒 {w.classify_agent}</span>}
                {w.event_filter && <span className="rounded bg-surface px-1 text-xs">Filter: {w.event_filter}</span>}
                {(w.alert_events || []).length > 0 &&
                  <span className="rounded bg-surface px-1 text-xs">🚨 {w.alert_events.join(", ")}</span>}
                {Object.keys(w.event_cooldowns || {}).length > 0 &&
                  <span className="rounded bg-surface px-1 text-xs">⏳ {fmtCooldowns(w.event_cooldowns)}</span>}
                {w.ref_field && <span className="rounded bg-surface px-1 text-xs">ref: {w.ref_field}</span>}
              </div>
              <Actions>
                <IconButton icon={w.enabled ? "⏸" : "⏵"} onClick={() => toggle.mutate(w)}
                  title={tr(w.enabled ? "jobs_panel.deaktivieren" : "jobs_panel.aktivieren")} />
                <IconButton icon={ICON.edit} title={tr("common.bearbeiten")}
                  onClick={() => { setErr(""); setDialog(w); }} />
                <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger onClick={() => setDeleteHook(w)} />
              </Actions>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-surface px-1.5 py-0.5 text-xs">
                {location.origin}/api/hooks/{w.public_id}
              </code>
              <IconButton icon={ICON.copy} title={tr("webhooks_panel.url_kopieren")}
                onClick={() => navigator.clipboard?.writeText(`${location.origin}/api/hooks/${w.public_id}`)} />
            </div>
          </ListenLine>
        ))}
        {hooks?.length === 0 && <ListingEmpty>{tr("webhooks_panel.keine_webhooks")}</ListingEmpty>}
      </Listing>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("webhooks_panel.webhook_anlegen")}
      </button>

      {dialog && (
        <WebhookDialog hook={dialog.id ? dialog : null} error={err} runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(body, id) => save.mutate({ id, body })} />
      )}
      {deleteHook && (
        <DeleteDialog was={deleteHook.route} hint={tr("webhooks_panel.loeschen_hinweis")} runs={del.isPending}
          onClose={() => setDeleteHook(null)} onDelete={() => del.mutate(deleteHook.id)} />
      )}
    </Area>
  );
}

function WebhookDialog({ hook, error: error, runs: running, onClose, onSave }: {
  hook: any | null; error: string; runs: boolean;
  onClose: () => void; onSave: (body: any, id: number | null) => void;
}) {
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  // For mode=workflow: published process definitions to choose from.
  const { data: defs } = useQuery({
    queryKey: ["workflow-defs"],
    queryFn: () => api.get<{ id: number; name: string; key: string; current_version_id: number | null }[]>("/workflows"),
  });
  const [f, setF] = useState(hook ? {
    route: hook.route, mode: hook.mode, secret: "",
    project_id: hook.project_id ? String(hook.project_id) : "",
    event_header: hook.event_header || "",
    event_filter: hook.event_filter || "", event_key_header: hook.event_key_header || "",
    event_cooldowns: fmtCooldowns(hook.event_cooldowns),
    alert_events: (hook.alert_events || []).join(", "),
    ref_field: hook.ref_field || "",
    workflow_definition_id: hook.workflow_definition_id ? String(hook.workflow_definition_id) : "",
    context_map: fmtContextMap(hook.context_map),
    context_fixed: fmtAssignments(hook.context_fixed),
    event_name: hook.event_name || "",
    response_timeout: hook.response_timeout ? String(hook.response_timeout) : "",
    response_map: fmtContextMap(hook.response_map),
  } : EMPTY);
  const [more, setMore] = useState(false);

  const body = () => ({
    route: f.route, mode: f.mode, secret: f.secret,
    project_id: f.project_id ? +f.project_id : null,
    event_header: f.event_header || null, event_filter: f.event_filter || null,
    event_key_header: f.event_key_header || null,
    event_cooldowns: parseCooldowns(f.event_cooldowns),
    alert_events: f.alert_events.split(",").map((x: string) => x.trim()).filter(Boolean),
    ref_field: f.ref_field || null,
    workflow_definition_id: f.workflow_definition_id ? +f.workflow_definition_id : null,
    context_map: parseContextMap(f.context_map),
    context_fixed: parseAssignments(f.context_fixed),
    event_name: f.event_name || null,
    response_timeout: f.response_timeout ? +f.response_timeout : 0,
    response_map: parseContextMap(f.response_map),
  });

  return (
    <Dialog wide title={hook ? tr("webhooks_panel.webhook_bearbeiten") : tr("webhooks_panel.webhook_anlegen")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!f.route.trim()} runs={running}
        onSave={() => onSave(body(), hook ? hook.id : null)}
        saveText={hook ? undefined : tr("common.anlegen")} />}>
      <Errorrow text={error} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={tr("webhooks_panel.route")}>
          <input value={f.route} autoFocus onChange={(e) => setF({ ...f, route: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("webhooks_panel.modus")}>
          <select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} className={INPUT_VALUE}>
            <option value="workflow">{tr("webhooks_panel.modus_ablauf")}</option>
            <option value="event">{tr("webhooks_panel.modus_ereignis")}</option>
          </select>
        </Field>
        <Field label={tr("webhooks_panel.secret")}
          hint={hook ? tr("webhooks_panel.secret_unveraendert") : undefined}>
          <input value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("webhooks_panel.projekt_optional")}>
          <select value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })} className={INPUT_VALUE}>
            <option value="">—</option>
            {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        {f.mode === "workflow" ? (
          <>
            <Field label={tr("webhooks_panel.prozess_waehlen")}>
              <select value={f.workflow_definition_id}
                onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value })} className={INPUT_VALUE}>
                <option value="">—</option>
                {defs?.filter((d) => d.current_version_id).map((d) => (
                  <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
                ))}
              </select>
            </Field>
            <Field label={tr("webhooks_panel.antwort_abwarten")}>
              <input type="number" min={0} max={120} value={f.response_timeout}
                onChange={(e) => setF({ ...f, response_timeout: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <div className="sm:col-span-2">
              <Field label={tr("webhooks_panel.antwort_felder")} hint={tr("webhooks_panel.antwort_hinweis")}>
                <input value={f.response_map} onChange={(e) => setF({ ...f, response_map: e.target.value })}
                  placeholder="status: antwort.status, text: assistent.output" className={INPUT_VALUE} />
              </Field>
            </div>
          </>
        ) : (
          <Field label={tr("webhooks_panel.ereignisname")} hint={tr("webhooks_panel.ereignisname_hinweis")}>
            <input value={f.event_name} onChange={(e) => setF({ ...f, event_name: e.target.value })}
              placeholder="mail.received" className={INPUT_VALUE} />
          </Field>
        )}
        {/* Der Kontext gilt für beide Wege: Was der Auslöser weitergibt, hängt nicht daran,
            ob ein Ablauf startet oder ein Ereignis gemeldet wird. */}
        <div className="sm:col-span-2">
          <Field label={tr("webhooks_panel.kontext_aus_nutzlast")} hint={tr("webhooks_panel.ohne_mapping")}>
            <input value={f.context_map} onChange={(e) => setF({ ...f, context_map: e.target.value })}
              placeholder={tr("webhooks_panel.kontext_mapping_asset_id_data_id_ort_dat")} className={INPUT_VALUE} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label={tr("webhooks_panel.kontext_fest")} hint={tr("webhooks_panel.kontext_fest_hinweis")}>
            <textarea value={f.context_fixed} onChange={(e) => setF({ ...f, context_fixed: e.target.value })}
              rows={3} placeholder={"quelle = Tracker {device.id}\nstumm = true"}
              className={`${INPUT_VALUE} font-mono text-xs`} />
          </Field>
        </div>

        <button type="button" onClick={() => setMore(!more)}
          className="text-left text-xs text-muted hover:text-ink sm:col-span-2">
          {more ? "▾" : "▸"} {tr("webhooks_panel.filter_alarme")}
        </button>
        {more && (
          <>
            <Field label={tr("webhooks_panel.event_header_z_b_x_github_event")}>
              <input value={f.event_header} onChange={(e) => setF({ ...f, event_header: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("webhooks_panel.nur_diese_events_push_issues")}>
              <input value={f.event_filter} onChange={(e) => setF({ ...f, event_filter: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("webhooks_panel.sofort_alarm_bei_outage_alert")}>
              <input value={f.alert_events} onChange={(e) => setF({ ...f, alert_events: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("webhooks_panel.zusammenfassen_push_300_issues_60")}
              hint={tr("webhooks_panel.cooldown_erste_zustellung_laeuft_durch_f")}>
              <input value={f.event_cooldowns} onChange={(e) => setF({ ...f, event_cooldowns: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("webhooks_panel.gruppier_header_optional")}>
              <input value={f.event_key_header} onChange={(e) => setF({ ...f, event_key_header: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("webhooks_panel.idempotenz_feld")} hint={tr("webhooks_panel.idempotenz_hinweis")}>
              <input value={f.ref_field} onChange={(e) => setF({ ...f, ref_field: e.target.value })} className={INPUT_VALUE} />
            </Field>
          </>
        )}
      </div>
    </Dialog>
  );
}
