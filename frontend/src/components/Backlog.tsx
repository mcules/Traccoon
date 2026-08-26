import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api, ApiError, Issue, Project, ProjectMeta, Sprint } from "../api";
import { type OnOpenTicket } from "../ticketOpen";
import { Area, BUTTON, BUTTON_SMALL, BUTTON_TEXT } from "./ui";
import BulkBar from "./issues/BulkBar";
import { IssueFilterRow, useFiltered, useIssueFilter } from "./issues/IssueFilter";
import IssueTable from "./issues/IssueTable";
import { useSelection } from "./issues/useSelection";

/**
 * The backlog: the same table as the list, once per sprint plus the unplanned rest.
 *
 * What the backlog can do beyond a list is the sprint: create one, start it, finish it, and
 * move tickets in and out. The sections carry that; the tickets inside them are drawn by the
 * one table this house shows tickets in, so list, backlog and archive read alike.
 *
 * Moving into a sprint is a bulk action like the others, not a dropdown per row. Before, a
 * ticket could be moved in two ways that looked nothing alike, and one of them could only
 * ever move one at a time.
 */
export default function Backlog({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const filter = useIssueFilter();
  const { filtered, count } = useFiltered(issues, filter);
  const { ticked, chosen, tick, setMany, clear } = useSelection();

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["issues", project.id] });
    qc.invalidateQueries({ queryKey: ["meta", project.id] });
  };
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

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

  const open = useMemo(() => (meta.sprints || []).filter((s) => s.state !== "closed"), [meta]);
  const backlog = useMemo(() => filtered.filter((i) => !i.sprint_id), [filtered]);
  // Only what stands on this page can be acted on, and a filter that hides a ticket hides it
  // from the handles as well.
  const shown = useMemo(() => new Set(filtered.map((i) => i.key)), [filtered]);
  const picked = useMemo(() => chosen.filter((k) => shown.has(k)), [chosen, shown]);

  const table = (rows: Issue[], empty: string) => (
    <IssueTable meta={meta} issues={rows} onOpen={onOpen}
      ticked={ticked} onTick={tick} onSetMany={setMany} empty={empty} />
  );

  return (
    <div className="space-y-5">
      {err && <div className="text-sm text-red-400">{err}</div>}

      <BulkBar project={project} meta={meta} picked={picked} sprints onDone={clear} />

      {/* The filter stands over every section, which is why it is a card of its own and not
          the tool row of one of them: it searches the sprints as well as the rest. */}
      <Area tools={<IssueFilterRow meta={meta} filter={filter} count={count} />} />

      {open.map((s: Sprint) => {
        const inside = filtered.filter((i) => i.sprint_id === s.id);
        const done = inside.filter((i) => i.resolved_at).length;
        return (
          <Area key={s.id} title={s.name} tools={
            <>
              {s.state === "active"
                ? <span className="rounded bg-green-500/20 px-1.5 text-xs text-green-400">{tr("backlog.running")}</span>
                : <span className="rounded bg-surface px-1.5 text-xs text-muted">{tr("backlog.planned")}</span>}
              <span className="text-xs text-muted">
                {tr("backlog.n_tickets_n_done", { n: inside.length, done })}
              </span>
              <div className="flex-1" />
              {s.state === "active" ? (
                <button onClick={() => action.mutate({ id: s.id, was: "complete" })}
                  className={BUTTON_SMALL.secondary}>{tr("backlog.finish")}</button>
              ) : (
                <button onClick={() => action.mutate({ id: s.id, was: "start" })}
                  className={BUTTON.primary}>{tr("backlog.start")}</button>
              )}
              {!inside.length && (
                <button onClick={() => remove.mutate(s.id)}
                  className={BUTTON_TEXT.danger}>{tr("common.delete_2")}</button>
              )}
            </>
          }>
            {table(inside, tr("backlog.nothing_assigned_yet"))}
          </Area>
        );
      })}

      {/* No heading: the view switcher above already says "backlog", and a card that repeats
          the name of the page one is standing on says nothing. A sprint card carries a title
          because it names WHICH sprint; this one is the rest. */}
      <Area tools={
        <>
          <span className="text-xs text-muted">
            {tr("backlog.n_tickets", { n: backlog.length })}
          </span>
          <div className="flex-1" />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("backlog.new_sprint")}
            className="rounded border border-line bg-surface px-2 py-1 text-xs" />
          <button onClick={() => name.trim() && fresh.mutate()}
            className={BUTTON_SMALL.secondary}>{tr("backlog.add_sprint")}</button>
        </>
      }>
        {table(backlog, tr("backlog.backlog_empty"))}
      </Area>
    </div>
  );
}
