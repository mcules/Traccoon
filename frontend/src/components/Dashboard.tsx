import { useQuery } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api, Project, ProjectCosts } from "../api";
import { Area } from "./ui";
import DeploymentsPanel from "./DeploymentsPanel";

const CAT_KEY: Record<string, string> = { todo: "common.open_state", in_progress: "common.in_progress", done: "common.done_state" };
const CAT_COLOR: Record<string, string> = { todo: "bg-slate-400", in_progress: "bg-sky-400", done: "bg-green-400" };

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
  if (!data) return <div className="text-muted">{tr("dashboard.loading")}</div>;

  const t = data.tickets, r = data.runs;
  const cats: [string, number][] = Object.entries(t.by_category);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label={tr("dashboard.tickets")} value={t.total} />
        <Tile label={tr("dashboard.waiting")} value={t.waiting_for_human}
          color={t.waiting_for_human ? "text-yellow-400" : undefined} />
        <Tile label={tr("dashboard.running_now")} value={t.working}
          color={t.working ? "text-sky-400" : undefined} />
        <Tile label={tr("dashboard.done_days_d", { days: data.window_days })} value={data.throughput.done_in_window}
          color="text-green-400" />
      </div>

      <Karte title={tr("dashboard.tickets_state")}>
        {t.total > 0 ? (
          <>
            <div className="flex h-3 overflow-hidden rounded">
              {cats.map(([k, n]) => (
                <div key={k} className={CAT_COLOR[k] || "bg-slate-500"}
                  style={{ width: `${(n / t.total) * 100}%` }} title={`${(CAT_KEY[k] ? tr(CAT_KEY[k]) : k)}: ${n}`} />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted">
              {cats.map(([k, n]) => (
                <span key={k} className="flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${CAT_COLOR[k] || "bg-slate-500"}`} />
                  {(CAT_KEY[k] ? tr(CAT_KEY[k]) : k)}: <b className="text-ink">{n}</b>
                </span>
              ))}
              {t.failed_state > 0 && <span className="text-red-400">Fehlgeschlagen: {t.failed_state}</span>}
            </div>
          </>
        ) : <Empty />}
      </Karte>

      <div className="grid gap-3 sm:grid-cols-2">
        <Karte title={tr("dashboard.agent_runs_days_days", { days: data.window_days })}>
          {r.total > 0 ? (
            <div className="space-y-2 text-sm">
              <Line label={tr("dashboard.runs_total")} value={r.total} />
              <Line label={tr("dashboard.success_rate")}
                value={r.success_rate === null ? "—" : `${r.success_rate}%`}
                color={r.success_rate === null ? "" : r.success_rate >= 66 ? "text-green-400"
                  : r.success_rate >= 33 ? "text-yellow-400" : "text-red-400"} />
              <Line label={tr("dashboard.costs")} value={`$${r.cost_usd.toFixed(4)}`} />
              <div className="border-t border-line pt-2 text-xs text-muted">
                {Object.entries(r.by_status).map(([s, v]: any) => (
                  <span key={s} className="mr-3">{s}: <b className="text-ink">{v.count}</b></span>
                ))}
              </div>
            </div>
          ) : <Empty text={tr("dashboard.no_runs_period_yet")} />}
        </Karte>

        <Karte title={tr("dashboard.agent_load")}>
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
          ) : <Empty text={tr("dashboard.no_agent_open_tickets")} />}
        </Karte>
      </div>

      {/* Ungegatet: wer ein Ticket gemergt hat, will wissen, ob es draußen ist — und ist nicht
          necessarily a maintainer. The full list stands under Settings → Deployment. */}
      <Karte title={tr("dashboard.recent_deployments")}>
        <DeploymentsPanel projectId={project.id} variant="kompakt" limit={5} />
      </Karte>

      {costs && costs.by_model.length > 0 && (
        <Karte title={tr("dashboard.cost_model_total_sum", { sum: costs.total_usd.toFixed(2) })}>
          <div className="overflow-x-auto">
            <table className="hidden w-full text-sm sm:table">
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th className="py-1 pr-3 font-medium">{tr("dashboard.model")}</th>
                  <th className="py-1 pr-3 text-right font-medium">{tr("dashboard.costs")}</th>
                  <th className="py-1 pr-3 text-right font-medium">{tr("dashboard.in")}</th>
                  <th className="py-1 text-right font-medium">{tr("dashboard.out")}</th>
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

            <div className="divide-y divide-line text-sm sm:hidden">
              {costs.by_model.slice().sort((a, b) => b.usd - a.usd).map((m) => (
                <div key={`${m.provider}/${m.model}`} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
                  <span className="min-w-0 flex-1 break-all text-ink">{m.model || m.provider}</span>
                  <span className="tabular-nums text-ink">${m.usd.toFixed(2)}</span>
                  <span className="basis-full text-xs text-muted">
                    {tr("dashboard.in")} {m.input_tokens} · {tr("dashboard.out")} {m.output_tokens}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Karte>
      )}
    </div>
  );
}

function Tile({ label, value: value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="rounded-lg border border-line bg-card p-3">
      <div className={`text-2xl font-semibold ${color || "text-ink"}`}>{value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function Karte({ title: title, children }: { title: string; children: React.ReactNode }) {
  return <Area title={title}>{children}</Area>;
}

function Line({ label, value: value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className={color || "text-ink"}>{value}</span>
    </div>
  );
}

function Empty({ text }: { text?: string }) {
  return <div className="text-xs text-muted">{text || tr("dashboard.no_data_yet")}</div>;
}
