import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { tr } from "../i18n";
import { api, ApiError, getToken, Project } from "../api";
import { useAuth } from "../auth";
import { formatDateTime } from "../lib/formatTime";
import { Area, BUTTON_SMALL, Dialog, Errorrow, Listing, ListingEmpty, Tag } from "./ui";

/**
 * The person's own Claude CLI session in a project in CLI mode.
 *
 * The terminal is a real one: the backend passes the socket through to ttyd in the session
 * container, which serves the tmux session claude runs in. Closing the tab changes nothing
 * there; the session keeps running and keeps receiving the tickets that are released.
 */
interface QueueEntry {
  id: number; issue_key: string; release_id: number | null; summary: string; state: "waiting" | "delivered";
  delivery: "now" | "queue"; context: "keep" | "clear"; error: string;
}
interface ReleaseTicket { key: string; summary: string; agent_status: string; finished: boolean; }
interface ReleaseOut {
  id: number; name: string; state: "open" | "deploying" | "deployed" | "failed";
  summary: string; deployed_at: string | null; tickets: ReleaseTicket[];
}
interface SessionOut { status: string; error: string; container: string; queue: QueueEntry[]; }

// ttyd's commands: the first byte of every frame.
const INPUT = "0".charCodeAt(0);
const OUTPUT = "0";
const RESIZE = "1";

export default function CliSession({ project }: { project: Project }) {
  const qc = useQueryClient();
  const key = ["cli-session", project.id];
  const { data: sess } = useQuery({
    queryKey: key,
    queryFn: () => api.get<SessionOut>(`/projects/${project.id}/cli/session`),
    refetchInterval: 5000,
  });
  const [err, setErr] = useState("");
  const onError = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const start = useMutation({
    mutationFn: () => api.post<SessionOut>(`/projects/${project.id}/cli/session/start`),
    onSuccess: (d) => { setErr(""); qc.setQueryData(key, d); }, onError,
  });
  const stop = useMutation({
    mutationFn: () => api.post<SessionOut>(`/projects/${project.id}/cli/session/stop`),
    onSuccess: (d) => qc.setQueryData(key, d), onError,
  });
  const move = useMutation({
    mutationFn: ({ id, direction }: { id: number; direction: "up" | "down" }) =>
      api.post<SessionOut>(`/projects/${project.id}/cli/queue/${id}/move`, { direction }),
    onSuccess: (d) => qc.setQueryData(key, d), onError,
  });

  const [log, setLog] = useState<string | null>(null);
  const showLog = useMutation({
    mutationFn: () => api.get<{ log: string }>(`/projects/${project.id}/cli/logs`),
    onSuccess: (d) => setLog(d.log || tr("cli_session.log_empty")), onError,
  });
  // The image is shared by every session of the house: only an admin rebuilds it.
  const { user } = useAuth();
  const admin = user?.global_role === "admin";
  const { data: img } = useQuery({
    queryKey: ["cli-image"], enabled: admin, staleTime: 60_000,
    queryFn: () => api.get<{ version: string }>("/cli/image"),
  });
  const rebuild = useMutation({
    mutationFn: () => api.post<{ ok: boolean; version: string; log: string }>("/cli/image/build", { latest: true }),
    onSuccess: (d) => {
      qc.setQueryData(["cli-image"], d);
      setLog(d.ok ? tr("cli_session.image_built", { version: d.version }) : d.log);
    },
    onError,
  });

  const running = sess?.status === "running";
  const statusTag = {
    running: <Tag color="green">{tr("cli_session.running")}</Tag>,
    starting: <Tag color="yellow">{tr("cli_session.starting")}</Tag>,
    failed: <Tag color="red">{tr("cli_session.failed")}</Tag>,
  }[sess?.status ?? ""] ?? <Tag>{tr("cli_session.stopped")}</Tag>;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      {log !== null && (
        <Dialog title={tr("cli_session.log")} wide onClose={() => setLog(null)}>
          <pre className="whitespace-pre-wrap break-all font-mono text-xs text-ink">{log}</pre>
        </Dialog>
      )}
      <Area
        title={tr("cli_session.title")}
        subtitle={sess?.container}
        tools={<>
          {statusTag}
          {admin && img?.version && <span className="font-mono text-xs text-muted">{img.version}</span>}
          <div className="flex-1" />
          {admin && (
            <button className={BUTTON_SMALL.secondary} disabled={rebuild.isPending}
              title={tr("cli_session.update_hint")} onClick={() => rebuild.mutate()}>
              ⬆ {rebuild.isPending ? tr("cli_session.updating") : tr("cli_session.update")}
            </button>
          )}
          <button className={BUTTON_SMALL.secondary} onClick={() => showLog.mutate()}>
            {tr("cli_session.log")}
          </button>
          {running ? (<>
            <button className={BUTTON_SMALL.secondary} disabled={start.isPending}
              onClick={() => { stop.mutateAsync().then(() => start.mutate()); }}>
              ↻ {tr("cli_session.restart")}
            </button>
            <button className={BUTTON_SMALL.danger} disabled={stop.isPending}
              onClick={() => stop.mutate()}>⏹ {tr("cli_session.stop")}</button>
          </>) : (
            <button className={BUTTON_SMALL.primary} disabled={start.isPending}
              onClick={() => start.mutate()}>
              ▶ {start.isPending ? tr("cli_session.starting") : tr("cli_session.start")}
            </button>
          )}
        </>}
      >
        {err && <Errorrow text={err} />}
        {sess?.status === "failed" && sess.error && <Errorrow text={sess.error} />}
        {running
          ? <TerminalView projectId={project.id} />
          : <p className="text-sm text-muted">{tr("cli_session.not_running_hint")}</p>}
      </Area>

      <Area title={tr("cli_session.queue")} hint={tr("cli_session.queue_hint")}>
        <Listing>
          {(sess?.queue ?? []).length === 0 && <ListingEmpty>{tr("cli_session.queue_empty")}</ListingEmpty>}
          {(sess?.queue ?? []).map((q) => (
            <div key={q.id} className="flex items-center gap-2 bg-surface px-3 py-2 text-sm">
              <span className="font-mono text-xs text-muted">{q.release_id ? "🚀" : q.issue_key}</span>
              <span className="min-w-0 flex-1 truncate" title={q.summary}>{q.summary}</span>
              {q.state === "delivered"
                ? <Tag color="blue">{tr("cli_session.in_session")}</Tag>
                : q.delivery === "now"
                  ? <Tag color="yellow">{tr("cli_session.now")}</Tag>
                  : <>
                      <button className={BUTTON_SMALL.secondary} title={tr("cli_session.up")}
                        onClick={() => move.mutate({ id: q.id, direction: "up" })}>↑</button>
                      <button className={BUTTON_SMALL.secondary} title={tr("cli_session.down")}
                        onClick={() => move.mutate({ id: q.id, direction: "down" })}>↓</button>
                    </>}
              {q.context === "clear" && <Tag title={tr("cli_session.clear_hint")}>/clear</Tag>}
              {q.error && <Tag color="red" title={q.error}>!</Tag>}
            </div>
          ))}
        </Listing>
      </Area>

      <Releases project={project} />
    </div>
  );
}

