import { useMemo, useState } from "react";
import { tr } from "../i18n";
import { Issue, Project, ProjectMeta } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};
const PRIO_RANK: Record<string, number> = { highest: 4, high: 3, medium: 2, low: 1, lowest: 0 };

type SortKey = "key" | "summary" | "priority" | "status" | "agent";

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

      {/* Five columns cannot be held on a phone: the title column was left squeezed into two
          words per line. From sm on the sortable table, below that cards. */}
      <table className="hidden w-full text-sm sm:table">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <Th onClick={() => clickSort("key")} className="w-24">{tr("issue_list.key")}<Arrow k="key" /></Th>
            <Th onClick={() => clickSort("summary")}>{tr("issue_list.title")}<Arrow k="summary" /></Th>
            <Th onClick={() => clickSort("agent")} className="w-32">{tr("issue_list.agent")}<Arrow k="agent" /></Th>
            <Th onClick={() => clickSort("status")} className="w-32">{tr("issue_list.status")}<Arrow k="status" /></Th>
            <Th onClick={() => clickSort("priority")} className="w-20">{tr("issue_list.prio")}<Arrow k="priority" /></Th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((i) => {
            const t = typeMap.get(i.type_id);
            return (
              <tr key={i.id} {...ticketOpenHandlers(i.key, onOpen)}
                className="cursor-pointer border-b border-line/50 hover:bg-card">
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
            <tr><td colSpan={5} className="py-6 text-center text-sm text-muted">{tr("issue_list.no_matches")}</td></tr>
          )}
        </tbody>
      </table>

      <div className="space-y-1.5 sm:hidden">
        {filtered.map((i) => {
          const t = typeMap.get(i.type_id);
          const w = waitInfo(i);
          return (
            <div key={i.id} {...ticketOpenHandlers(i.key, onOpen)}
              className="cursor-pointer rounded-lg border border-line bg-card p-2 text-sm">
              <div className="flex items-baseline gap-2">
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
