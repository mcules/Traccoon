import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Issue, Project, ProjectMeta } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";
import { BUTTON, BUTTON_SMALL, BUTTON_TEXT} from "./ui";

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400",
  medium: "text-muted", low: "text-muted", lowest: "text-muted",
};

export default function Backlog({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["issues", project.id] });
    qc.invalidateQueries({ queryKey: ["meta", project.id] });
  };
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const setSprint = useMutation({
    mutationFn: (v: { key: string; sprint_id: number | null }) =>
      api.put(`/issues/${v.key}`, { sprint_id: v.sprint_id }),
    onSuccess: inv, onError: error,
  });
  const fresh = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/sprints`, { name }),
    onSuccess: () => { setName(""); inv(); }, onError: error,
  });
  const action = useMutation({
    mutationFn: (v: { id: number; was: "start" | "complete" }) =>
      api.post(`/projects/${project.id}/sprints/${v.id}/${v.was}`),
    onSuccess: inv, onError: error,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/projects/${project.id}/sprints/${id}`),
    onSuccess: inv, onError: error,
  });

  const openSprints = (meta.sprints || []).filter((s: any) => s.state !== "closed");
  const backlog = issues.filter((i) => !i.sprint_id);

  const Line = (i: Issue) => (
    <div key={i.id} className="flex items-center gap-3 rounded border border-line bg-card px-2 py-1.5 text-sm">
      <button {...ticketOpenHandlers(i.key, onOpen)} className={BUTTON_TEXT.secondary}>{i.key}</button>
      <span className="flex-1 truncate">{i.summary}</span>
      {(() => { const w = waitInfo(i); return w && (
        <span title={`${w.title}: ${w.label}`} className="text-xs">{w.icon}</span>
      ); })()}
      {i.assigned_agent && <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">🤖 {i.assigned_agent}</span>}
      <span className={`text-xs ${PRIO_COLOR[i.priority] || "text-muted"}`}>{i.priority}</span>
      <select value={i.sprint_id ?? ""} onChange={(e) =>
        setSprint.mutate({ key: i.key, sprint_id: e.target.value ? +e.target.value : null })}
        className="rounded border border-line bg-surface px-1.5 py-0.5 text-xs text-ink">
        <option value="">{tr("backlog.backlog")}</option>
        {openSprints.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
    </div>
  );

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {err && <div className="text-sm text-red-400">{err}</div>}

      {openSprints.map((s: any) => {
        const inside = issues.filter((i) => i.sprint_id === s.id);
        const done = inside.filter((i) => i.resolved_at).length;
        return (
          <section key={s.id} className="rounded-lg border border-line p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="font-medium">{s.name}</span>
              {s.state === "active"
                ? <span className="rounded bg-green-500/20 px-1.5 text-xs text-green-400">{tr("backlog.running")}</span>
                : <span className="rounded bg-surface px-1.5 text-xs text-muted">geplant</span>}
              <span className="text-xs text-muted">{inside.length} Tickets · {done} fertig</span>
              <div className="flex-1" />
              {s.state === "active" ? (
                <button onClick={() => action.mutate({ id: s.id, was: "complete" })}
                  className={BUTTON_SMALL.secondary}>
                  {tr("backlog.finish")}</button>
              ) : (
                <button onClick={() => action.mutate({ id: s.id, was: "start" })}
                  className={BUTTON.primary}>{tr("backlog.start")}</button>
              )}
              {!inside.length && (
                <button onClick={() => remove.mutate(s.id)}
                  className={BUTTON_TEXT.danger}>{tr("common.delete_2")}</button>
              )}
            </div>
            <div className="space-y-1">
              {inside.length ? inside.map(Line)
                : <div className="text-xs text-muted">{tr("backlog.nothing_assigned_yet")}</div>}
            </div>
          </section>
        );
      })}

      <section className="rounded-lg border border-line p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="font-medium">{tr("backlog.backlog")}</span>
          <span className="text-xs text-muted">{backlog.length} Tickets</span>
          <div className="flex-1" />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("backlog.new_sprint")}
            className="rounded border border-line bg-surface px-2 py-1 text-xs" />
          <button onClick={() => name.trim() && fresh.mutate()}
            className={BUTTON_SMALL.secondary}>+ Sprint</button>
        </div>
        <div className="space-y-1">
          {backlog.length ? backlog.map(Line)
            : <div className="text-xs text-muted">{tr("backlog.backlog_empty")}</div>}
        </div>
      </section>
    </div>
  );
}
