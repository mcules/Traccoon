import { useMemo, useState } from "react";
import { tr } from "../../i18n";
import { Issue, ProjectMeta } from "../../api";
import { waitInfo } from "../../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../../ticketOpen";

/**
 * The one shape a list of tickets has in this house.
 *
 * List, backlog and archive used to answer the same question three times: a sortable table,
 * bordered rows with a dropdown in them, and the table again. Three answers to "show me
 * tickets" is exactly what the design guide exists to abolish, so there is one table now and
 * the three views differ in what they hand it, not in how it looks.
 *
 * It owns its sorting (a table sorts what it shows) and nothing else. The selection lives
 * with the caller: the backlog draws one table per sprint, and ticking across two of them has
 * to end up in one bulk action.
 */

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};
const PRIO_RANK: Record<string, number> = { highest: 4, high: 3, medium: 2, low: 1, lowest: 0 };

type SortKey = "key" | "summary" | "priority" | "status" | "agent";

export default function IssueTable({
  meta, issues, onOpen, ticked, onTick, onSetMany, empty,
}: {
  meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
  ticked: Set<string>;
  /** `shift` means: take the range from the anchor. The caller holds the anchor, because it
   *  is the one that knows which rows stand beside each other across several tables. */
  onTick: (rows: Issue[], key: string, index: number, shift: boolean) => void;
  /** The box in the header: all rows of THIS table on or off. In the backlog that is one
   *  sprint, not the whole page. */
  onSetMany: (keys: string[], on: boolean) => void;
  empty?: string;
}) {
  const statusMap = useMemo(() => new Map(meta.statuses.map((s) => [s.id, s.name])), [meta]);
  const typeMap = useMemo(() => new Map(meta.types.map((t) => [t.id, t])), [meta]);
  const [sort, setSort] = useState<SortKey>("key");
  const [asc, setAsc] = useState(true);

  const rows = useMemo(() => {
    const value = (i: Issue): string | number =>
      sort === "priority" ? (PRIO_RANK[i.priority] ?? -1)
        : sort === "status" ? (statusMap.get(i.status_id) || "")
          : sort === "agent" ? (i.assigned_agent || "")
            : sort === "summary" ? i.summary.toLowerCase()
              : i.number;
    return [...issues].sort((a, b) => {
      const va = value(a), vb = value(b);
      const c = va < vb ? -1 : va > vb ? 1 : 0;
      return asc ? c : -c;
    });
  }, [issues, sort, asc, statusMap]);

  const allTicked = rows.length > 0 && rows.every((i) => ticked.has(i.key));

  const clickSort = (k: SortKey) => {
    if (sort === k) setAsc(!asc);
    else { setSort(k); setAsc(true); }
  };
  const Arrow = ({ k }: { k: SortKey }) => sort === k ? <span>{asc ? " ▲" : " ▼"}</span> : null;
  const nothing = empty ?? tr("issue_list.no_matches");

  return (
    <>
      {/* Five columns cannot be held on a phone: the title column was left squeezed into two
          words per line. From sm on the sortable table, below that cards. */}
      <table className="hidden w-full text-sm sm:table">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="w-8 py-2 pr-2">
              <input type="checkbox" checked={allTicked} title={tr("issue_list.choose_all")}
                onChange={() => onSetMany(rows.map((i) => i.key), !allTicked)} />
            </th>
            <Th onClick={() => clickSort("key")} className="w-24">{tr("issue_list.key")}<Arrow k="key" /></Th>
            <Th onClick={() => clickSort("summary")}>{tr("issue_list.title")}<Arrow k="summary" /></Th>
            <Th onClick={() => clickSort("agent")} className="w-32">{tr("issue_list.agent")}<Arrow k="agent" /></Th>
            <Th onClick={() => clickSort("status")} className="w-32">{tr("issue_list.status")}<Arrow k="status" /></Th>
            <Th onClick={() => clickSort("priority")} className="w-20">{tr("issue_list.prio")}<Arrow k="priority" /></Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i, index) => {
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
                    onChange={(e) => onTick(rows, i.key, index,
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
          {!rows.length && (
            <tr><td colSpan={6} className="py-6 text-center text-sm text-muted">{nothing}</td></tr>
          )}
        </tbody>
      </table>

      <div className="space-y-1.5 sm:hidden">
        {rows.map((i, index) => {
          const t = typeMap.get(i.type_id);
          const w = waitInfo(i);
          return (
            <div key={i.id} {...ticketOpenHandlers(i.key, onOpen)}
              className={`cursor-pointer rounded-lg border bg-card p-2 text-sm ${
                ticked.has(i.key) ? "border-brand" : "border-line"}`}>
              <div className="flex items-baseline gap-2">
                <input type="checkbox" checked={ticked.has(i.key)} className="mt-0.5"
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => { e.stopPropagation(); onTick(rows, i.key, index, false); }} />
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
        {!rows.length && <div className="py-6 text-center text-sm text-muted">{nothing}</div>}
      </div>
    </>
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
