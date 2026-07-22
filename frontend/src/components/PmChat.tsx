import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, getToken, Project } from "../api";
import { formatTime } from "../lib/formatTime";

interface Msg { id?: number; role: string; author?: string; content: string; created_at?: string; }

export default function PmChat({ project }: { project: Project }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

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
        {messages.length === 0 && <div className="text-sm text-muted">Sag dem PM, was zu tun ist — er legt Tickets an und delegiert an Agenten.</div>}
      </div>
      <div className="flex gap-2 border-t border-line p-3">
        <input value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Nachricht an den PM…" className="flex-1 rounded border border-line bg-surface px-3 py-2" />
        <button onClick={send} className="rounded bg-brand px-4 py-2 text-white">Senden</button>
      </div>
    </div>
  );
}
