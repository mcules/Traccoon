import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { tr } from "../i18n";
import { ApiError, JobError, ProcRun, processApi, RunningRun, workflowApi, WorkflowTaskLite } from "../api";
import { formatTime } from "../lib/formatTime";
import { toast } from "../toast";
import { Area, ICON, IconButton, Listing, ListingEmpty, ListRow, Tag } from "./ui";
import { TaskModal, useMySteps, useMyWork } from "./MyWork";

/**
 * The machine side of the start page: what is running, and what is standing.
 *
 * The start page used to know only tickets. It said "3 running" as a number and left the
 * question of *what* to the office, and about a flow that had failed or a job that fell over
 * at night it said nothing at all — those two live in areas nobody opens without a reason,
 * which is precisely why they stayed unnoticed for days.
 */

/** How long ago, in words. Below an hour to the minute, above it to the hour. */
function since(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 60) return tr("ops.minutes", { n: min });
  const h = Math.round(min / 6) / 10;
  return h < 48 ? tr("ops.hours", { n: h }) : tr("ops.days", { n: Math.round(h / 24) });
}

/** Stuck flows: the same source the operations tab reads, filtered to what needs attention. */
export function useStuckFlows() {
  return useQuery({
    queryKey: ["proc-stuck"],
    queryFn: () => processApi.running({ onlyStuck: true }),
    refetchInterval: 30000,
  });
}

/** Agents at work right now, with what they are at. */
export function Running() {
  const { data } = useMyWork();
  const runs = data?.running || [];

  // Shown even when nothing runs. It stands beside "stuck", and a card that comes and goes
  // takes its neighbour across the page with it every time an agent finishes — an empty line
  // is the cheaper answer, and it is an answer: nobody is working right now.
  return (
    <Area title={`▶ ${tr("my_work.running_now")}`} hint={tr("ops.agents_working_right_now")}>
      <Listing>
        {runs.map((r) => <RunLine key={r.run_id} r={r} />)}
        {runs.length === 0 && <ListingEmpty>{tr("ops.nothing_running")}</ListingEmpty>}
      </Listing>
    </Area>
  );
}

/**
 * One agent at work: in, and watch.
 *
 * The row leads into the office, to the room of exactly this run — that is the place that
 * answers "what is it doing right now", live and to the second. It used to lead to the
 * ticket, which says what the work is about but not a word about what is happening in it,
 * and for a run without a ticket (the assistant, a job) it led nowhere at all.
 *
 * The ticket is still one click away: it hangs on its own key in the row. Deliberately not
 * as a link inside a link — that is not allowed and the browser makes of it what it wants —
 * but as a row that navigates and one handle in it that goes somewhere else.
 */
function RunLine({ r }: { r: RunningRun }) {
  const nav = useNavigate();
  return (
    <ListRow onClick={() => nav(`/office?sid=${encodeURIComponent(r.sid)}`)}>
      <div className="flex items-center gap-3" title={tr("ops.watch_in_office")}>
        <Tag color="blue">🤖 {r.agent || "?"}</Tag>
        <span className="min-w-0 flex-1 truncate text-sm">{r.summary || tr("ops.no_ticket")}</span>
        {r.issue_key && r.project_key && (
          <button type="button" title={tr("ops.to_ticket")}
            onClick={(e) => {
              e.stopPropagation();
              nav(`/projects/${r.project_key}/tickets/${r.issue_key}`);
            }}
            className="shrink-0 rounded px-1 font-mono text-xs text-muted hover:bg-card hover:text-brand">
            {r.issue_key}
          </button>
        )}
        {r.project_key && <span className="hidden shrink-0 font-mono text-xs text-muted sm:inline">{r.project_key}</span>}
        {r.phase && <span className="hidden shrink-0 text-xs text-muted md:inline">{r.phase}</span>}
        <span className="shrink-0 text-xs text-muted">{since(r.started_at)}</span>
      </div>
    </ListRow>
  );
}

/**
 * What is standing: failed or long waiting flows, plus jobs of mine that fell over.
 *
 * Both in one card on purpose. The question behind them is one and the same — "is something
 * broken?" — and two cards that are empty most of the time would take the room of an answer
 * to give none.
 */
