import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tr } from "../../i18n";
import { api, ApiError, Project, ProjectMeta } from "../../api";
import { BUTTON_SMALL, ConfirmDialog } from "../ui";

/**
 * The handles of a selection, in one place for list, backlog and archive.
 *
 * They appear only when something is ticked: as long as nothing is chosen the page is a list,
 * and a bar of buttons above it would be furniture for a case that is not there. The same
 * shape the mailbox uses.
 */

/** The roles a ticket can be handed to. The same list as in the ticket drawer. */
const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PRIOS = ["highest", "high", "medium", "low", "lowest"];
/** One class chain for the dropdowns of the bar, so they cannot drift apart. */
const SELECT = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

export type BulkAction = "status" | "priority" | "assignee" | "sprint" | "archive" | "unarchive"
  | "delete" | "assign_agent";
type BulkBody = {
  keys: string[]; action: BulkAction;
  status_id?: number; priority?: string; user_id?: number | null; sprint_id?: number | null;
  agent?: string;
};
type BulkFail = { key: string; error: string; error_key?: string; values?: Record<string, string> };
type BulkResult = { done: number; action: BulkAction; failed: BulkFail[] };

export default function BulkBar({
  project, meta, picked, onDone, sprints = false, archived = false,
}: {
  project: Project; meta: ProjectMeta; picked: string[]; onDone: () => void;
  /** The backlog moves tickets between sprints; the other two views have no sprint. */
  sprints?: boolean;
  /** In the archive the useful handle is the way back, not the way in. */
  archived?: boolean;
}) {
  const [ask, setAsk] = useState<null | { body: BulkBody; text: string; hint?: string;
                                          confirmText: string; danger: boolean }>(null);
  const [report, setReport] = useState<BulkResult | null>(null);
  const [err, setErr] = useState("");
  const qc = useQueryClient();

  const bulk = useMutation({
    // The keys travel WITH the call and are not read out of the surroundings: the selection
    // is emptied on success, and a mutation reading it afterwards would send nothing.
    mutationFn: (body: BulkBody) =>
      api.post<BulkResult>(`/projects/${project.id}/issues/bulk`, body),
    onSuccess: (r) => {
      setErr("");
      onDone();
      // Only what did not work is shown. A green "12 done" is the state of the list itself.
      setReport(r.failed.length ? r : null);
      qc.invalidateQueries({ queryKey: ["issues", project.id] });
      qc.invalidateQueries({ queryKey: ["issues-archived", project.id] });
      qc.invalidateQueries({ queryKey: ["meta", project.id] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")),
  });

  /** Run it, or ask first where the answer cannot be taken back. */
  const run = (body: BulkBody, confirm?: { text: string; hint?: string; confirmText: string;
                                            danger: boolean }) => {
    setReport(null);
    if (confirm) setAsk({ body, ...confirm });
    else bulk.mutate(body);
  };
  const open = meta.sprints?.filter((s) => s.state !== "closed") ?? [];

  return (
    <>
      {picked.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-card p-2">
          <span className="rounded bg-brand/20 px-1.5 py-0.5 text-xs text-brand">
            {tr("issue_list.n_chosen", { n: picked.length })}
          </span>

          <select value="" disabled={bulk.isPending} className={SELECT}
            onChange={(e) => e.target.value &&
              run({ keys: picked, action: "status", status_id: +e.target.value })}>
            <option value="">{tr("issue_list.set_status")}</option>
            {meta.statuses.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
          </select>

          <select value="" disabled={bulk.isPending} className={SELECT}
            onChange={(e) => e.target.value &&
              run({ keys: picked, action: "priority", priority: e.target.value })}>
            <option value="">{tr("issue_list.set_priority")}</option>
            {PRIOS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>

          <select value="" disabled={bulk.isPending} className={SELECT}
            onChange={(e) => e.target.value && run({
              keys: picked, action: "assignee",
              user_id: e.target.value === "none" ? null : +e.target.value,
            })}>
            <option value="">{tr("issue_list.set_assignee")}</option>
            <option value="none">{tr("issue_list.no_assignee")}</option>
            {meta.members.map((m) => (
              <option key={m.user_id} value={m.user_id}>{m.display_name || m.username}</option>
            ))}
          </select>

          {sprints && (
            <select value="" disabled={bulk.isPending} className={SELECT}
              onChange={(e) => e.target.value && run({
                keys: picked, action: "sprint",
                sprint_id: e.target.value === "none" ? null : +e.target.value,
              })}>
              <option value="">{tr("issue_list.set_sprint")}</option>
              <option value="none">{tr("backlog.backlog")}</option>
              {open.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          )}

          {meta.my_ai_assign && (
            <select value="" disabled={bulk.isPending} className={SELECT}
              onChange={(e) => e.target.value && run(
                { keys: picked, action: "assign_agent", agent: e.target.value },
                // The expensive one: a run starts per ticket, and the question says how many.
                { text: tr("issue_list.really_hand_n_tickets", { n: picked.length,
                                                                 agent: e.target.value }),
                  hint: tr("issue_list.one_paid_run_per_ticket"),
                  confirmText: tr("issue_list.hand_over"), danger: false })}>
              <option value="">{tr("issue_list.hand_to_agent")}</option>
              {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          )}

          <button disabled={bulk.isPending} className={BUTTON_SMALL.secondary}
            onClick={() => run({ keys: picked, action: archived ? "unarchive" : "archive" })}>
            {archived ? tr("issue_list.unarchive") : tr("issue_list.archive")}
          </button>
          <button disabled={bulk.isPending} className={BUTTON_SMALL.danger}
            onClick={() => run({ keys: picked, action: "delete" },
              { text: tr("issue_list.really_delete_n_tickets", { n: picked.length }),
                hint: tr("issue_list.deleting_takes_comments_and_runs_with_it"),
                confirmText: tr("common.delete"), danger: true })}>
            {tr("common.delete")}
          </button>

          <div className="flex-1" />
          <button className={BUTTON_SMALL.secondary} onClick={onDone}>
            {tr("issue_list.clear_selection")}
          </button>
        </div>
      )}

      {err && <div className="mb-3 text-sm text-red-400">{err}</div>}

      {/* What did NOT work, with the ticket and the reason. A selection is a rough
          instrument, and the one that refused is the interesting half of the answer. */}
      {report && (
        <div className="mb-3 rounded-lg border border-yellow-500/40 bg-card p-2 text-sm">
          <div className="mb-1 text-ink">
            {tr("issue_list.n_done_n_refused", { done: report.done, n: report.failed.length })}
          </div>
          <ul className="space-y-0.5 text-xs text-muted">
            {report.failed.map((f) => (
              <li key={f.key}>
                <span className="font-mono">{f.key}</span>{" — "}
                {f.error_key ? tr(f.error_key, f.values) : f.error}
              </li>
            ))}
          </ul>
          <button className="mt-2 text-xs text-brand" onClick={() => setReport(null)}>
            {tr("common.close")}
          </button>
        </div>
      )}

      {ask && (
        <ConfirmDialog title={ask.confirmText} text={ask.text} hint={ask.hint}
          confirmText={ask.confirmText} danger={ask.danger} runs={bulk.isPending}
          onClose={() => setAsk(null)}
          onConfirm={() => { const b = ask.body; setAsk(null); bulk.mutate(b); }} />
      )}
    </>
  );
}
