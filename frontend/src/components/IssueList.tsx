import { useMemo } from "react";
import { Issue, Project, ProjectMeta } from "../api";
import { type OnOpenTicket } from "../ticketOpen";
import { Area } from "./ui";
import BulkBar from "./issues/BulkBar";
import IssueTable from "./issues/IssueTable";
import { IssueFilterRow, useFiltered, useIssueFilter } from "./issues/IssueFilter";
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
  const filter = useIssueFilter();
  const { filtered, count } = useFiltered(issues, filter);
  const { ticked, chosen, tick, setMany, clear } = useSelection();

  // Only what is visible can be acted on: a filter that hides a ticket must not act on it.
  const shown = useMemo(() => new Set(filtered.map((i) => i.key)), [filtered]);
  const picked = useMemo(() => chosen.filter((k) => shown.has(k)), [chosen, shown]);

  return (
    <div>
      <BulkBar project={project} meta={meta} picked={picked} archived={archived} onDone={clear} />
      {/* The filter belongs to the list, so it stands in the tool row of its card. The card
          itself is the rule of the design guide: a list stands in one. */}
      <Area tools={<IssueFilterRow meta={meta} filter={filter} count={count} />}>
        <IssueTable meta={meta} issues={filtered} onOpen={onOpen}
          ticked={ticked} onTick={tick} onSetMany={setMany} />
      </Area>
    </div>
  );
}
