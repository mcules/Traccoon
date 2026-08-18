import { useState } from "react";
import { tr } from "../i18n";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, workflowApi, MyDashboard, MyTicket, ProjectMeta, WorkflowTaskLite } from "../api";
import { waitInfo } from "../lib/waitReason";
import { formatTime } from "../lib/formatTime";
import { NODE_TYPE_LABELS } from "./workflow/types";
import WorkflowTaskForm from "./workflow/WorkflowTaskForm";

const PRIO_FARBE: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400", medium: "text-yellow-400",
  low: "text-sky-400", lowest: "text-slate-400",
};
const KAT_LABEL: Record<string, string> = { todo: "Offen", in_progress: "In Arbeit", done: "Erledigt" };

export default function MyWork() {
  const { data } = useQuery({
    queryKey: ["my-dashboard"],
    queryFn: () => api.get<MyDashboard>("/me/dashboard"),
    refetchInterval: 8000,
  });
  const { data: wfTasks } = useQuery({
    queryKey: ["workflow-tasks"],
    queryFn: () => workflowApi.myTasks(),
    refetchInterval: 8000,
  });
  const [openTask, setOpenTask] = useState<WorkflowTaskLite | null>(null);
  if (!data) return null;

  const s = data.stats;
  const leer =
    data.action.length === 0 && data.assigned.length === 0 && (wfTasks?.length ?? 0) === 0;

  return (
    <div className="mb-6 space-y-4">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-3 sm:gap-3 lg:grid-cols-6">
        <Kachel label={tr("my_work.wartet_auf_dich")} wert={s.action}
          farbe={s.action ? "text-yellow-400" : undefined} />
        <Kachel label={tr("my_work.mir_zugewiesen")} wert={s.assigned} />
        <Kachel label={tr("my_work.laeuft_gerade")} wert={s.working}
          farbe={s.working ? "text-sky-400" : undefined} />
        <Kachel label={tr("my_work.erledigt_7_t")} wert={s.done_7d} farbe="text-green-400" />
        <Kachel label={tr("my_work.projekte")} wert={s.projects} />
        <Link to="/inbox" className="block">
          <Kachel label={tr("my_work.ungelesen")} wert={s.unread}
            farbe={s.unread ? "text-brand" : undefined} />
        </Link>
      </div>

      {(wfTasks?.length ?? 0) > 0 && (
        <Sektion titel={`🧭 ${tr("my_work.meine_schritte")}`} hinweis={tr("my_work.meine_schritte_hinweis")}>
          <div className="space-y-1.5">
            {wfTasks!.map((t) => (
              <button
                key={t.step_id}
                onClick={() => setOpenTask(t)}
                className="flex w-full items-center gap-3 rounded-md border border-line bg-surface px-3 py-2 text-left hover:border-brand"
              >
                <span className="rounded bg-card px-1.5 py-0.5 text-[11px] text-muted">
                  {tr(NODE_TYPE_LABELS[t.node_type])}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm">
                  {t.node_config.label || t.definition_name}
                </span>
                {t.issue_key && <span className="shrink-0 font-mono text-xs text-muted">{t.issue_key}</span>}
                {t.project_key && (
                  <span className="hidden shrink-0 font-mono text-xs text-muted sm:inline">{t.project_key}</span>
                )}
                <span className="hidden shrink-0 text-xs text-muted lg:inline">{formatTime(t.entered_at)}</span>
              </button>
            ))}
          </div>
        </Sektion>
      )}

      {data.action.length > 0 && (
        <Sektion titel={`⚡ ${tr("my_work.braucht_dich")}`} hinweis={tr("my_work.braucht_dich_hinweis")}>
          <ProjektGruppen tickets={data.action} />
        </Sektion>
      )}

      {data.assigned.length > 0 && (
        <Sektion titel={`📋 ${tr("my_work.mir_zugewiesen")}`} hinweis={tr("my_work.mir_zugewiesen_hinweis")}>
          <ProjektGruppen tickets={data.assigned} />
        </Sektion>
      )}

      {leer && (
        <div className="rounded-lg border border-line bg-card p-4 text-sm text-muted">
          🎉 {tr("my_work.nichts_offen")}
        </div>
      )}

      {openTask && <TaskModal task={openTask} onClose={() => setOpenTask(null)} />}
    </div>
  );
}

