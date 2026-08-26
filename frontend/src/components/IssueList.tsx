import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api, ApiError, Issue, Project, ProjectMeta } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";
import { BUTTON_SMALL, ConfirmDialog } from "./ui";

/** The roles a ticket can be handed to. The same list as in the ticket drawer. */
const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PRIOS = ["highest", "high", "medium", "low", "lowest"];

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};
const PRIO_RANK: Record<string, number> = { highest: 4, high: 3, medium: 2, low: 1, lowest: 0 };

/** One class chain for the six dropdowns of the bar, so they cannot drift apart. */
const SELECT = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

type SortKey = "key" | "summary" | "priority" | "status" | "agent";
type BulkAction = "status" | "priority" | "assignee" | "archive" | "unarchive" | "delete"
  | "assign_agent";
type BulkBody = {
  keys: string[]; action: BulkAction;
  status_id?: number; priority?: string; user_id?: number | null; agent?: string;
};
type BulkFail = { key: string; error: string; error_key?: string; values?: Record<string, string> };
type BulkResult = { done: number; action: BulkAction; failed: BulkFail[] };

export default function IssueList({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
}) {
  const statusMap = useMemo(() => new Map(meta.statuses.map((s) => [s.id, s.name])), [meta]);
  const typeMap = useMemo(() => new Map(meta.types.map((t) => [t.id, t])), [meta]);

  const [q, setQ] = useState("");
  const [statusId, setStatusId] = useState<number | "">("");
  const [onlyAi, setOnlyAi] = useState(false);
  const [sort, setSort] = useState<SortKey>("key");
  const [asc, setAsc] = useState(true);

  const [chosen, setChosen] = useState<string[]>([]);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [ask, setAsk] = useState<null | { body: BulkBody; text: string; hint?: string;
                                          confirmText: string; danger: boolean }>(null);
  const [report, setReport] = useState<BulkResult | null>(null);
  const [err, setErr] = useState("");
  const qc = useQueryClient();

  const filtered = useMemo(() => {
    let rows = issues;
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter((i) => i.summary.toLowerCase().includes(s) || i.key.toLowerCase().includes(s));
    }
    if (statusId !== "") rows = rows.filter((i) => i.status_id === statusId);
    if (onlyAi) rows = rows.filter((i) => i.assigned_agent);
    const value = (i: Issue): string | number =>
      sort === "priority" ? (PRIO_RANK[i.priority] ?? -1)
        : sort === "status" ? (statusMap.get(i.status_id) || "")
          : sort === "agent" ? (i.assigned_agent || "")
            : sort === "summary" ? i.summary.toLowerCase()
              : i.number;
    return [...rows].sort((a, b) => {
      const va = value(a), vb = value(b);
      const c = va < vb ? -1 : va > vb ? 1 : 0;
      return asc ? c : -c;
    });
  }, [issues, q, statusId, onlyAi, sort, asc, statusMap]);

  const ticked = useMemo(() => new Set(chosen), [chosen]);
  // Only what is visible can be ticked: a filter that hides a ticket must not act on it.
  const shown = useMemo(() => new Set(filtered.map((i) => i.key)), [filtered]);
  const picked = useMemo(() => chosen.filter((k) => shown.has(k)), [chosen, shown]);
  const allTicked = filtered.length > 0 && filtered.every((i) => ticked.has(i.key));

  /**
   * The current state in a box that stays the same.
   *
   * A range tick needs the list and the anchor as they are RIGHT NOW. Through the closure
   * they would be the ones of the render the row was drawn in, and with a filter in between
   * that is a different list.
   */
  const now = useRef({ chosen, filtered, anchor });
  now.current = { chosen, filtered, anchor };

  const tick = useCallback((key: string, index: number, shift: boolean) => {
    const { chosen: had, filtered: rows, anchor: from_key } = now.current;
    const set = new Set(had);
    if (shift && from_key !== null) {
      const from = rows.findIndex((i) => i.key === from_key);
      if (from >= 0) {
        const [a, b] = from < index ? [from, index] : [index, from];
        // The range follows what the anchor did: ticking ticks, unticking unticks.
        const add = !set.has(key);
        rows.slice(a, b + 1).forEach((i) => (add ? set.add(i.key) : set.delete(i.key)));
        setChosen([...set]);
        return;
      }
    }
    set.has(key) ? set.delete(key) : set.add(key);
    setAnchor(key);
    setChosen([...set]);
  }, []);

  const bulk = useMutation({
    // The keys travel WITH the call and are not read out of the surroundings: the selection
    // is emptied on success, and a mutation reading it afterwards would send nothing.
    mutationFn: (body: BulkBody) =>
      api.post<BulkResult>(`/projects/${project.id}/issues/bulk`, body),
    onSuccess: (r) => {
      setErr("");
      setChosen([]);
      setAnchor(null);
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

  const clickSort = (k: SortKey) => {
    if (sort === k) setAsc(!asc);
    else { setSort(k); setAsc(true); }
  };
  const Arrow = ({ k }: { k: SortKey }) => sort === k ? <span>{asc ? " ▲" : " ▼"}</span> : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("issue_list.search_title_or_key")}
          className="w-64 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <select value={statusId} onChange={(e) => setStatusId(e.target.value ? +e.target.value : "")}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
          <option value="">{tr("issue_list.all_states")}</option>
          {meta.statuses.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-muted">
          <input type="checkbox" checked={onlyAi} onChange={(e) => setOnlyAi(e.target.checked)} />
          {tr("issue_list.only_with_agent")}
        </label>
        <div className="flex-1" />
        <span className="text-xs text-muted">{filtered.length} / {issues.length}</span>
      </div>

      {/* The handles appear only when something is ticked: as long as nothing is chosen the
          list is a list, and a bar of buttons above it would be furniture for a case that is
          not there. Same shape as in the mailbox. */}
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

          {meta.my_ai_assign && (
            <select value="" disabled={bulk.isPending} className={SELECT}
              onChange={(e) => e.target.value && run(
                { keys: picked, action: "assign_agent", agent: e.target.value },
                // The expensive one: a run starts per ticket, and the question says how many.
                { text: tr("issue_list.really_hand_n_tickets", { n: picked.length,
                                                                 agent: e.target.value }),
                  hint: tr("issue_list.one_paid_run_per_ticket"),
                  confirmText: tr("issue_list.hand_over"),
                  danger: false })}>
              <option value="">{tr("issue_list.hand_to_agent")}</option>
              {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          )}

          <button disabled={bulk.isPending} className={BUTTON_SMALL.secondary}
            onClick={() => run({ keys: picked, action: "archive" })}>
            {tr("issue_list.archive")}
          </button>
          <button disabled={bulk.isPending} className={BUTTON_SMALL.secondary}
            onClick={() => run({ keys: picked, action: "unarchive" })}>
            {tr("issue_list.unarchive")}
          </button>
          <button disabled={bulk.isPending} className={BUTTON_SMALL.danger}
            onClick={() => run({ keys: picked, action: "delete" },
              { text: tr("issue_list.really_delete_n_tickets", { n: picked.length }),
                hint: tr("issue_list.deleting_takes_comments_and_runs_with_it"),
                confirmText: tr("common.delete"), danger: true })}>
            {tr("common.delete")}
          </button>

          <div className="flex-1" />
          <button className={BUTTON_SMALL.secondary} onClick={() => { setChosen([]); setAnchor(null); }}>
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

      {/* Five columns cannot be held on a phone: the title column was left squeezed into two
          words per line. From sm on the sortable table, below that cards. */}
      <table className="hidden w-full text-sm sm:table">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="w-8 py-2 pr-2">
              <input type="checkbox" checked={allTicked} title={tr("issue_list.choose_all")}
                onChange={() => setChosen(allTicked ? [] : filtered.map((i) => i.key))} />
            </th>
            <Th onClick={() => clickSort("key")} className="w-24">{tr("issue_list.key")}<Arrow k="key" /></Th>
            <Th onClick={() => clickSort("summary")}>{tr("issue_list.title")}<Arrow k="summary" /></Th>
            <Th onClick={() => clickSort("agent")} className="w-32">{tr("issue_list.agent")}<Arrow k="agent" /></Th>
            <Th onClick={() => clickSort("status")} className="w-32">{tr("issue_list.status")}<Arrow k="status" /></Th>
            <Th onClick={() => clickSort("priority")} className="w-20">{tr("issue_list.prio")}<Arrow k="priority" /></Th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((i, index) => {
            const t = typeMap.get(i.type_id);
            return (
              <tr key={i.id} {...ticketOpenHandlers(i.key, onOpen)}
                className={`cursor-pointer border-b border-line/50 hover:bg-card ${
                  ticked.has(i.key) ? "bg-card" : ""}`}>
                {/* The tick box is the second way to touch a row; a click on it must not open
                    the ticket, otherwise every tick would bring the drawer along. */}
                <td className="py-1.5" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={ticked.has(i.key)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => tick(i.key, index,
                      (e.nativeEvent as MouseEvent).shiftKey === true)} />
                </td>
                <td className="py-1.5 font-mono text-xs text-muted">{i.key}</td>
                <td className="py-1.5">
                  {t && <span className="mr-1.5" style={{ color: t.color }}>{t.icon === "bug" ? "🐞" : "•"}</span>}
                  {i.summary}
                  {i.agent_working && <span className="ml-2 text-xs text-yellow-400">{tr("issue_list.running")}</span>}
                  {(() => { const w = waitInfo(i); return w && (
                    <span title={`${w.title}: ${w.label}`} className="ml-2 text-xs">{w.icon}</span>
                  ); })()}
                </td>
                <td className="py-1.5 text-xs">
                  {i.assigned_agent
                    ? <span className="rounded bg-brand/20 px-1.5 text-brand">🤖 {i.assigned_agent}</span>
                    : <span className="text-muted">—</span>}
                </td>
                <td className="py-1.5 text-xs text-muted">{statusMap.get(i.status_id) || "—"}</td>
                <td className={`py-1.5 text-xs ${PRIO_COLOR[i.priority] || "text-muted"}`}>{i.priority}</td>
              </tr>
            );
          })}
          {!filtered.length && (
            <tr><td colSpan={6} className="py-6 text-center text-sm text-muted">{tr("issue_list.no_matches")}</td></tr>
          )}
        </tbody>
      </table>

      <div className="space-y-1.5 sm:hidden">
        {filtered.map((i, index) => {
          const t = typeMap.get(i.type_id);
          const w = waitInfo(i);
          return (
            <div key={i.id} {...ticketOpenHandlers(i.key, onOpen)}
              className={`cursor-pointer rounded-lg border p-2 text-sm ${
                ticked.has(i.key) ? "border-brand bg-card" : "border-line bg-card"}`}>
              <div className="flex items-baseline gap-2">
                <input type="checkbox" checked={ticked.has(i.key)} className="mt-0.5"
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => { e.stopPropagation(); tick(i.key, index, false); }} />
                <span className="font-mono text-xs text-muted">{i.key}</span>
                {t && <span style={{ color: t.color }}>{t.icon === "bug" ? "🐞" : "•"}</span>}
                <span className="min-w-0 flex-1">{i.summary}</span>
                {w && <span title={`${w.title}: ${w.label}`}>{w.icon}</span>}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span className="text-muted">{statusMap.get(i.status_id) || "—"}</span>
                <span className={PRIO_COLOR[i.priority] || "text-muted"}>{i.priority}</span>
                {i.assigned_agent && (
                  <span className="rounded bg-brand/20 px-1.5 text-brand">🤖 {i.assigned_agent}</span>
                )}
                {i.agent_working && <span className="text-yellow-400">{tr("issue_list.running")}</span>}
              </div>
            </div>
          );
        })}
        {!filtered.length && (
          <div className="py-6 text-center text-sm text-muted">{tr("issue_list.no_matches")}</div>
        )}
      </div>
    </div>
  );
}

function Th({ children, onClick, className = "" }: {
  children: React.ReactNode; onClick: () => void; className?: string;
}) {
  return (
    <th onClick={onClick}
      className={`cursor-pointer select-none py-2 pr-2 font-medium hover:text-ink ${className}`}>
      {children}
    </th>
  );
}
