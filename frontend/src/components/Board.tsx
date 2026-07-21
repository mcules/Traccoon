import { Issue, Project, ProjectMeta, Status } from "../api";

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};

const HOLD_LABEL: Record<string, string> = {
  plan_review: "Plan-Freigabe", plan_split: "Aufteilung", question: "Rückfrage",
  review: "Review", permission: "Berechtigung", merge: "Merge", verify: "Verifikation",
  incomplete: "unvollständig", stuck: "steckt fest", cap: "Limit", interrupted: "gestoppt",
};

export default function Board({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: (k: string) => void;
}) {
  const board = meta.boards[0];
  const statusMap = new Map(meta.statuses.map((s) => [s.id, s] as const));
  const typeMap = new Map(meta.types.map((t) => [t.id, t] as const));
  const cols: Status[] = board
    ? board.columns.map((c) => statusMap.get(c.status_id)).filter((s): s is Status => !!s)
    : meta.statuses;

  // Split-Beziehungen aus der geladenen Ticket-Liste ableiten.
  const childrenByParent = new Map<number, Issue[]>();
  for (const i of issues) {
    if (i.parent_ticket_id != null) {
      const arr = childrenByParent.get(i.parent_ticket_id) || [];
      arr.push(i);
      childrenByParent.set(i.parent_ticket_id, arr);
    }
  }

  return (
    <div>
      <div className="flex gap-3 overflow-x-auto pb-4">
        {cols.map((s) => {
          const items = issues
            .filter((i) => i.status_id === s.id)
            .sort((a, b) => a.rank.localeCompare(b.rank));
          return (
            <div key={s.id} className="w-72 shrink-0 rounded-lg bg-surface p-2">
              <div className="mb-2 flex items-center justify-between px-1 text-xs font-medium uppercase text-muted">
                <span>{s.name}</span>
                <span>{items.length}</span>
              </div>
              <div className="space-y-2">
                {items.map((i) => {
                  const kids = childrenByParent.get(i.id);
                  const isUmbrella = !!kids && kids.length > 0;
                  const sibs = i.parent_ticket_id != null ? childrenByParent.get(i.parent_ticket_id) : undefined;
                  const isChild = !!sibs;
                  const holdBadge = i.hold_reason && (HOLD_LABEL[i.hold_reason] || i.hold_reason);
                  return (
                    <button key={i.id} onClick={() => onOpen(i.key)}
                      className={`block w-full rounded border bg-card p-2.5 text-left hover:border-brand ${
                        isChild ? "border-l-2 border-l-brand/60 border-line" : "border-line"}`}>
                      <div className="text-sm">{i.summary}</div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-mono text-muted">{i.key}</span>
                        <span style={{ color: typeMap.get(i.type_id)?.color }}>
                          {typeMap.get(i.type_id)?.name}
                        </span>
                        <span className={PRIO_COLOR[i.priority]}>●</span>
                        {isUmbrella && (
                          <span className="rounded bg-purple-500/20 px-1 text-purple-300">
                            🧩 Sammelticket {kids!.filter((k) => k.agent_status === "done").length}/{kids!.length}
                          </span>
                        )}
                        {isChild && (
                          <span className="rounded bg-purple-500/20 px-1 text-purple-300">
                            🧩 Teil {(i.split_order ?? 0) + 1}/{sibs!.length}
                          </span>
                        )}
                        {holdBadge && (
                          <span className="rounded bg-yellow-500/20 px-1 text-yellow-300">⏸ {holdBadge}</span>
                        )}
                        <div className="flex-1" />
                        {project.my_ai_assign && i.assigned_agent && (
                          <span className="rounded bg-brand/20 px-1 text-brand">
                            🤖 {i.agent_working ? "läuft" : i.agent_status || i.assigned_agent}
                          </span>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
