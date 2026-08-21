import { useEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { api, getToken, Project } from "../api";
import { useAuth } from "../auth";
import { formatTime } from "../lib/formatTime";
import { BUTTON } from "./ui";

interface Msg { id?: number; role: string; author?: string; content: string; created_at?: string; }

export default function PmChat({ project }: { project: Project }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // The presentation is a user setting (profile) and applies across projects (TRA-21).
  const { user } = useAuth();
  const cli = user?.pm_chat_style === "cli";

  useQuery({
    queryKey: ["pm-history", project.id],
    queryFn: async () => {
      const h = await api.get<Msg[]>(`/projects/${project.id}/messages?token=${getToken()}`);
      setMessages(h);
      return h;
    },
  });

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/projects/${project.id}/ws?token=${getToken()}`);
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(e.data);
        if (m.type === "pm_chat") setMessages((prev) => [...prev, { role: m.role, content: m.content, created_at: m.created_at }]);
      } catch { /* ignore */ }
    };
    wsRef.current = ws;
    return () => ws.close();
  }, [project.id]);

  useEffect(() => { boxRef.current?.scrollTo(0, boxRef.current.scrollHeight); }, [messages]);

  function send() {
    if (!text.trim() || wsRef.current?.readyState !== WebSocket.OPEN) return;
    setMessages((prev) => [...prev, { role: "user", content: text, created_at: new Date().toISOString() }]);
    wsRef.current.send(JSON.stringify({ type: "chat", content: text }));
    setText("");
  }

  return cli
    ? <CliChat messages={messages} text={text} setText={setText} send={send} boxRef={boxRef} project={project} />
    : <BubbleChat messages={messages} text={text} setText={setText} send={send} boxRef={boxRef} />;
}

type ViewProps = {
  messages: Msg[]; text: string; setText: (v: string) => void; send: () => void;
  boxRef: React.RefObject<HTMLDivElement>;
};

/** Bisherige Darstellung: Sprechblasen im App-Theme. */
function BubbleChat({ messages, text, setText, send, boxRef }: ViewProps) {
  return (
    <div className="flex h-[70vh] flex-col rounded-lg border border-line bg-card">
      <div ref={boxRef} className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.map((m, i) => (
          <div key={i} className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
            m.role === "user" ? "ml-auto bg-brand/20" : m.role === "system" ? "bg-red-500/10 text-red-300" : "bg-surface"}`}>
            <div className="mb-1 flex items-center gap-2 text-xs text-muted">
              <span>{m.role === "user" ? "Du" : m.role === "pm" ? "🤖 PM" : "System"}</span>
              {m.created_at && <span className="ml-auto">{formatTime(m.created_at)}</span>}
            </div>
            <div className="whitespace-pre-wrap">{m.content}</div>
          </div>
        ))}
        {messages.length === 0 && <div className="text-sm text-muted">{tr("pm_chat.sag_dem_pm_was_zu_tun_ist_er_legt_ticket")}</div>}
      </div>
      <div className="flex gap-2 border-t border-line p-3">
        <input value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={tr("pm_chat.nachricht_an_den_pm")} className="flex-1 rounded border border-line bg-surface px-3 py-2" />
        <button onClick={send} className={BUTTON.haupt}>{tr("pm_chat.senden")}</button>
      </div>
    </div>
  );
}

// Terminal palette (fixed, independent of the app theme: a terminal is always dark).
const T = {
  bg: "#1b1a17", border: "#33302b", ink: "#e6e2db", dim: "#8b857a",
  accent: "#d97757", user: "#87b7c9", err: "#e0685f",
};

/** Terminal look like the Claude Code CLI in dark mode. */
function CliChat({ messages, text, setText, send, boxRef, project }: ViewProps & { project: Project }) {
  return (
    <div className="flex h-[70vh] flex-col overflow-hidden rounded-lg border font-mono text-[13px] leading-relaxed"
      style={{ background: T.bg, borderColor: T.border, color: T.ink }}>
      <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs"
        style={{ borderColor: T.border, color: T.dim }}>
        <span style={{ color: T.accent }}>✻</span>
        <span>projektmanager — {project.key}</span>
      </div>

      <div ref={boxRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.length === 0 && (
          <div style={{ color: T.dim }}>
            <div><span style={{ color: T.accent }}>✻</span> {tr("pm_chat.willkommen_beim_projektmanager")}</div>
            <div className="mt-1">{tr("pm_chat.sag_was_zu_tun_ist_er_legt_tickets_an_un")}</div>
          </div>
        )}
        {messages.map((m, i) => {
          if (m.role === "user") {
            return (
              <div key={i} className="whitespace-pre-wrap break-words">
                <span style={{ color: T.dim }}>&gt; </span>
                <span style={{ color: T.user }}>{m.content}</span>
                {m.created_at && (
                  <span className="ml-2 text-[11px]" style={{ color: T.dim }}>{formatTime(m.created_at)}</span>
                )}
              </div>
            );
          }
          const system = m.role === "system";
          return (
            <div key={i} className="flex gap-2">
              <span style={{ color: system ? T.err : T.accent }}>{system ? "✗" : "⏺"}</span>
              <div className="min-w-0 flex-1">
                <div className="whitespace-pre-wrap break-words" style={system ? { color: T.err } : undefined}>
                  {m.content}
                </div>
                {m.created_at && (
                  <div className="mt-0.5 text-[11px]" style={{ color: T.dim }}>{formatTime(m.created_at)}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2 border-t px-3 py-2" style={{ borderColor: T.border }}>
        <span style={{ color: T.accent }}>&gt;</span>
        <input value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={tr("pm_chat.nachricht_an_den_pm")}
          className="flex-1 bg-transparent font-mono text-[13px] outline-none placeholder:opacity-50"
          style={{ color: T.ink }} />
        <span className="text-[11px]" style={{ color: T.dim }}>⏎ senden</span>
      </div>
    </div>
  );
}
