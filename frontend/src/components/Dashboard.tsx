import { useQuery } from "@tanstack/react-query";
import { api, Project, ProjectCosts } from "../api";

const KAT_LABEL: Record<string, string> = { todo: "Offen", in_progress: "In Arbeit", done: "Erledigt" };
const KAT_FARBE: Record<string, string> = { todo: "bg-slate-400", in_progress: "bg-sky-400", done: "bg-green-400" };

export default function Dashboard({ project }: { project: Project }) {
  const { data } = useQuery({
    queryKey: ["dashboard", project.id],
    queryFn: () => api.get<any>(`/projects/${project.id}/dashboard`),
    refetchInterval: 8000,
  });
  const { data: costs } = useQuery({
    queryKey: ["project-costs", project.id],
    queryFn: () => api.get<ProjectCosts>(`/projects/${project.id}/costs`),
    refetchInterval: 8000,
  });
  if (!data) return <div className="text-muted">Lädt…</div>;

  const t = data.tickets, r = data.runs;
  const kats: [string, number][] = Object.entries(t.by_category);

  return (
    <div className="max-w-4xl space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kachel label="Tickets" wert={t.total} />
        <Kachel label="Wartet auf dich" wert={t.waiting_for_human}
          farbe={t.waiting_for_human ? "text-yellow-400" : undefined} />
        <Kachel label="Läuft gerade" wert={t.working}
          farbe={t.working ? "text-sky-400" : undefined} />
        <Kachel label={`Erledigt (${data.window_days} T.)`} wert={data.throughput.done_in_window}
          farbe="text-green-400" />
      </div>

      <Karte titel="Tickets nach Status">
        {t.total > 0 ? (
          <>
            <div className="flex h-3 overflow-hidden rounded">
              {kats.map(([k, n]) => (
                <div key={k} className={KAT_FARBE[k] || "bg-slate-500"}
                  style={{ width: `${(n / t.total) * 100}%` }} title={`${KAT_LABEL[k] || k}: ${n}`} />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted">
              {kats.map(([k, n]) => (
                <span key={k} className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${KAT_FARBE[k] || "bg-slate-500"}`} />
                  {KAT_LABEL[k] || k}: <b className="text-ink">{n}</b>
                </span>
              ))}
              {t.failed_state > 0 && <span className="text-red-400">Fehlgeschlagen: {t.failed_state}</span>}
            </div>
          </>
        ) : <Leer />}
      </Karte>

      <div className="grid gap-3 sm:grid-cols-2">
        <Karte titel={`Agentenläufe (${data.window_days} Tage)`}>
          {r.total > 0 ? (
            <div className="space-y-2 text-sm">
              <Zeile label="Läufe gesamt" wert={r.total} />
              <Zeile label="Erfolgsquote"
                wert={r.success_rate === null ? "—" : `${r.success_rate}%`}
                farbe={r.success_rate === null ? "" : r.success_rate >= 66 ? "text-green-400"
                  : r.success_rate >= 33 ? "text-yellow-400" : "text-red-400"} />
              <Zeile label="Kosten" wert={`$${r.cost_usd.toFixed(4)}`} />
              <div className="border-t border-line pt-2 text-xs text-muted">
                {Object.entries(r.by_status).map(([s, v]: any) => (
                  <span key={s} className="mr-3">{s}: <b className="text-ink">{v.count}</b></span>
                ))}
              </div>
            </div>
          ) : <Leer text="Noch keine Läufe im Zeitraum." />}
        </Karte>

        <Karte titel="Agenten-Auslastung">
          {data.agents.length ? (
            <div className="space-y-2">
              {data.agents.map((a: any) => (
                <div key={a.agent} className="flex items-center gap-2 text-sm">
                  <span className="w-28 truncate">🤖 {a.agent}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded bg-surface">
                    <div className="h-full bg-brand"
                      style={{ width: `${(a.count / data.agents[0].count) * 100}%` }} />
                  </div>
                  <span className="w-6 text-right text-xs text-muted">{a.count}</span>
                </div>
              ))}
            </div>
          ) : <Leer text="Kein Agent hat offene Tickets." />}
        </Karte>
      </div>

      {costs && costs.by_model.length > 0 && (
        <Karte titel={`Kosten nach Modell (gesamt $${costs.total_usd.toFixed(2)})`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th className="py-1 pr-3 font-medium">Modell</th>
                  <th className="py-1 pr-3 text-right font-medium">Kosten</th>
                  <th className="py-1 pr-3 text-right font-medium">In</th>
                  <th className="py-1 text-right font-medium">Out</th>
                </tr>
              </thead>
              <tbody>
                {costs.by_model
                  .slice()
                  .sort((a, b) => b.usd - a.usd)
                  .map((m) => (
                    <tr key={`${m.provider}/${m.model}`} className="border-t border-line">
                      <td className="py-1 pr-3 text-ink">{m.model || m.provider}</td>
                      <td className="py-1 pr-3 text-right text-ink">${m.usd.toFixed(2)}</td>
                      <td className="py-1 pr-3 text-right text-muted">{m.input_tokens}</td>
                      <td className="py-1 text-right text-muted">{m.output_tokens}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Karte>
      )}
    </div>
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

function Karte({ titel, children }: { titel: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-3 text-sm font-medium">{titel}</div>
      {children}
    </div>
  );
}

function Zeile({ label, wert, farbe }: { label: string; wert: string | number; farbe?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className={farbe || "text-ink"}>{wert}</span>
    </div>
  );
}

function Leer({ text = "Noch keine Daten." }: { text?: string }) {
  return <div className="text-xs text-muted">{text}</div>;
}
