import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

interface Svc { service: string; container: string; status: string }
interface Env {
  kind: "ticket" | "branch"; ref: string | number; label: string;
  container: string; status: string; url: string | null; port: number | null;
  error: string | null; services: Svc[];
}

const BADGE: Record<string, string> = {
  running: "bg-green-500/20 text-green-400",
  starting: "bg-yellow-500/20 text-yellow-400",
  error: "bg-red-500/20 text-red-400",
};

/** Übersicht aller Testumgebungen des Projekts (Ticket + Branch) mit Logs und Stop (TRA-18). */
export default function TestenvsPanel({ project }: { project: Project }) {
  const qc = useQueryClient();
  const kann = project.my_role !== "viewer";
  const { data: envs } = useQuery({
    queryKey: ["testenvs", project.id],
    queryFn: () => api.get<Env[]>(`/projects/${project.id}/testenvs`),
    refetchInterval: 5000,
  });
  const { data: branches } = useQuery({
    queryKey: ["branches", project.id],
    queryFn: () => api.get<string[]>(`/projects/${project.id}/branches`),
  });

  const [branch, setBranch] = useState("");
  const [offen, setOffen] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["testenvs", project.id] });
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const start = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/branch-testenvs`, { branch }),
    onSuccess: () => { setErr(""); inv(); }, onError: fehler,
  });
  const stop = useMutation({
    mutationFn: (e: Env) => e.kind === "ticket"
      ? api.post(`/issues/${e.ref}/testenv/stop`)
      : api.post(`/projects/${project.id}/branch-testenvs/${e.ref}/stop`),
    onSuccess: inv, onError: fehler,
  });
  const holeLogs = async (e: Env, service?: string) => {
    try {
      const r = await api.post<{ log: string }>(
        `/projects/${project.id}/testenvs/${e.container}/logs`, { service, tail: 500 });
      setLogs({ ...logs, [e.container]: r.log || "(keine Ausgabe)" });
    } catch (ex) { fehler(ex); }
  };

  if (project.testenv_enabled === false) {
    return <div className="text-sm text-muted">
      Testumgebungen sind für dieses Projekt ausgeschaltet (Einstellungen → Testumgebung).
    </div>;
  }

  return (
    <div className="space-y-4">
      {err && <div className="text-sm text-red-400">{err}</div>}

      <section>
        <h3 className="mb-2 font-medium">Laufende Umgebungen</h3>
        <div className="space-y-2">
          {envs?.map((e) => (
            <div key={e.container} className="rounded border border-line bg-card text-sm">
              <div className="flex flex-wrap items-center gap-2 p-2.5">
                <span className={`rounded px-1.5 py-0.5 text-xs ${BADGE[e.status] || "bg-surface text-muted"}`}>
                  {e.status || "—"}
                </span>
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
                  {e.kind === "ticket" ? "Ticket" : "Branch"}
                </span>
                <span className="flex-1 truncate">{e.label}</span>
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer"
                    className="text-brand hover:underline">🖥 öffnen</a>
                )}
                <button onClick={() => { setOffen(offen === e.container ? null : e.container); }}
                  className="text-muted hover:text-ink">
                  {offen === e.container ? "▾ Details" : "▸ Details"}</button>
                {kann && (
                  <button onClick={() => stop.mutate(e)}
                    className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-red-400">
                    Stoppen</button>
                )}
              </div>
              {offen === e.container && (
                <div className="space-y-2 border-t border-line p-2.5">
                  <div className="text-xs text-muted">
                    Container-Präfix <span className="font-mono">{e.container}</span>
                    {e.port ? ` · Port ${e.port}` : ""}
                  </div>
                  {e.error && (
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs text-red-400">
                      {e.error}</pre>
                  )}
                  <div className="flex flex-wrap gap-1">
                    {e.services.map((s) => (
                      <span key={s.container} className="rounded bg-surface px-1.5 py-0.5 text-xs">
                        {s.service} · <span className="text-muted">{s.status}</span>
                      </span>
                    ))}
                    {e.services.length === 0 && (
                      <span className="text-xs text-muted">Kein Container beim Runner sichtbar.</span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <button onClick={() => holeLogs(e)}
                      className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                      Logs (alle)</button>
                    {e.services.map((s) => (
                      <button key={s.container} onClick={() => holeLogs(e, s.service)}
                        className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                        Logs {s.service}</button>
                    ))}
                  </div>
                  {logs[e.container] && (
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 font-mono text-xs">
                      {logs[e.container]}</pre>
                  )}
                </div>
              )}
            </div>
          ))}
          {envs?.length === 0 && <div className="text-xs text-muted">Keine laufende Testumgebung.</div>}
        </div>
      </section>

      {kann && (
        <section className="rounded-lg border border-line bg-card p-3">
          <h3 className="mb-1 font-medium">Branch-Testumgebung starten</h3>
          <p className="mb-2 text-xs text-muted">
            Baut den gewählten Branch in einer eigenen, wegwerfbaren Umgebung — unabhängig
            von einem Ticket.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select value={branch} onChange={(e) => setBranch(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">— Branch wählen —</option>
              {branches?.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <button onClick={() => branch && start.mutate()} disabled={!branch || start.isPending}
              className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-40">
              {start.isPending ? "startet…" : "Starten"}</button>
          </div>
        </section>
      )}
    </div>
  );
}
