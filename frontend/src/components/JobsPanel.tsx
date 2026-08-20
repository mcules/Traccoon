import { useState } from "react";
import { formatDateTime } from "../lib/formatTime";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, Liste,
  ListeLeer, ListenZeile, LoeschDialog,
} from "./ui";

const EMPTY = { name: "", type: "cron", schedule: "0 8 * * *", kind: "workflow",
                agent: "", prompt: "", command: "", notify_mode: "on_output", notify_chat: "",
                result_html: false, pause_on_success: false, run_timeout: 600,
                // Liste = Script-Argumente, Objekt = Parameter eines prompt-Jobs.
                args: [] as any[] | Record<string, any>,
                project_id: null as number | null,
                workflow_definition_id: null as number | null };

/**
 * Scheduled jobs: what runs on its own, and when.
 *
 * The form used to stand under the list, permanently open, with a line "editing job #12"
 * above it. A dialog says that by itself, and the list keeps the height of its entries.
 */
export default function JobsPanel() {
  const qc = useQueryClient();
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<any[]>("/jobs") });
  const [dialog, setDialog] = useState<any | null>(null);      // {} = neuer Job
  const [loeschJob, setLoeschJob] = useState<any | null>(null);
  const [err, setErr] = useState("");

  const inv = () => qc.invalidateQueries({ queryKey: ["jobs"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
  const speichern = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: any }) =>
      id ? api.put(`/jobs/${id}`, body) : api.post("/jobs", body),
    onSuccess: () => { setDialog(null); setErr(""); inv(); }, onError: fail,
  });
  const run = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/run`), onSuccess: inv, onError: fail });
  const toggle = useMutation({
    mutationFn: (j: any) => api.post(`/jobs/${j.id}/enabled`, { enabled: !j.enabled }),
    onSuccess: inv, onError: fail });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/jobs/${id}`),
    onSuccess: () => { setLoeschJob(null); inv(); }, onError: fail });

  return (
    <Bereich hinweis={tr("jobs_panel.einleitung")}>
      <Fehlerzeile text={err} />
      <Liste className="mb-4">
        {jobs?.map((j) => (
          <ListenZeile key={j.id} gedimmt={!j.enabled}>
            <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-medium">{j.name}</span>
                {!j.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("jobs_panel.aus")}</span>}
                {j.enabled && j.paused && <span className="rounded bg-surface px-1 text-xs text-amber-400">{tr("jobs_panel.pausiert")}</span>}
                <span className="font-mono text-xs text-muted">{j.type}:{j.schedule} · {j.kind}</span>
              </div>
              {j.last_run_at && (
                <div className="text-xs text-muted">{tr("jobs_panel.zuletzt")} {formatDateTime(j.last_run_at)}</div>
              )}
            </div>
            <Aktionen>
              <IconKnopf icon={j.enabled ? "⏸" : "⏵"} onClick={() => toggle.mutate(j)}
                titel={tr(j.enabled ? "jobs_panel.deaktivieren" : "jobs_panel.aktivieren")} />
              <IconKnopf icon={ICON.starten} titel={tr("jobs_panel.jetzt_ausfuehren")}
                onClick={() => run.mutate(j.id)} disabled={run.isPending} />
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => { setErr(""); setDialog(j); }} />
              <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschJob(j)} />
            </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {jobs?.length === 0 && <ListeLeer>{tr("jobs_panel.keine_jobs")}</ListeLeer>}
      </Liste>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className="rounded bg-brand px-3 py-1.5 text-sm text-white">
        {ICON.neu} {tr("jobs_panel.job_anlegen")}
      </button>

      {dialog && (
        <JobDialog job={dialog.id ? dialog : null} fehler={err} laeuft={speichern.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(body, id) => speichern.mutate({ id, body })} />
      )}
      {loeschJob && (
        <LoeschDialog was={loeschJob.name} laeuft={del.isPending}
          onClose={() => setLoeschJob(null)} onLoeschen={() => del.mutate(loeschJob.id)} />
      )}
    </Bereich>
  );
}

