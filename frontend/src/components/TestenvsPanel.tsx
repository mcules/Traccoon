import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Bereich, Etikett, Fehlerzeile, ICON, IconKnopf, Liste, ListeLeer, ListenZeile, Zeilenknopf,
} from "./ui";

interface Svc { service: string; container: string; status: string }
interface Env {
  kind: "ticket" | "branch"; ref: string | number; label: string;
  container: string; status: string; url: string | null; port: number | null;
  error: string | null; services: Svc[];
}

const BADGE: Record<string, "gruen" | "gelb" | "rot"> = {
  running: "gruen", starting: "gelb", error: "rot",
};

/** Overview of all test environments of the project (ticket plus branch) with logs and stop (ABC-18). */
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
      setLogs({ ...logs, [e.container]: r.log || tr("testenvs.keine_ausgabe") });
    } catch (ex) { fehler(ex); }
  };

  if (project.testenv_enabled === false) {
    return <div className="text-sm text-muted">
      {tr("testenvs.ausgeschaltet")}
    </div>;
  }

  return (
    <div className="space-y-4">
      <Fehlerzeile text={err} />

      <Bereich titel={tr("testenvs_panel.laufende_umgebungen")}>
        <Liste>
          {envs?.map((e) => (
            <ListenZeile key={e.container}>
              <div className="flex flex-wrap items-center gap-2">
                <Etikett farbe={BADGE[e.status] || "neutral"}>{e.status || "—"}</Etikett>
                <Etikett>{e.kind === "ticket" ? "Ticket" : "Branch"}</Etikett>
                <span className="min-w-0 flex-1 truncate text-ink">{e.label}</span>
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer"
                    className="shrink-0 text-brand hover:underline">{tr("testenvs_panel.oeffnen")}</a>
                )}
                <Zeilenknopf onClick={() => setOffen(offen === e.container ? null : e.container)}>
                  {offen === e.container ? "▾ Details" : "▸ Details"}
                </Zeilenknopf>
                {kann && (
                  <IconKnopf icon="⏹" titel={tr("testenvs_panel.stoppen")} gefahr onClick={() => stop.mutate(e)} />
                )}
              </div>
              {offen === e.container && (
                <div className="mt-2 space-y-2 border-t border-line pt-2.5">
                  <div className="text-xs text-muted">
                    {tr("testenvs.container_praefix")} <span className="font-mono">{e.container}</span>
                    {e.port ? ` · Port ${e.port}` : ""}
                  </div>
                  {e.error && (
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs text-red-400">
                      {e.error}</pre>
                  )}
                  <div className="flex flex-wrap gap-1">
                    {e.services.map((s) => (
                      <Etikett key={s.container}>{s.service} · {s.status}</Etikett>
                    ))}
                    {e.services.length === 0 && (
                      <span className="text-xs text-muted">{tr("testenvs_panel.kein_container_beim_runner_sichtbar")}</span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <button onClick={() => holeLogs(e)}
                      className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                      {tr("testenvs.logs_alle")}</button>
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
            </ListenZeile>
          ))}
          {envs?.length === 0 && <ListeLeer>{tr("testenvs_panel.keine_laufende_testumgebung")}</ListeLeer>}
        </Liste>
      </Bereich>

      {kann && (
        <Bereich titel={tr("testenvs_panel.branch_testumgebung_starten")} hinweis={tr("testenvs.baut_branch")}>
          <div className="flex flex-wrap items-center gap-2">
            <select value={branch} onChange={(e) => setBranch(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("testenvs.branch_waehlen")}</option>
              {branches?.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <button onClick={() => branch && start.mutate()} disabled={!branch || start.isPending}
              className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-40">
              {start.isPending ? "startet…" : "Starten"}</button>
          </div>
        </Bereich>
      )}
    </div>
  );
}
