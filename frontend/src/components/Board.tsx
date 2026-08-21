import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Issue, Project, ProjectMeta, Status } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";
import { BUTTON_TEXT } from "./ui";

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};

const WAIT_KIND_COLOR: Record<string, string> = {
  error: "bg-red-500/20 text-red-300",
  question: "bg-yellow-500/20 text-yellow-300",
  external: "bg-sky-500/20 text-sky-300",
};

// Remember collapsed columns per project (purely local, no server setting).
const collapseKey = (projectId: number) => `traccoon.board.collapsed.${projectId}`;

function loadCollapsed(projectId: number): Set<number> {
  try {
    const raw = localStorage.getItem(collapseKey(projectId));
    return new Set<number>(raw ? (JSON.parse(raw) as number[]) : []);
  } catch {
    return new Set<number>();
  }
}

export default function Board({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
}) {
  const board = meta.boards[0];
  const statusMap = new Map(meta.statuses.map((s) => [s.id, s] as const));
  const typeMap = new Map(meta.types.map((t) => [t.id, t] as const));
  const cols: Status[] = board
    ? board.columns.map((c) => statusMap.get(c.status_id)).filter((s): s is Status => !!s)
    : meta.statuses;

  // Derive split relations from the loaded ticket list.
  const childrenByParent = new Map<number, Issue[]>();
  for (const i of issues) {
    if (i.parent_ticket_id != null) {
      const arr = childrenByParent.get(i.parent_ticket_id) || [];
      arr.push(i);
      childrenByParent.set(i.parent_ticket_id, arr);
    }
  }
  // Sorted tickets per column (for the desktop board AND the mobile single column).
  const itemsByCol = new Map<number, Issue[]>();
  for (const s of cols) {
    itemsByCol.set(s.id, issues.filter((i) => i.status_id === s.id)
      .sort((a, b) => a.rank.localeCompare(b.rank)));
  }

  const qc = useQueryClient();
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  const [moveErr, setMoveErr] = useState<string | null>(null);
  const [mobileCol, setMobileCol] = useState<number | null>(null);

  // Collapsed columns (desktop board only); reloaded on a project change.
  const [collapsed, setCollapsed] = useState<Set<number>>(() => loadCollapsed(project.id));
  useEffect(() => { setCollapsed(loadCollapsed(project.id)); }, [project.id]);
  const toggleCol = (id: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem(collapseKey(project.id), JSON.stringify([...next]));
      } catch {
        /* for instance private mode without storage: the state then holds only for this session */
      }
      return next;
    });

  const move = useMutation({
    mutationFn: (v: { key: string; status_id: number; position: number }) =>
      api.put(`/issues/${v.key}/move`, { status_id: v.status_id, position: v.position }),
    onSuccess: () => { setMoveErr(null); qc.invalidateQueries({ queryKey: ["issues", project.id] }); },
    onError: () => {
      setMoveErr("Verschieben fehlgeschlagen — bitte erneut versuchen.");
      qc.invalidateQueries({ queryKey: ["issues", project.id] });
    },
  });

  const canDrag = project.my_role !== "viewer" && !move.isPending;
  const canMove = project.my_role !== "viewer";

  /** In the test environment flow the way to "done" leads only over "set to done" on the
   * ticket (which stops the test environment, merges and only then sets done). The server
   * rejects the direct jump with a 409; here we additionally hide it. */
  const inTestenvFlow = (i: Issue) =>
    project.testenv_enabled !== false &&
    (i.agent_status === "to_test" || i.agent_status === "testing");
  const movableCols = (i: Issue) =>
    inTestenvFlow(i) ? cols.filter((s) => s.category !== "done") : cols;

  const dropIn = (colId: number, idx: number) => {
    setOverCol(null);
    setOverIdx(null);
    if (!dragKey || move.isPending) return;
    const key = dragKey;
    setDragKey(null);
    const dragIssue = issues.find((x) => x.key === key);
    if (!dragIssue) return;
    if (inTestenvFlow(dragIssue) && statusMap.get(colId)?.category === "done") {
      setMoveErr(tr("board.to_done_only_through_set_to_done_on_the_ticke"));
      return;
    }
    let targetIdx = idx;
    if (dragIssue.status_id === colId) {
      const sameColItems = (itemsByCol.get(colId) || []);
      const originalIdx = sameColItems.findIndex((x) => x.key === key);
      if (originalIdx !== -1 && idx > originalIdx) targetIdx = idx - 1;
      if (originalIdx === targetIdx) return;
    }
    move.mutate({ key, status_id: colId, position: targetIdx });
  };

  // Content of a card (title plus meta line), used in the desktop card AND the mobile card.
  const cardContent = (i: Issue) => {
    const kids = childrenByParent.get(i.id);
    const isUmbrella = !!kids && kids.length > 0;
    const sibs = i.parent_ticket_id != null ? childrenByParent.get(i.parent_ticket_id) : undefined;
    const isChild = !!sibs;
    const wait = waitInfo(i);
    return (
      <>
        <div className="text-sm">{i.summary}</div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="font-mono text-muted">{i.key}</span>
          <span style={{ color: typeMap.get(i.type_id)?.color }}>{typeMap.get(i.type_id)?.name}</span>
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
          {wait && (
            <span title={`${wait.title}: ${wait.label}`} className={`rounded px-1 ${WAIT_KIND_COLOR[wait.kind]}`}>
              {wait.icon} {wait.label}
            </span>
          )}
          <div className="flex-1" />
          {project.my_ai_assign && i.assigned_agent && (
            <span className="rounded bg-brand/20 px-1 text-brand">
              🤖 {i.agent_working ? tr("board.running") : i.agent_status || i.assigned_agent}
            </span>
          )}
        </div>
      </>
    );
  };

  // Mobile column selection: the active column (default = the first non-empty one, otherwise the first).
  const firstNonEmpty = cols.find((s) => (itemsByCol.get(s.id)?.length ?? 0) > 0)?.id;
  const activeCol = mobileCol ?? firstNonEmpty ?? cols[0]?.id ?? null;
  const activeItems = activeCol != null ? (itemsByCol.get(activeCol) || []) : [];
  const activeName = cols.find((s) => s.id === activeCol)?.name || "";

  return (
    <div>
      {moveErr && (
        <div className="mb-3 flex items-center justify-between rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          <span>{moveErr}</span>
          <button onClick={() => setMoveErr(null)} className={BUTTON_TEXT.danger}>✕</button>
        </div>
      )}

      {/* ── Mobil (< md): Status-Chips + eine Spalte, Status-Wechsel per Dropdown ── */}
      <div className="md:hidden">
        <div className="mb-3 flex gap-1.5 overflow-x-auto pb-1">
          {cols.map((s) => {
            const n = itemsByCol.get(s.id)?.length ?? 0;
            const active = s.id === activeCol;
            return (
              <button key={s.id} onClick={() => setMobileCol(s.id)}
                className={`flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-xs ${
                  active ? "border-brand bg-brand text-white" : "border-line text-muted hover:text-ink"
                }`}>
                <span>{s.name}</span>
                <span className={`rounded-full px-1 ${active ? "bg-white/20" : "bg-surface"}`}>{n}</span>
              </button>
            );
          })}
        </div>
        <div className="space-y-2">
          {activeItems.length === 0 && (
            <div className="rounded border border-line bg-surface px-3 py-6 text-center text-sm text-muted">
              {tr("board.no_tickets_column", { column: activeName })}
            </div>
          )}
          {activeItems.map((i) => (
            <div key={i.id} className="relative rounded-lg border border-line bg-card">
              <button {...ticketOpenHandlers(i.key, onOpen)} className="block w-full p-3 pr-28 text-left">
                {cardContent(i)}
              </button>
              {/* Am Handy zeigt die Spaltenauswahl oben schon, wo die Karte steht — die
                  Beschriftung „Status" daneben war eine Zeile Wiederholung je Karte. */}
              {canMove ? (
                <div className="absolute right-2 top-2">
                  <select
                    value={i.status_id}
                    title={tr("board.move")}
                    onChange={(e) => move.mutate({ key: i.key, status_id: Number(e.target.value), position: 0 })}
                    className="rounded border border-line bg-surface px-2 py-1 text-xs text-muted">
                    {movableCols(i).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {/* ── Desktop (md+): klassisches Mehrspalten-Board mit Drag & Drop ── */}
      <div className="hidden gap-3 overflow-x-auto pb-4 md:flex">
        {cols.map((s) => {
          const items = itemsByCol.get(s.id) || [];
          const dragOverThisCol = overCol === s.id;

          // Collapsed: a narrow pillar with a vertical name plus the count. It stays a drop
          // target (the card lands at the end of the column); a click expands it again.
          if (collapsed.has(s.id)) {
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => toggleCol(s.id)}
                title={`„${s.name}" ausklappen`}
                className={`flex w-10 shrink-0 cursor-pointer flex-col items-center gap-2 rounded-lg bg-surface py-2 text-muted hover:text-ink ${
                  dragOverThisCol ? "ring-1 ring-brand" : ""
                }`}
                onDragOver={(e) => {
                  if (!dragKey) return;
                  e.preventDefault();
                  setOverCol(s.id);
                  setOverIdx(items.length);
                }}
                onDrop={(e) => { e.preventDefault(); dropIn(s.id, items.length); }}
              >
                <span className="text-xs">›</span>
                <span className="rounded bg-card px-1 text-xs">{items.length}</span>
                <span className="text-xs font-medium uppercase [writing-mode:vertical-rl]">{s.name}</span>
              </button>
            );
          }

          return (
            <div
              key={s.id}
              className={`w-72 shrink-0 rounded-lg bg-surface p-2 ${dragOverThisCol ? "ring-1 ring-brand" : ""}`}
              onDragOver={(e) => {
                if (!dragKey) return;
                e.preventDefault();
                setOverCol(s.id);
                if (items.length === 0) setOverIdx(0);
              }}
              onDrop={(e) => { e.preventDefault(); dropIn(s.id, overIdx ?? items.length); }}
            >
              <div className="mb-2 flex items-center gap-1 px-1 text-xs font-medium uppercase text-muted">
                <button
                  type="button"
                  onClick={() => toggleCol(s.id)}
                  title={`„${s.name}" einklappen`}
                  className="shrink-0 rounded px-1 hover:bg-card hover:text-ink"
                >
                  ⌄
                </button>
                <span className="truncate">{s.name}</span>
                <div className="flex-1" />
                <span>{items.length}</span>
              </div>
              <div className="space-y-2">
                {items.length === 0 && dragOverThisCol && (
                  <div className="h-10 rounded border-2 border-dashed border-brand/60" />
                )}
                {items.map((i, idx) => {
                  const isChild = i.parent_ticket_id != null && childrenByParent.has(i.parent_ticket_id);
                  const showDropBefore = dragOverThisCol && overIdx === idx && dragKey !== i.key;
                  return (
                    <div key={i.id}>
                      {showDropBefore && <div className="mb-2 h-1.5 rounded bg-brand" />}
                      <button
                        {...ticketOpenHandlers(i.key, onOpen)}
                        draggable={canDrag}
                        onDragStart={(e) => {
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", i.key);
                          setDragKey(i.key);
                        }}
                        onDragEnd={() => { setDragKey(null); setOverCol(null); setOverIdx(null); }}
                        onDragOver={(e) => {
                          if (!dragKey || dragKey === i.key) return;
                          e.preventDefault();
                          e.stopPropagation();
                          const rect = e.currentTarget.getBoundingClientRect();
                          const before = e.clientY < rect.top + rect.height / 2;
                          setOverCol(s.id);
                          setOverIdx(before ? idx : idx + 1);
                        }}
                        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); dropIn(s.id, overIdx ?? idx); }}
                        className={`block w-full rounded border bg-card p-2.5 text-left hover:border-brand ${
                          isChild ? "border-l-2 border-l-brand/60 border-line" : "border-line"} ${
                          dragKey === i.key ? "opacity-40" : ""} ${canDrag ? "cursor-grab active:cursor-grabbing" : ""}`}>
                        {cardContent(i)}
                      </button>
                    </div>
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
