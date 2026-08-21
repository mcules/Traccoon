import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Area, Tag, Errorrow, ICON, IconButton, Listing, ListingEmpty, ListenLine, Rowbutton, BUTTON_SMALL, BUTTON_TEXT, BUTTON} from "./ui";

interface Svc { service: string; container: string; status: string }
interface Env {
  kind: "ticket" | "branch"; ref: string | number; label: string;
  container: string; status: string; url: string | null; port: number | null;
  error: string | null; services: Svc[];
}

const BADGE: Record<string, "green" | "yellow" | "red"> = {
  running: "green", starting: "yellow", error: "red",
};

/** Overview of all test environments of the project (ticket plus branch) with logs and stop (TRA-18). */
export default function TestenvsPanel({ project }: { project: Project }) {
  const qc = useQueryClient();
  const can = project.my_role !== "viewer";
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
  const [open, setOpen] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["testenvs", project.id] });
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const start = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/branch-testenvs`, { branch }),
    onSuccess: () => { setErr(""); inv(); }, onError: error,
  });
  const stop = useMutation({
    mutationFn: (e: Env) => e.kind === "ticket"
      ? api.post(`/issues/${e.ref}/testenv/stop`)
      : api.post(`/projects/${project.id}/branch-testenvs/${e.ref}/stop`),
    onSuccess: inv, onError: error,
  });
  const fetchLogs = async (e: Env, service?: string) => {
    try {
      const r = await api.post<{ log: string }>(
        `/projects/${project.id}/testenvs/${e.container}/logs`, { service, tail: 500 });
      setLogs({ ...logs, [e.container]: r.log || tr("testenvs.no_output") });
    } catch (ex) { error(ex); }
  };

  if (project.testenv_enabled === false) {
    return <div className="text-sm text-muted">
      {tr("testenvs.test_environments_switched_off")}
    </div>;
  }

  return (
    <div className="space-y-4">
      <Errorrow text={err} />

      <Area title={tr("testenvs_panel.running_environments")}>
        <Listing>
          {envs?.map((e) => (
            <ListenLine key={e.container}>
              <div className="flex flex-wrap items-center gap-2">
                <Tag color={BADGE[e.status] || "neutral"}>{e.status || "—"}</Tag>
                <Tag>{e.kind === "ticket" ? "Ticket" : "Branch"}</Tag>
                <span className="min-w-0 flex-1 truncate text-ink">{e.label}</span>
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer"
                    className={BUTTON_TEXT.secondary}>{tr("testenvs_panel.open")}</a>
                )}
                <Rowbutton onClick={() => setOpen(open === e.container ? null : e.container)}>
                  {open === e.container ? "▾ Details" : "▸ Details"}
                </Rowbutton>
                {can && (
                  <IconButton icon="⏹" title={tr("testenvs_panel.stop")} danger onClick={() => stop.mutate(e)} />
                )}
              </div>
              {open === e.container && (
                <div className="mt-2 space-y-2 border-t border-line pt-2.5">
                  <div className="text-xs text-muted">
                    {tr("testenvs.container_prefix")} <span className="font-mono">{e.container}</span>
                    {e.port ? ` · Port ${e.port}` : ""}
                  </div>
                  {e.error && (
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs text-red-400">
                      {e.error}</pre>
                  )}
                  <div className="flex flex-wrap gap-1">
                    {e.services.map((s) => (
                      <Tag key={s.container}>{s.service} · {s.status}</Tag>
                    ))}
                    {e.services.length === 0 && (
                      <span className="text-xs text-muted">{tr("testenvs_panel.no_container_visible_on_the_runner")}</span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <button onClick={() => fetchLogs(e)}
                      className={BUTTON_SMALL.secondary}>
                      {tr("testenvs.logs_all")}</button>
                    {e.services.map((s) => (
                      <button key={s.container} onClick={() => fetchLogs(e, s.service)}
                        className={BUTTON_SMALL.secondary}>
                        Logs {s.service}</button>
                    ))}
                  </div>
                  {logs[e.container] && (
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 font-mono text-xs">
                      {logs[e.container]}</pre>
                  )}
                </div>
              )}
            </ListenLine>
          ))}
          {envs?.length === 0 && <ListingEmpty>{tr("testenvs_panel.no_running_test_environment")}</ListingEmpty>}
        </Listing>
      </Area>

      {can && (
        <Area title={tr("testenvs_panel.start_branch_test_environment")} hint={tr("testenvs.builds_chosen_branch_own")}>
          <div className="flex flex-wrap items-center gap-2">
            <select value={branch} onChange={(e) => setBranch(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("testenvs.choose_branch")}</option>
              {branches?.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <button onClick={() => branch && start.mutate()} disabled={!branch || start.isPending}
              className={BUTTON.primary}>
              {start.isPending ? "startet…" : "Starten"}</button>
          </div>
        </Area>
      )}
    </div>
  );
}
