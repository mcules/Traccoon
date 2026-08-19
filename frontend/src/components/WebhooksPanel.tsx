import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, Liste,
  ListeLeer, ListenZeile, LoeschDialog,
} from "./ui";

const EMPTY = {
  route: "", mode: "task", secret: "", project_id: "", agent: "", classify_agent: "", prompt_tmpl: "",
  auto_run: false, title_template: "{title}", body_template: "{body}",
  event_header: "", event_filter: "", event_key_header: "",
  event_cooldowns: "", alert_events: "", ref_field: "", notify_chat: "",
  workflow_definition_id: "", context_map: "",
  response_timeout: "", response_map: "",
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
  const [loeschHook, setLoeschHook] = useState<any | null>(null);
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
    onSuccess: () => { setLoeschHook(null); inv(); }, onError: fail });

  return (
    <Bereich hinweis={tr("webhooks_panel.einleitung")}>
      <Fehlerzeile text={err} />
      <Liste className="mb-4">
        {hooks?.map((w) => (
          <ListenZeile key={w.id} gedimmt={!w.enabled}>
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
              <Aktionen>
                <IconKnopf icon={w.enabled ? "⏸" : "⏵"} onClick={() => toggle.mutate(w)}
                  titel={tr(w.enabled ? "jobs_panel.deaktivieren" : "jobs_panel.aktivieren")} />
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => { setErr(""); setDialog(w); }} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschHook(w)} />
              </Aktionen>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-surface px-1.5 py-0.5 text-xs">
                {location.origin}/api/hooks/{w.public_id}
              </code>
              <IconKnopf icon={ICON.kopieren} titel={tr("webhooks_panel.url_kopieren")}
                onClick={() => navigator.clipboard?.writeText(`${location.origin}/api/hooks/${w.public_id}`)} />
            </div>
          </ListenZeile>
        ))}
        {hooks?.length === 0 && <ListeLeer>{tr("webhooks_panel.keine_webhooks")}</ListeLeer>}
      </Liste>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className="rounded bg-brand px-3 py-1.5 text-sm text-white">
        {ICON.neu} {tr("webhooks_panel.webhook_anlegen")}
      </button>

      {dialog && (
        <WebhookDialog hook={dialog.id ? dialog : null} fehler={err} laeuft={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(body, id) => save.mutate({ id, body })} />
      )}
      {loeschHook && (
        <LoeschDialog was={loeschHook.route} hinweis={tr("webhooks_panel.loeschen_hinweis")} laeuft={del.isPending}
          onClose={() => setLoeschHook(null)} onLoeschen={() => del.mutate(loeschHook.id)} />
      )}
    </Bereich>
  );
}

