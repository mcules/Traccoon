import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Issue, Project, ProjectMeta, Status } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";

const PRIO_COLOR: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};

const WAIT_KIND_COLOR: Record<string, string> = {
  error: "bg-red-500/20 text-red-300",
  question: "bg-yellow-500/20 text-yellow-300",
  external: "bg-sky-500/20 text-sky-300",
};

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

  // Split-Beziehungen aus der geladenen Ticket-Liste ableiten.
  const childrenByParent = new Map<number, Issue[]>();
  for (const i of issues) {
    if (i.parent_ticket_id != null) {
      const arr = childrenByParent.get(i.parent_ticket_id) || [];
      arr.push(i);
      childrenByParent.set(i.parent_ticket_id, arr);
    }
  }

  const qc = useQueryClient();
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  const [moveErr, setMoveErr] = useState<string | null>(null);

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

  const dropIn = (colId: number, idx: number) => {
    setOverCol(null);
    setOverIdx(null);
    if (!dragKey || move.isPending) return;
    const key = dragKey;
    setDragKey(null);
    const dragIssue = issues.find((x) => x.key === key);
    if (!dragIssue) return;
    let targetIdx = idx;
    if (dragIssue.status_id === colId) {
      // Ziel = Ursprungsspalte: Index muss sich auf die "anderen" Tickets
      // beziehen (Backend zählt ohne das gezogene Ticket selbst).
      const sameColItems = issues
        .filter((x) => x.status_id === colId)
        .sort((a, b) => a.rank.localeCompare(b.rank));
      const originalIdx = sameColItems.findIndex((x) => x.key === key);
      if (originalIdx !== -1 && idx > originalIdx) targetIdx = idx - 1;
      if (originalIdx === targetIdx) return; // No-Op: keine tatsächliche Verschiebung
    }
    move.mutate({ key, status_id: colId, position: targetIdx });
  };

  return (
    <div>
      {moveErr && (
        <div className="mb-3 flex items-center justify-between rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          <span>{moveErr}</span>
          <button onClick={() => setMoveErr(null)} className="text-red-400 hover:text-red-300">✕</button>
        </div>
      )}
      <div className="flex gap-3 overflow-x-auto pb-4">
        {cols.map((s) => {
          const items = issues
            .filter((i) => i.status_id === s.id)
            .sort((a, b) => a.rank.localeCompare(b.rank));
          const dragOverThisCol = overCol === s.id;
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
              onDrop={(e) => {
                e.preventDefault();
                dropIn(s.id, overIdx ?? items.length);
              }}
            >
              <div className="mb-2 flex items-center justify-between px-1 text-xs font-medium uppercase text-muted">
                <span>{s.name}</span>
                <span>{items.length}</span>
              </div>
              <div className="space-y-2">
                {items.length === 0 && dragOverThisCol && (
                  <div className="h-10 rounded border-2 border-dashed border-brand/60" />
                )}
                {items.map((i, idx) => {
                  const kids = childrenByParent.get(i.id);
                  const isUmbrella = !!kids && kids.length > 0;
                  const sibs = i.parent_ticket_id != null ? childrenByParent.get(i.parent_ticket_id) : undefined;
                  const isChild = !!sibs;
                  const wait = waitInfo(i);
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
                        onDrop={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          dropIn(s.id, overIdx ?? idx);
                        }}
                        className={`block w-full rounded border bg-card p-2.5 text-left hover:border-brand ${
                          isChild ? "border-l-2 border-l-brand/60 border-line" : "border-line"} ${
                          dragKey === i.key ? "opacity-40" : ""} ${canDrag ? "cursor-grab active:cursor-grabbing" : ""}`}>
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
                          {wait && (
                            <span title={`${wait.title}: ${wait.label}`}
                              className={`rounded px-1 ${WAIT_KIND_COLOR[wait.kind]}`}>
                              {wait.icon} {wait.label}
                            </span>
                          )}
                          <div className="flex-1" />
                          {project.my_ai_assign && i.assigned_agent && (
                            <span className="rounded bg-brand/20 px-1 text-brand">
                              🤖 {i.agent_working ? "läuft" : i.agent_status || i.assigned_agent}
                            </span>
                          )}
                        </div>
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