function JobDialog({ job, fehler, laeuft, onClose, onSpeichern }: {
  job: any | null; fehler: string; laeuft: boolean;
  onClose: () => void; onSpeichern: (body: any, id: number | null) => void;
}) {
  // For kind=workflow: published definitions to choose from.
  const { data: defs } = useQuery({
    queryKey: ["workflow-defs"],
    queryFn: () => api.get<{ id: number; name: string; key: string; current_version_id: number | null }[]>("/workflows"),
  });
  // Templates only prefill the form; the job carries its own fields afterwards.
  const { data: templates } = useQuery({
    queryKey: ["job-templates"],
    queryFn: () => api.get<{ key: string; label: string; beschreibung: string;
                             params: Record<string, any>; felder: Record<string, any> }[]>("/jobs/templates"),
  });

  const [f, setF] = useState<any>(job ? {
    name: job.name, type: job.type, schedule: job.schedule, kind: job.kind, agent: job.agent || "",
    prompt: job.prompt || "", command: job.command || "", notify_mode: job.notify_mode,
    notify_chat: job.notify_chat || "", result_html: !!job.result_html,
    pause_on_success: !!job.pause_on_success, run_timeout: job.run_timeout ?? 600,
    args: job.args || [], project_id: job.project_id ?? null,
    workflow_definition_id: job.workflow_definition_id ?? null,
  } : EMPTY);
  // Parameters as JSON text, so that a typo while editing does not eat the value at once.
  const [paramText, setParamText] = useState(
    job && job.args && !Array.isArray(job.args) ? JSON.stringify(job.args, null, 2) : "");

  const paramFehler = (() => {
    if (!paramText.trim()) return "";
    try {
      const v = JSON.parse(paramText);
      return v && typeof v === "object" && !Array.isArray(v) ? "" : tr("jobs_panel.objekt_erwartet");
    } catch { return tr("jobs_panel.kein_gueltiges_json"); }
  })();
  const setParams = (text: string) => {
    setParamText(text);
    if (!text.trim()) { setF((p: any) => ({ ...p, args: [] })); return; }
    try {
      const v = JSON.parse(text);
      if (v && typeof v === "object" && !Array.isArray(v)) setF((p: any) => ({ ...p, args: v }));
    } catch { /* ungültig: Text stehen lassen, Job-Feld unverändert */ }
  };
  const useTemplate = (key: string) => {
    const t = templates?.find((x) => x.key === key);
    if (!t) return;
    setF((p: any) => ({ ...p, ...t.felder, args: t.params }));
    setParamText(JSON.stringify(t.params, null, 2));
  };

  return (
    <Dialog breit titel={job ? tr("jobs_panel.job_bearbeiten") : tr("jobs_panel.job_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!f.name.trim()} laeuft={laeuft}
        onSpeichern={() => onSpeichern(f, job ? job.id : null)}
        speichernText={job ? undefined : tr("common.anlegen")} />}>
      <Fehlerzeile text={fehler} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {!job && !!templates?.length && (
          <div className="sm:col-span-2">
            <Feld label={tr("jobs_panel.aus_vorlage")} hinweis={tr("jobs_panel.fuellt_das_formular_vor")}>
              <select value="" onChange={(e) => e.target.value && useTemplate(e.target.value)} className={EINGABE}>
                <option value="">—</option>
                {templates.map((t) => <option key={t.key} value={t.key}>{t.label}: {t.beschreibung}</option>)}
              </select>
            </Feld>
          </div>
        )}
        <Feld label={tr("jobs_panel.name")}>
          <input value={f.name} autoFocus onChange={(e) => setF({ ...f, name: e.target.value })} className={EINGABE} />
        </Feld>
        <Feld label={tr("jobs_panel.schedule")}>
          <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })}
            className={`${EINGABE} font-mono`} />
        </Feld>
        <Feld label={tr("jobs_panel.takt")}>
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={EINGABE}>
            <option value="cron">cron</option><option value="interval">interval</option><option value="once">once</option>
          </select>
        </Feld>
        {/* Ein Job ist Zeitplan plus Ablauf. Fragen, Skript, Aufruf — das waren einmal
            eigene Arten, die je genau eins konnten; heute sind es Knoten IM Ablauf. */}
        <Feld label={tr("jobs_panel.art")}>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className={EINGABE}>
            <option value="workflow">{tr("jobs_panel.art_ablauf")}</option>
            <option value="film">{tr("jobs_panel.art_film")}</option>
          </select>
        </Feld>

        {f.kind === "workflow" && (
          <Feld label={tr("jobs_panel.prozess_waehlen")}>
            <select value={f.workflow_definition_id ?? ""}
              onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value ? Number(e.target.value) : null })}
              className={EINGABE}>
              <option value="">—</option>
              {defs?.filter((d) => d.current_version_id).map((d) => (
                <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
              ))}
            </select>
          </Feld>
        )}
        {f.kind === "workflow" && (
          <div className="sm:col-span-2">
            <Feld label={tr("jobs_panel.startkontext")}>
              <textarea value={paramText} onChange={(e) => setParams(e.target.value)} rows={4}
                placeholder={tr("jobs_panel.startkontext_platzhalter")}
                className={`${EINGABE} font-mono text-xs`} />
            </Feld>
            <div className="mt-1 text-xs">
              {paramFehler
                ? <span className="text-red-400">{paramFehler} — {tr("jobs_panel.parameter_nicht_uebernommen")}</span>
                : <span className="text-muted">{tr("jobs_panel.startkontext_hinweis")}</span>}
            </div>
          </div>
        )}
        {f.kind === "film" && (
          <Feld label={tr("jobs_panel.meldung")}>
            <select value={f.notify_mode} onChange={(e) => setF({ ...f, notify_mode: e.target.value })} className={EINGABE}>
              <option value="always">{tr("jobs_panel.notify_immer")}</option>
              <option value="never">{tr("jobs_panel.notify_nie")}</option>
            </select>
          </Feld>
        )}
        {f.kind === "workflow" && (
          <p className="text-xs text-muted sm:col-span-2">
            {tr("jobs_panel.meldung_im_ablauf")}
          </p>
        )}
      </div>
    </Dialog>
  );
}