function TaskModal({ task, onClose }: { task: WorkflowTaskLite; onClose: () => void }) {
  const { data: meta } = useQuery({
    queryKey: ["meta", task.project_id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${task.project_id}/meta`),
    enabled: !!task.project_id,
  });
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-xl border border-line bg-card p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-medium">{task.node_config.label || task.definition_name}</div>
          <button onClick={onClose} className="text-muted hover:text-ink">
            ✕
          </button>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="rounded bg-surface px-1.5 py-0.5">{tr(NODE_TYPE_LABELS[task.node_type])}</span>
          {task.issue_key && <span className="font-mono">{task.issue_key}</span>}
          {task.project_key && <span className="font-mono">{task.project_key}</span>}
        </div>
        <WorkflowTaskForm
          iid={task.instance_id}
          sid={task.step_id}
          nodeType={task.node_type}
          config={task.node_config}
          members={meta?.members || []}
          onDone={onClose}
        />
      </div>
    </div>
  );
}

function ProjektGruppen({ tickets }: { tickets: MyTicket[] }) {
  // Nach Projekt gruppieren, Reihenfolge des ersten Auftretens (bereits nach updated_at sortiert) behalten.
  const gruppen: { id: number; key: string; name: string; items: MyTicket[] }[] = [];
  const idx = new Map<number, number>();
  for (const t of tickets) {
    let i = idx.get(t.project_id);
    if (i === undefined) {
      i = gruppen.length;
      idx.set(t.project_id, i);
      gruppen.push({ id: t.project_id, key: t.project_key, name: t.project_name, items: [] });
    }
    gruppen[i].items.push(t);
  }

  return (
    <div className="space-y-3">
      {gruppen.map((g) => (
        <div key={g.id}>
          <Link to={`/projects/${g.key}`}
            className="mb-1.5 flex min-h-[36px] items-center gap-2 text-xs font-medium text-muted hover:text-ink md:min-h-[28px]">
            <span className="font-mono">{g.key}</span>
            <span className="truncate">{g.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-[11px]">{g.items.length}</span>
          </Link>
          <div className="space-y-1.5">
            {g.items.map((t) => <TicketZeile key={t.key} t={t} />)}
          </div>
        </div>
      ))}
    </div>
  );
}

function TicketZeile({ t }: { t: MyTicket }) {
  const wi = waitInfo(t);
  return (
    <Link to={`/projects/${t.project_key}/tickets/${t.key}`}
      className="flex items-center gap-3 rounded-md border border-line bg-surface px-3 py-2 hover:border-brand">
      <span className="font-mono text-xs text-muted" title={t.project_name}>{t.key}</span>
      <span className="min-w-0 flex-1 truncate text-sm">{t.summary}</span>
      {t.agent_working && <span className="text-xs text-sky-400" title={tr("my_work.agent_arbeitet")}>{tr("my_work.laeuft")}</span>}
      {wi && (
        <span className={`shrink-0 text-xs ${
          wi.kind === "error" ? "text-red-400" : wi.kind === "question" ? "text-yellow-400" : "text-muted"
        }`} title={wi.title}>{wi.icon} {wi.label}</span>
      )}
      {t.assigned_agent && <span className="hidden shrink-0 text-xs text-muted sm:inline">🤖 {t.assigned_agent}</span>}
      <span className={`hidden shrink-0 text-xs sm:inline ${PRIO_FARBE[t.priority] || "text-muted"}`}>{t.priority}</span>
      <span className="hidden shrink-0 text-xs text-muted md:inline">{KAT_LABEL[t.category] || t.category}</span>
      <span className="hidden shrink-0 text-xs text-muted lg:inline">{formatTime(t.updated_at)}</span>
    </Link>
  );
}

function Kachel({ label, wert, farbe }: { label: string; wert: number; farbe?: string }) {
  return (
    <div className="rounded-lg border border-line bg-card p-2 sm:p-3">
      <div className={`text-xl font-semibold sm:text-2xl ${farbe || "text-ink"}`}>{wert}</div>
      <div className="truncate text-[11px] leading-tight text-muted sm:text-xs" title={label}>{label}</div>
    </div>
  );
}

function Sektion({ titel, hinweis, children }: { titel: string; hinweis: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-1 text-sm font-medium">{titel}</div>
      <div className="mb-3 text-xs text-muted">{hinweis}</div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}
