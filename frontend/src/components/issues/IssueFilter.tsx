import { useCallback, useMemo, useState, type ReactNode } from "react";
import { tr } from "../../i18n";
import { Issue, ProjectMeta } from "../../api";

/**
 * Search, state, only-with-agent: the filter over a set of tickets.
 *
 * It sits in its own file because three views ask the same question of the same tickets, and
 * a filter that is written out per view drifts: the list would learn a field the backlog
 * never gets, and the same word would find different things on two pages.
 */
export function useIssueFilter() {
  const [q, setQ] = useState("");
  const [statusId, setStatusId] = useState<number | "">("");
  const [onlyAi, setOnlyAi] = useState(false);

  const keep = useCallback((issues: Issue[]) => {
    let rows = issues;
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter((i) => i.summary.toLowerCase().includes(s) || i.key.toLowerCase().includes(s));
    }
    if (statusId !== "") rows = rows.filter((i) => i.status_id === statusId);
    if (onlyAi) rows = rows.filter((i) => i.assigned_agent);
    return rows;
  }, [q, statusId, onlyAi]);

  return { q, setQ, statusId, setStatusId, onlyAi, setOnlyAi, keep };
}

export type IssueFilterState = ReturnType<typeof useIssueFilter>;

/** The filter as a row, for the tool row of a card. `count` stands at the right end. */
export function IssueFilterRow({ meta, filter, count }: {
  meta: ProjectMeta; filter: IssueFilterState; count?: ReactNode;
}) {
  return (
    <>
      <input value={filter.q} onChange={(e) => filter.setQ(e.target.value)}
        placeholder={tr("issue_list.search_title_or_key")}
        className="w-64 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
      <select value={filter.statusId}
        onChange={(e) => filter.setStatusId(e.target.value ? +e.target.value : "")}
        className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
        <option value="">{tr("issue_list.all_states")}</option>
        {meta.statuses.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <label className="flex items-center gap-1.5 text-sm text-muted">
        <input type="checkbox" checked={filter.onlyAi}
          onChange={(e) => filter.setOnlyAi(e.target.checked)} />
        {tr("issue_list.only_with_agent")}
      </label>
      <div className="flex-1" />
      {count}
    </>
  );
}

/** What a view shows after filtering, plus the `12 / 30` for the tool row. */
export function useFiltered(issues: Issue[], filter: IssueFilterState) {
  const filtered = useMemo(() => filter.keep(issues), [issues, filter.keep]);
  const count = (
    <span className="text-xs text-muted">{filtered.length} / {issues.length}</span>
  );
  return { filtered, count };
}