export function Stuck() {
  const { data } = useMyWork();
  const { data: flows } = useStuckFlows();
  // The steps that wait for ME. A flow standing at one of them is not a case for the
  // operations page — it is a case for the form, and that one opens right here.
  const { data: tasks } = useMySteps();
  const [openTask, setOpenTask] = useState<WorkflowTaskLite | null>(null);
  const jobs = data?.job_errors || [];
  const stuck = flows || [];

  return (
    <Area title={`⚠ ${tr("ops.stuck")}`} hint={tr("ops.failed_flows_and_jobs")}>
      <Listing>
        {stuck.map((f) => (
          <FlowLine key={f.id} f={f} task={(tasks || []).find((t) => t.instance_id === f.id)}
            onTask={setOpenTask} />
        ))}
        {jobs.map((j) => <JobLine key={`${j.job_id}-${j.started_at}`} j={j} />)}
        {!stuck.length && !jobs.length && <ListingEmpty>{tr("ops.nothing_stuck")}</ListingEmpty>}
      </Listing>
      {openTask && <TaskModal task={openTask} onClose={() => setOpenTask(null)} />}
    </Area>
  );
}

/**
 * One standing flow: where it leads, and what one can do with it.
 *
 * The click goes **to the place where the matter can be settled**, and that is a different
 * one for every reason it stands still:
 *
 * * it waits for an answer from me → the form of that step, right here over the page;
 * * it hangs off a ticket → that ticket;
 * * otherwise → the operations list, opened at exactly this run and its history.
 *
 * Formerly every row led to the same list, where one then had to look for the run one had
 * just been pointing at.
 *
 * A failed run carries two handles of its own. Until now it could only be cancelled, which
 * is why six identical failures of the same flow stood in the list, none of them removable:
 * `↻` runs the same thing again (after the cause has been fixed — the usual case is a
 * service that was gone), `🗑` throws the run away for good.
 */
function FlowLine({ f, task, onTask }: {
  f: ProcRun; task?: WorkflowTaskLite; onTask: (t: WorkflowTaskLite) => void;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const failed = f.status === "failed";

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["proc-stuck"] });
    qc.invalidateQueries({ queryKey: ["proc-running"] });
    qc.invalidateQueries({ queryKey: ["my-dashboard"] });
  };
  const fail = (e: unknown) =>
    toast(e instanceof ApiError ? e.message : tr("common.error"), "error");

  const again = useMutation({
    mutationFn: () => workflowApi.restart(f.id),
    onSuccess: () => { refresh(); toast(tr("ops.restarted"), "success"); },
    onError: fail,
  });
  const drop = useMutation({
    mutationFn: () => workflowApi.remove(f.id),
    onSuccess: () => { refresh(); toast(tr("ops.deleted"), "success"); },
    onError: fail,
  });

  const open = () => {
    if (task) return onTask(task);
    if (f.subject_ref && f.project_key && !f.subject_ref.startsWith("HW-")) {
      return nav(`/projects/${f.project_key}/tickets/${f.subject_ref}`);
    }
    nav(`/processes/operations?run=${f.id}`);
  };

  return (
    <ListRow onClick={open}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Tag color={failed ? "red" : "yellow"}>
          {tr(failed ? "ops.flow_failed" : "ops.flow_hangs")}
        </Tag>
        <span className="min-w-0 flex-1 truncate text-sm">{f.definition_name}</span>
        {f.subject_ref && <span className="shrink-0 font-mono text-xs text-muted">{f.subject_ref}</span>}
        {f.project_key && <span className="hidden shrink-0 font-mono text-xs text-muted sm:inline">{f.project_key}</span>}
        <span className="shrink-0 text-xs text-muted">
          {f.hours != null ? tr("ops.hours", { n: f.hours }) : ""}
        </span>
        {failed && (
          // `stopPropagation`: the row leads somewhere, and a handle inside it must do its
          // own thing instead of both at once.
          <span className="flex shrink-0 items-center gap-1"
            onClick={(e) => { e.stopPropagation(); }}>
            <IconButton icon={ICON.again} title={tr("ops.restart")}
              disabled={again.isPending} onClick={() => again.mutate()} />
            <IconButton icon={ICON.remove} title={tr("ops.delete_run")} danger
              disabled={drop.isPending} onClick={() => drop.mutate()} />
          </span>
        )}
      </div>
      {(f.error || f.node_label) && (
        <div className="mt-0.5 truncate text-xs text-muted">
          {f.error || `${tr("ops.at_step")} ${f.node_label}`}
        </div>
      )}
      {task && (
        <div className="mt-0.5 text-xs text-brand">{tr("ops.answer_here")}</div>
      )}
    </ListRow>
  );
}

function JobLine({ j }: { j: JobError }) {
  return (
    <Link to="/settings/jobs"
      className="group block bg-surface px-3 py-2.5 transition-colors hover:bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Tag color="red">{tr("ops.job_failed")}</Tag>
        <span className="min-w-0 flex-1 truncate text-sm">{j.name}</span>
        <span className="shrink-0 text-xs text-muted">{formatTime(j.started_at)}</span>
      </div>
      {j.error && <div className="mt-0.5 truncate text-xs text-red-300">{j.error}</div>}
    </Link>
  );
}
