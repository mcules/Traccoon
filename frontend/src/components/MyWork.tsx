import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, MyDashboard, MyTicket } from "../api";
import { waitInfo } from "../lib/waitReason";
import { formatTime } from "../lib/formatTime";

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
  if (!data) return null;

  const s = data.stats;
  const leer = data.action.length === 0 && data.assigned.length === 0;

  return (
    <div className="mb-6 space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kachel label="Wartet auf dich" wert={s.action}
          farbe={s.action ? "text-yellow-400" : undefined} />
        <Kachel label="Mir zugewiesen" wert={s.assigned} />
        <Kachel label="Läuft gerade" wert={s.working}
          farbe={s.working ? "text-sky-400" : undefined} />
        <Kachel label="Erledigt (7 T.)" wert={s.done_7d} farbe="text-green-400" />
        <Kachel label="Projekte" wert={s.projects} />
        <Link to="/inbox" className="block">
          <Kachel label="Ungelesen" wert={s.unread}
            farbe={s.unread ? "text-brand" : undefined} />
        </Link>
      </div>

      {data.action.length > 0 && (
        <Sektion titel="⚡ Braucht deine Interaktion" hinweis="Ein Agent wartet auf deine Freigabe, Abnahme oder Antwort.">
          <ProjektGruppen tickets={data.action} />
        </Sektion>
      )}

      {data.assigned.length > 0 && (
        <Sektion titel="📋 Mir zugewiesen" hinweis="Offene Tickets, für die du verantwortlich bist.">
          <ProjektGruppen tickets={data.assigned} />
        </Sektion>
      )}

      {leer && (
        <div className="rounded-lg border border-line bg-card p-4 text-sm text-muted">
          🎉 Nichts wartet auf dich — keine offenen Tickets, die dir zugewiesen sind.
        </div>
      )}
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
            className="mb-1.5 flex items-center gap-2 text-xs font-medium text-muted hover:text-ink">
            <span className="font-mono">{g.key}</span>
            <span className="truncate">{g.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-[10px]">{g.items.length}</span>
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
    <Link to={`/projects/${t.project_key}?ticket=${t.key}`}
      className="flex items-center gap-3 rounded-md border border-line bg-surface px-3 py-2 hover:border-brand">
      <span className="font-mono text-xs text-muted" title={t.project_name}>{t.key}</span>
      <span className="min-w-0 flex-1 truncate text-sm">{t.summary}</span>
      {t.agent_working && <span className="text-xs text-sky-400" title="Agent arbeitet">▶ läuft</span>}
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
    <div className="rounded-lg border border-line bg-card p-3">
      <div className={`text-2xl font-semibold ${farbe || "text-ink"}`}>{wert}</div>
      <div className="text-xs text-muted">{label}</div>
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