function WebhookDialog({ hook, fehler, laeuft, onClose, onSpeichern }: {
  hook: any | null; fehler: string; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: any, id: number | null) => void;
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
    agent: hook.agent || "", classify_agent: hook.classify_agent || "", prompt_tmpl: hook.prompt_tmpl || "",
    auto_run: !!hook.auto_run, title_template: hook.title_template || "{title}",
    body_template: hook.body_template || "{body}", event_header: hook.event_header || "",
    event_filter: hook.event_filter || "", event_key_header: hook.event_key_header || "",
    event_cooldowns: fmtCooldowns(hook.event_cooldowns),
    alert_events: (hook.alert_events || []).join(", "),
    ref_field: hook.ref_field || "", notify_chat: hook.notify_chat || "",
    workflow_definition_id: hook.workflow_definition_id ? String(hook.workflow_definition_id) : "",
    context_map: fmtContextMap(hook.context_map),
    response_timeout: hook.response_timeout ? String(hook.response_timeout) : "",
    response_map: fmtContextMap(hook.response_map),
  } : EMPTY);
  const [mehr, setMehr] = useState(false);

  const body = () => ({
    route: f.route, mode: f.mode, secret: f.secret,
    project_id: f.project_id ? +f.project_id : null, agent: f.agent || null,
    classify_agent: f.classify_agent || null, prompt_tmpl: f.prompt_tmpl || null, auto_run: f.auto_run,
    title_template: f.title_template, body_template: f.body_template,
    event_header: f.event_header || null, event_filter: f.event_filter || null,
    event_key_header: f.event_key_header || null,
    event_cooldowns: parseCooldowns(f.event_cooldowns),
    alert_events: f.alert_events.split(",").map((x: string) => x.trim()).filter(Boolean),
    ref_field: f.ref_field || null, notify_chat: f.notify_chat || null,
    workflow_definition_id: f.workflow_definition_id ? +f.workflow_definition_id : null,
    context_map: parseContextMap(f.context_map),
    response_timeout: f.response_timeout ? +f.response_timeout : 0,
    response_map: parseContextMap(f.response_map),
  });

  return (
    <Dialog breit titel={hook ? tr("webhooks_panel.webhook_bearbeiten") : tr("webhooks_panel.webhook_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!f.route.trim()} laeuft={laeuft}
        onSpeichern={() => onSpeichern(body(), hook ? hook.id : null)}
        speichernText={hook ? undefined : tr("common.anlegen")} />}>
      <Fehlerzeile text={fehler} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Feld label={tr("webhooks_panel.route")}>
          <input value={f.route} autoFocus onChange={(e) => setF({ ...f, route: e.target.value })} className={EINGABE} />
        </Feld>
        <Feld label={tr("webhooks_panel.modus")}>
          <select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} className={EINGABE}>
            <option value="task">task (Ticket)</option><option value="notify">notify</option>
            <option value="assistant">assistant (Mail)</option>
            <option value="workflow">workflow (Prozess starten)</option>
          </select>
        </Feld>
        <Feld label={tr("webhooks_panel.secret")}
          hinweis={hook ? tr("webhooks_panel.secret_unveraendert") : undefined}>
          <input value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} className={EINGABE} />
        </Feld>
        {f.mode === "assistant" ? (
          <Feld label={tr("webhooks_panel.klassifizier_agent_z_b_mail_classifier")}>
            <input value={f.classify_agent} onChange={(e) => setF({ ...f, classify_agent: e.target.value })} className={EINGABE} />
          </Feld>
        ) : (
          <Feld label={tr("webhooks_panel.projekt_fuer_task")}>
            <select value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })} className={EINGABE}>
              <option value="">—</option>
              {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Feld>
        )}
        <Feld label={tr("webhooks_panel.agent_optional")}>
          <input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} className={EINGABE} />
        </Feld>
        {f.mode === "assistant" ? (
          <div className="sm:col-span-2">
            <Feld label={tr("webhooks_panel.task_prompt")}>
              <textarea value={f.prompt_tmpl} onChange={(e) => setF({ ...f, prompt_tmpl: e.target.value })}
                placeholder={tr("webhooks_panel.task_prompt_platzhalter")} rows={8}
                className={`${EINGABE} font-mono text-xs`} />
            </Feld>
          </div>
        ) : (
          <Feld label={tr("webhooks_panel.titel_vorlage")}>
            <input value={f.title_template} onChange={(e) => setF({ ...f, title_template: e.target.value })} className={EINGABE} />
          </Feld>
        )}
        {f.mode === "workflow" && (
          <>
            <Feld label={tr("webhooks_panel.prozess_waehlen")}>
              <select value={f.workflow_definition_id}
                onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value })} className={EINGABE}>
                <option value="">—</option>
                {defs?.filter((d) => d.current_version_id).map((d) => (
                  <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
                ))}
              </select>
            </Feld>
            <Feld label={tr("webhooks_panel.kontext_mapping")} hinweis={tr("webhooks_panel.ohne_mapping")}>
              <input value={f.context_map} onChange={(e) => setF({ ...f, context_map: e.target.value })}
                placeholder={tr("webhooks_panel.kontext_mapping_asset_id_data_id_ort_dat")} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.antwort_abwarten")}>
              <input type="number" min={0} max={120} value={f.response_timeout}
                onChange={(e) => setF({ ...f, response_timeout: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.antwort_felder")} hinweis={tr("webhooks_panel.antwort_hinweis")}>
              <input value={f.response_map} onChange={(e) => setF({ ...f, response_map: e.target.value })}
                placeholder="status: antwort.status, text: assistent.output" className={EINGABE} />
            </Feld>
          </>
        )}
        {f.mode === "assistant" && (
          <label className="flex items-center gap-2 text-sm text-ink sm:col-span-2">
            <input type="checkbox" checked={f.auto_run} onChange={(e) => setF({ ...f, auto_run: e.target.checked })} />
            {tr("webhooks_panel.sofort_ausfuehren")}
          </label>
        )}

        <button type="button" onClick={() => setMehr(!mehr)}
          className="text-left text-xs text-muted hover:text-ink sm:col-span-2">
          {mehr ? "▾" : "▸"} {tr("webhooks_panel.filter_alarme")}
        </button>
        {mehr && (
          <>
            <Feld label={tr("webhooks_panel.event_header_z_b_x_github_event")}>
              <input value={f.event_header} onChange={(e) => setF({ ...f, event_header: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.nur_diese_events_push_issues")}>
              <input value={f.event_filter} onChange={(e) => setF({ ...f, event_filter: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.sofort_alarm_bei_outage_alert")}>
              <input value={f.alert_events} onChange={(e) => setF({ ...f, alert_events: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.zusammenfassen_push_300_issues_60")}
              hinweis={tr("webhooks_panel.cooldown_erste_zustellung_laeuft_durch_f")}>
              <input value={f.event_cooldowns} onChange={(e) => setF({ ...f, event_cooldowns: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.gruppier_header_optional")}>
              <input value={f.event_key_header} onChange={(e) => setF({ ...f, event_key_header: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.idempotenz_feld_im_payload_z_b_id")}>
              <input value={f.ref_field} onChange={(e) => setF({ ...f, ref_field: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("webhooks_panel.telegram_chat_id_optional")}>
              <input value={f.notify_chat} onChange={(e) => setF({ ...f, notify_chat: e.target.value })} className={EINGABE} />
            </Feld>
          </>
        )}
      </div>
    </Dialog>
  );
}
