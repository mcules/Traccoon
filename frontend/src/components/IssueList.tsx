import { useMemo, useState } from "react";
import { Issue, Project, ProjectMeta } from "../api";

const PRIO_FARBE: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};
const PRIO_RANG: Record<string, number> = { highest: 4, high: 3, medium: 2, low: 1, lowest: 0 };

type SortKey = "key" | "summary" | "priority" | "status" | "agent";

export default function IssueList({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: (k: string) => void;
}) {
  const statusMap = useMemo(() => new Map(meta.statuses.map((s) => [s.id, s.name])), [meta]);
  const typeMap = useMemo(() => new Map(meta.types.map((t) => [t.id, t])), [meta]);

  const [q, setQ] = useState("");
  const [statusId, setStatusId] = useState<number | "">("");
  const [nurKi, setNurKi] = useState(false);
  const [sort, setSort] = useState<SortKey>("key");
  const [asc, setAsc] = useState(true);

  const gefiltert = useMemo(() => {
    let rows = issues;
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter((i) => i.summary.toLowerCase().includes(s) || i.key.toLowerCase().includes(s));
    }
    if (statusId !== "") rows = rows.filter((i) => i.status_id === statusId);
    if (nurKi) rows = rows.filter((i) => i.assigned_agent);
    const wert = (i: Issue): string | number =>
      sort === "priority" ? (PRIO_RANG[i.priority] ?? -1)
        : sort === "status" ? (statusMap.get(i.status_id) || "")
          : sort === "agent" ? (i.assigned_agent || "")
            : sort === "summary" ? i.summary.toLowerCase()
              : i.number;
    return [...rows].sort((a, b) => {
      const va = wert(a), vb = wert(b);
      const c = va < vb ? -1 : va > vb ? 1 : 0;
      return asc ? c : -c;
    });
  }, [issues, q, statusId, nurKi, sort, asc, statusMap]);

  const klickSort = (k: SortKey) => {
    if (sort === k) setAsc(!asc);
    else { setSort(k); setAsc(true); }
  };
  const Pfeil = ({ k }: { k: SortKey }) => sort === k ? <span>{asc ? " ▲" : " ▼"}</span> : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Suche (Titel oder Schlüssel)…"
          className="w-64 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <select value={statusId} onChange={(e) => setStatusId(e.target.value ? +e.target.value : "")}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
          <option value="">Alle Status</option>
          {meta.statuses.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-muted">
          <input type="checkbox" checked={nurKi} onChange={(e) => setNurKi(e.target.checked)} />
          nur mit Agent
        </label>
        <div className="flex-1" />
        <span className="text-xs text-muted">{gefiltert.length} / {issues.length}</span>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <Th onClick={() => klickSort("key")} className="w-24">Schlüssel<Pfeil k="key" /></Th>
            <Th onClick={() => klickSort("summary")}>Titel<Pfeil k="summary" /></Th>
            <Th onClick={() => klickSort("agent")} className="w-32">Agent<Pfeil k="agent" /></Th>
            <Th onClick={() => klickSort("status")} className="w-32">Status<Pfeil k="status" /></Th>
            <Th onClick={() => klickSort("priority")} className="w-20">Prio<Pfeil k="priority" /></Th>
          </tr>
        </thead>
        <tbody>
          {gefiltert.map((i) => {
            const t = typeMap.get(i.type_id);
            return (
              <tr key={i.id} onClick={() => onOpen(i.key)}
                className="cursor-pointer border-b border-line/50 hover:bg-card">
                <td className="py-1.5 font-mono text-xs text-muted">{i.key}</td>
                <td className="py-1.5">
                  {t && <span className="mr-1.5" style={{ color: t.color }}>{t.icon === "bug" ? "🐞" : "•"}</span>}
                  {i.summary}
                  {i.agent_working && <span className="ml-2 text-xs text-yellow-400">läuft…</span>}
                </td>
                <td className="py-1.5 text-xs">
                  {i.assigned_agent
                    ? <span className="rounded bg-brand/20 px-1.5 text-brand">🤖 {i.assigned_agent}</span>
                    : <span className="text-muted">—</span>}
                </td>
                <td className="py-1.5 text-xs text-muted">{statusMap.get(i.status_id) || "—"}</td>
                <td className={`py-1.5 text-xs ${PRIO_FARBE[i.priority] || "text-muted"}`}>{i.priority}</td>
              </tr>
            );
          })}
          {!gefiltert.length && (
            <tr><td colSpan={5} className="py-6 text-center text-sm text-muted">Keine Treffer.</td></tr>
          )}
        </tbody>
      </table>
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
