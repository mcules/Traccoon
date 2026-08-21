import { useState } from "react";
import { formatDateTime } from "../lib/formatTime";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListenLine, DeleteDialog, BUTTON } from "./ui";

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
  const [deleteJob, setDeleteJob] = useState<any | null>(null);
  const [err, setErr] = useState("");

  const inv = () => qc.invalidateQueries({ queryKey: ["jobs"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const save = useMutation({
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
    onSuccess: () => { setDeleteJob(null); inv(); }, onError: fail });

  return (
    <Area hint={tr("jobs_panel.scheduled_jobs_cron_e")}>
      <Errorrow text={err} />
      <Listing className="mb-4">
        {jobs?.map((j) => (
          <ListenLine key={j.id} dimmed={!j.enabled}>
            <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-medium">{j.name}</span>
                {!j.enabled && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("jobs_panel.off")}</span>}
                {j.enabled && j.paused && <span className="rounded bg-surface px-1 text-xs text-amber-400">{tr("jobs_panel.paused")}</span>}
                <span className="font-mono text-xs text-muted">{j.type}:{j.schedule} · {j.kind}</span>
              </div>
              {j.last_run_at && (
                <div className="text-xs text-muted">{tr("jobs_panel.last")} {formatDateTime(j.last_run_at)}</div>
              )}
            </div>
            <Actions>
              <IconButton icon={j.enabled ? "⏸" : "⏵"} onClick={() => toggle.mutate(j)}
                title={tr(j.enabled ? "jobs_panel.switch_off" : "jobs_panel.switch")} />
              <IconButton icon={ICON.start} title={tr("jobs_panel.run_now")}
                onClick={() => run.mutate(j.id)} disabled={run.isPending} />
              <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => { setErr(""); setDialog(j); }} />
              <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => setDeleteJob(j)} />
            </Actions>
            </div>
          </ListenLine>
        ))}
        {jobs?.length === 0 && <ListingEmpty>{tr("jobs_panel.no_jobs")}</ListingEmpty>}
      </Listing>
      <button onClick={() => { setErr(""); setDialog({}); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("jobs_panel.new_job")}
      </button>

      {dialog && (
        <JobDialog job={dialog.id ? dialog : null} error={err} runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(body, id) => save.mutate({ id, body })} />
      )}
      {deleteJob && (
        <DeleteDialog was={deleteJob.name} runs={del.isPending}
          onClose={() => setDeleteJob(null)} onDelete={() => del.mutate(deleteJob.id)} />
      )}
    </Area>
  );
}

function JobDialog({ job, error: error, runs: running, onClose, onSave }: {
  job: any | null; error: string; runs: boolean;
  onClose: () => void; onSave: (body: any, id: number | null) => void;
}) {
  // For kind=workflow: published definitions to choose from.
  const { data: defs } = useQuery({
    queryKey: ["workflow-defs"],
    queryFn: () => api.get<{ id: number; name: string; key: string; current_version_id: number | null }[]>("/workflows"),
  });
  // Templates only prefill the form; the job carries its own fields afterwards.
  const { data: templates } = useQuery({
    queryKey: ["job-templates"],
    queryFn: () => api.get<{ key: string; label: string; description: string;
                             params: Record<string, any>; fields: Record<string, any> }[]>("/jobs/templates"),
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

  const paramError = (() => {
    if (!paramText.trim()) return "";
    try {
      const v = JSON.parse(paramText);
      return v && typeof v === "object" && !Array.isArray(v) ? "" : tr("jobs_panel.object_expected_e_g");
    } catch { return tr("jobs_panel.not_valid_json"); }
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
    setF((p: any) => ({ ...p, ...t.fields, args: t.params }));
    setParamText(JSON.stringify(t.params, null, 2));
  };

  return (
    <Dialog wide title={job ? tr("jobs_panel.edit_job") : tr("jobs_panel.new_job")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!f.name.trim()} runs={running}
        onSave={() => onSave(f, job ? job.id : null)}
        saveText={job ? undefined : tr("common.create")} />}>
      <Errorrow text={error} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {!job && !!templates?.length && (
          <div className="sm:col-span-2">
            <Field label={tr("jobs_panel.template")} hint={tr("jobs_panel.prefills_the_form")}>
              <select value="" onChange={(e) => e.target.value && useTemplate(e.target.value)} className={INPUT_VALUE}>
                <option value="">—</option>
                {templates.map((t) => <option key={t.key} value={t.key}>{t.label}: {t.description}</option>)}
              </select>
            </Field>
          </div>
        )}
        <Field label={tr("jobs_panel.name")}>
          <input value={f.name} autoFocus onChange={(e) => setF({ ...f, name: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("jobs_panel.schedule")}>
          <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })}
            className={`${INPUT_VALUE} font-mono`} />
        </Field>
        <Field label={tr("jobs_panel.schedule_type")}>
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={INPUT_VALUE}>
            <option value="cron">cron</option><option value="interval">interval</option><option value="once">once</option>
          </select>
        </Field>
        {/* Ein Job ist Zeitplan plus Ablauf. Fragen, Skript, Aufruf — das waren einmal
            kinds of their own, each able to do exactly one thing; today they are nodes IN the flow. */}
        <Field label={tr("jobs_panel.kind")}>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className={INPUT_VALUE}>
            <option value="workflow">{tr("jobs_panel.flow")}</option>
            <option value="film">{tr("jobs_panel.end_day_film")}</option>
          </select>
        </Field>

        {f.kind === "workflow" && (
          <Field label={tr("jobs_panel.choose_process")}>
            <select value={f.workflow_definition_id ?? ""}
              onChange={(e) => setF({ ...f, workflow_definition_id: e.target.value ? Number(e.target.value) : null })}
              className={INPUT_VALUE}>
              <option value="">—</option>
              {defs?.filter((d) => d.current_version_id).map((d) => (
                <option key={d.id} value={d.id}>{d.name} ({d.key})</option>
              ))}
            </select>
          </Field>
        )}
        {f.kind === "workflow" && (
          <div className="sm:col-span-2">
            <Field label={tr("jobs_panel.start_context")}>
              <textarea value={paramText} onChange={(e) => setParams(e.target.value)} rows={4}
                placeholder={tr("jobs_panel.start_context_json_e")}
                className={`${INPUT_VALUE} font-mono text-xs`} />
            </Field>
            <div className="mt-1 text-xs">
              {paramError
                ? <span className="text-red-400">{paramError} — {tr("jobs_panel.parameters_not_applied")}</span>
                : <span className="text-muted">{tr("jobs_panel.available_flow_context_usable")}</span>}
            </div>
          </div>
        )}
        {f.kind === "film" && (
          <Field label={tr("jobs_panel.notification")}>
            <select value={f.notify_mode} onChange={(e) => setF({ ...f, notify_mode: e.target.value })} className={INPUT_VALUE}>
              <option value="always">{tr("jobs_panel.always")}</option>
              <option value="never">{tr("jobs_panel.never")}</option>
            </select>
          </Field>
        )}
        {f.kind === "workflow" && (
          <p className="text-xs text-muted sm:col-span-2">
            {tr("jobs_panel.what_gets_reported_stands")}
          </p>
        )}
      </div>
    </Dialog>
  );
}
