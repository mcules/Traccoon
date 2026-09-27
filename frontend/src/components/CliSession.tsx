import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { tr } from "../i18n";
import { api, ApiError, getToken, Project } from "../api";
import { Area, BUTTON_SMALL, Errorrow, Listing, ListingEmpty, Tag } from "./ui";

/**
 * The person's own Claude CLI session in a project in CLI mode.
 *
 * The terminal is a real one: the backend passes the socket through to ttyd in the session
 * container, which serves the tmux session claude runs in. Closing the tab changes nothing
 * there; the session keeps running and keeps receiving the tickets that are released.
 */
interface QueueEntry {
  id: number; issue_key: string; summary: string; state: "waiting" | "delivered";
  delivery: "now" | "queue"; context: "keep" | "clear"; error: string;
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

  const running = sess?.status === "running";
  const statusTag = {
    running: <Tag color="green">{tr("cli_session.running")}</Tag>,
    starting: <Tag color="yellow">{tr("cli_session.starting")}</Tag>,
    failed: <Tag color="red">{tr("cli_session.failed")}</Tag>,
  }[sess?.status ?? ""] ?? <Tag>{tr("cli_session.stopped")}</Tag>;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <Area
        title={tr("cli_session.title")}
        subtitle={sess?.container}
        tools={<>
          {statusTag}
          <div className="flex-1" />
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
              <span className="font-mono text-xs text-muted">{q.issue_key}</span>
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
    </div>
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
