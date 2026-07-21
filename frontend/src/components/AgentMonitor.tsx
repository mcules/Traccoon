import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Project } from "../api";

const ST_COLOR: Record<string, string> = {
  running: "text-yellow-400", success: "text-green-400", done: "text-green-400",
  failed: "text-red-400", blocked: "text-orange-400", planned: "text-sky-400",
};

export default function AgentMonitor({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { data: runs } = useQuery({
    queryKey: ["runs", project.id], queryFn: () => api.get<any[]>(`/projects/${project.id}/runs`),
    refetchInterval: 4000,
  });
  const { data: perms } = useQuery({
    queryKey: ["permreqs", project.id],
    queryFn: () => api.get<any[]>(`/projects/${project.id}/permission-requests`),
    refetchInterval: 4000,
  });
  const { data: active } = useQuery({
    queryKey: ["active-runs", project.id],
    queryFn: () => api.get<any[]>(`/projects/${project.id}/active-runs`),
    refetchInterval: 3000,
  });
  const stop = useMutation({
    mutationFn: (key: string) => api.post(`/issues/${key}/stop`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["active-runs", project.id] });
      qc.invalidateQueries({ queryKey: ["issues", project.id] });
    },
  });
  const decide = useMutation({
    mutationFn: (v: { id: number; decision: string }) => api.post(`/permission-requests/${v.id}/decide`, { decision: v.decision }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["permreqs", project.id] }); qc.invalidateQueries({ queryKey: ["issues", project.id] }); },
  });

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 font-medium">▶️ Läuft gerade</h3>
        {active && active.length > 0 ? (
          <div className="space-y-2">
            {active.map((a) => (
              <div key={a.issue_key}
                className="flex flex-wrap items-center gap-3 rounded border border-yellow-400/40 bg-yellow-400/5 p-2.5 text-sm">
                <span className="font-mono">{a.issue_key}</span>
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs">🤖 {a.role}</span>
                <span className="text-xs text-muted">{a.phase === "planning" ? "plant" : "arbeitet"}</span>
                <span className="text-xs text-muted">seit {fmtDauer(a.running_seconds)}</span>
                <div className="flex-1" />
                <button onClick={() => stop.mutate(a.issue_key)}
                  className="rounded border border-red-400/50 px-2 py-1 text-xs text-red-400 hover:bg-red-400/10">
                  ⏹ Stoppen</button>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-muted">Kein Agent arbeitet gerade.</div>
        )}
      </section>

      {perms && perms.length > 0 && (
        <section>
          <h3 className="mb-2 font-medium text-orange-400">⚙️ Offene Berechtigungen</h3>
          <div className="space-y-2">
            {perms.map((p) => (
              <div key={p.id} className="flex items-center gap-3 rounded border border-orange-400/40 bg-orange-400/5 p-2.5 text-sm">
                <span className="font-mono">{p.issue_key}</span>
                <span>Tool <b>{p.tool}</b> auf <span className="font-mono">{p.resource || "—"}</span></span>
                <div className="flex-1" />
                {["once", "always", "never"].map((d) => (
                  <button key={d} onClick={() => decide.mutate({ id: p.id, decision: d })}
                    className="rounded border border-line px-2 py-1 hover:border-brand">
                    {d === "once" ? "Einmal" : d === "always" ? "Immer" : "Nie"}</button>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="mb-2 font-medium">Agenten-Läufe</h3>
        <div className="space-y-1">
          {runs?.map((r) => (
            <div key={r.id} className="flex items-center gap-3 rounded border border-line bg-card p-2 text-sm">
              <span className="font-mono text-xs text-muted">{r.issue_key}</span>
              <span>{r.agent}</span>
              <span className="text-xs text-muted">{r.phase}</span>
              <span className={ST_COLOR[r.status] || "text-muted"}>{r.status}</span>
              <span className="text-xs text-muted">{r.iterations}it · {r.output_tokens}tok{r.cost_usd ? ` · $${r.cost_usd.toFixed(4)}` : ""}</span>
              <div className="flex-1 truncate text-xs text-muted">{r.summary}</div>
            </div>
          ))}
          {runs?.length === 0 && <div className="text-sm text-muted">Noch keine Läufe.</div>}
        </div>
      </section>
    </div>
  );
}

function fmtDauer(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
