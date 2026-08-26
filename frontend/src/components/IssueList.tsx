import { useMemo, useState } from "react";
import { tr } from "../i18n";
import { Issue, Project, ProjectMeta } from "../api";
import { type OnOpenTicket } from "../ticketOpen";
import BulkBar from "./issues/BulkBar";
import IssueTable from "./issues/IssueTable";
import { useSelection } from "./issues/useSelection";

/**
 * The tickets as a list: filter above, handles when something is ticked, one table.
 *
 * The archive is the same view over the archived tickets, which is why it is this component
 * as well and not a third answer to the same question.
 */
export default function IssueList({
  project, meta, issues, onOpen, archived = false,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
  archived?: boolean;
}) {
  const [q, setQ] = useState("");
  const [statusId, setStatusId] = useState<number | "">("");
  const [onlyAi, setOnlyAi] = useState(false);
  const { ticked, chosen, tick, setMany, clear } = useSelection();

  const filtered = useMemo(() => {
    let rows = issues;
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter((i) => i.summary.toLowerCase().includes(s) || i.key.toLowerCase().includes(s));
    }
    if (statusId !== "") rows = rows.filter((i) => i.status_id === statusId);
    if (onlyAi) rows = rows.filter((i) => i.assigned_agent);
    return rows;
  }, [issues, q, statusId, onlyAi]);

  // Only what is visible can be acted on: a filter that hides a ticket must not act on it.
  const shown = useMemo(() => new Set(filtered.map((i) => i.key)), [filtered]);
  const picked = useMemo(() => chosen.filter((k) => shown.has(k)), [chosen, shown]);

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

      <BulkBar project={project} meta={meta} picked={picked} archived={archived} onDone={clear} />

      <IssueTable meta={meta} issues={filtered} onOpen={onOpen}
        ticked={ticked} onTick={tick} onSetMany={setMany} />
    </div>
  );
}