/** Tickets collect in the open release; deploying it hands one job to my session. */
function Releases({ project }: { project: Project }) {
  const qc = useQueryClient();
  const key = ["releases", project.id];
  const { data: releases } = useQuery({
    queryKey: key, refetchInterval: 10000,
    queryFn: () => api.get<ReleaseOut[]>(`/projects/${project.id}/releases`),
  });
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState<ReleaseOut | null>(null);
  const refresh = () => {
    setErr("");
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["cli-session", project.id] });
  };
  const onError = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const deploy = useMutation({
    mutationFn: ({ id, force }: { id: number; force: boolean }) =>
      api.post(`/projects/${project.id}/releases/${id}/deploy`, { force }),
    onSuccess: () => { setConfirm(null); refresh(); }, onError,
  });
  const open = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/releases`), onSuccess: refresh, onError,
  });
  const drop = useMutation({
    mutationFn: ({ id, key: k }: { id: number; key: string }) =>
      api.del(`/projects/${project.id}/releases/${id}/issues/${k}`),
    onSuccess: refresh, onError,
  });

  const current = releases?.find((r) => r.state === "open");
  const past = (releases ?? []).filter((r) => r.state !== "open");
  const stateTag = (r: ReleaseOut) => ({
    deploying: <Tag color="yellow">{tr("releases.deploying")}</Tag>,
    deployed: <Tag color="green">{tr("releases.deployed")}</Tag>,
    failed: <Tag color="red">{tr("releases.failed")}</Tag>,
    open: <Tag color="blue">{tr("releases.open")}</Tag>,
  }[r.state]);
  const tryDeploy = (r: ReleaseOut) =>
    r.tickets.some((t) => !t.finished) ? setConfirm(r) : deploy.mutate({ id: r.id, force: false });

  return (
    <Area span="xl:col-span-2" title={tr("releases.title")}
      subtitle={current?.name}
      tools={<>
        <div className="flex-1" />
        {current ? (
          <button className={BUTTON_SMALL.primary}
            disabled={deploy.isPending || !current.tickets.some((t) => t.finished)}
            onClick={() => tryDeploy(current)}>🚀 {tr("releases.deploy", { name: current.name })}</button>
        ) : (
          <button className={BUTTON_SMALL.secondary} onClick={() => open.mutate()}>
            + {tr("releases.open_new")}
          </button>
        )}
      </>}
    >
      {err && <Errorrow text={err} />}
      {confirm && (
        <Dialog title={tr("releases.unfinished_title")} onClose={() => setConfirm(null)}
          foot={<>
            <button className={BUTTON_SMALL.secondary} onClick={() => setConfirm(null)}>{tr("common.cancel")}</button>
            <button className={BUTTON_SMALL.primary}
              onClick={() => deploy.mutate({ id: confirm.id, force: true })}>{tr("releases.deploy_finished_only")}</button>
          </>}>
          <p className="text-sm text-ink">{tr("releases.unfinished_text")}</p>
          <ul className="mt-2 list-disc pl-5 text-sm text-muted">
            {confirm.tickets.filter((t) => !t.finished).map((t) => <li key={t.key}>{t.key}: {t.summary}</li>)}
          </ul>
        </Dialog>
      )}
      <Listing>
        {!current?.tickets.length && <ListingEmpty>{tr("releases.empty")}</ListingEmpty>}
        {current?.tickets.map((t) => (
          <div key={t.key} className="flex items-center gap-2 bg-surface px-3 py-2 text-sm">
            <span className="font-mono text-xs text-muted">{t.key}</span>
            <span className="min-w-0 flex-1 truncate" title={t.summary}>{t.summary}</span>
            <Tag color={t.finished ? "green" : "yellow"}>{t.agent_status}</Tag>
            <button className={BUTTON_SMALL.secondary} title={tr("releases.drop")}
              onClick={() => drop.mutate({ id: current.id, key: t.key })}>✕</button>
          </div>
        ))}
      </Listing>
      {past.length > 0 && (
        <Listing>
          {past.map((r) => (
            <div key={r.id} className="flex items-center gap-2 bg-surface px-3 py-2 text-sm">
              <span className="w-24 shrink-0 font-medium">{r.name}</span>
              {stateTag(r)}
              <span className="min-w-0 flex-1 truncate text-muted" title={r.summary}>
                {r.tickets.map((t) => t.key).join(", ")}{r.summary ? ` · ${r.summary}` : ""}
              </span>
              {r.deployed_at && <span className="text-xs text-muted">{formatDateTime(r.deployed_at)}</span>}
              {r.state === "failed" && (
                <button className={BUTTON_SMALL.secondary} onClick={() => tryDeploy(r)}>{tr("releases.retry")}</button>
              )}
            </div>
          ))}
        </Listing>
      )}
    </Area>
  );
}

function TerminalView({ projectId }: { projectId: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [closed, setClosed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!box.current) return;
    const term = new Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 5000,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: { background: "#0b0f14" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current);
    fit.fit();
    setClosed(false);

    const enc = new TextEncoder();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}/api/projects/${projectId}/cli/ws?token=${getToken()}`, ["tty"]);
    ws.binaryType = "arraybuffer";
    const resize = () => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(enc.encode(RESIZE + JSON.stringify({ columns: term.cols, rows: term.rows })));
    };
    ws.onopen = () => {
      ws.send(enc.encode(JSON.stringify({ AuthToken: "", columns: term.cols, rows: term.rows })));
      term.focus();
    };
    ws.onmessage = (e) => {
      const raw = typeof e.data === "string" ? enc.encode(e.data) : new Uint8Array(e.data);
      if (String.fromCharCode(raw[0]) === OUTPUT) term.write(raw.subarray(1));
    };
    ws.onclose = () => setClosed(true);
    const input = term.onData((d) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const bytes = enc.encode(d);
      const frame = new Uint8Array(bytes.length + 1);
      frame[0] = INPUT;
      frame.set(bytes, 1);
      ws.send(frame);
    });
    const binary = term.onBinary((d) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const frame = new Uint8Array(d.length + 1);
      frame[0] = INPUT;
      for (let i = 0; i < d.length; i++) frame[i + 1] = d.charCodeAt(i) & 255;
      ws.send(frame);
    });
    const onSize = term.onResize(resize);
    const observer = new ResizeObserver(() => { try { fit.fit(); } catch { /* detached */ } });
    observer.observe(box.current);
    return () => {
      observer.disconnect();
      input.dispose(); binary.dispose(); onSize.dispose();
      ws.close();
      term.dispose();
    };
  }, [projectId, attempt]);

  return (
    <div className="space-y-2">
      <div ref={box} className="h-[70vh] w-full overflow-hidden rounded border border-line bg-[#0b0f14] p-1" />
      {closed && (
        <div className="flex items-center gap-2 text-sm text-muted">
          {tr("cli_session.disconnected")}
          <button className={BUTTON_SMALL.secondary} onClick={() => setAttempt((n) => n + 1)}>
            {tr("cli_session.reconnect")}
          </button>
        </div>
      )}
    </div>
  );
}
