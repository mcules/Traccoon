import { useEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import Markdown from "./Markdown";

interface ChatMsg {
  id: number; text: string; status: string; result: string; error: string;
  pending_tool: string | null; created_at: string;
}

export default function AssistantChat() {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [err, setErr] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const { data = [] } = useQuery({
    queryKey: ["assistant-chat"], queryFn: () => api.get<ChatMsg[]>("/assistant/chat"),
    refetchInterval: 3000,
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["assistant-chat"] });

  const send = useMutation({
    mutationFn: (text: string) => api.post("/assistant/chat", { text }),
    onSuccess: () => { setInput(""); inv(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: string }) =>
      api.post(`/assistant/chat/${id}/decide`, { decision }),
    onSuccess: inv, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [data.length, data.map((m) => m.status).join()]);

  return (
    <div className="flex h-[calc(100vh-16rem)] flex-col">
      <p className="mb-2 text-sm text-muted">
        Sag dem Assistenten, was er tun soll — er bedient Traccoon (in deinen Rechten) und deine
        Dienste. Bei heiklen Aktionen fragt er nach (hier oder per Telegram).
      </p>
      {err && <div className="mb-2 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-line bg-surface p-3">
        {data.length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            Noch nichts. Probier: „Welche Projekte hab ich?" oder „Kosten von ABC-23?"
          </div>
        )}
        {data.map((m) => (
          <div key={m.id} className="space-y-2">
            <div className="flex justify-end">
              <div className="max-w-[80%] rounded-lg rounded-br-sm bg-brand px-3 py-2 text-sm text-white whitespace-pre-wrap">
                {m.text}
              </div>
            </div>
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-lg rounded-bl-sm border border-line bg-card px-3 py-2 text-sm text-ink">
                {m.status === "awaiting" ? (
                  <div className="space-y-2">
                    <div className="text-amber-400">🔐 Freigabe nötig für <code>{m.pending_tool}</code></div>
                    <div className="flex gap-1">
                      {(["once", "always", "never"] as const).map((d) => (
                        <button key={d} onClick={() => decide.mutate({ id: m.id, decision: d })}
                          className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
                          {d === "once" ? "Einmal" : d === "always" ? "Immer" : "Nie"}</button>
                      ))}
                    </div>
                  </div>
                ) : ["new", "approved", "running"].includes(m.status) ? (
                  <span className="text-muted">🔄 denkt nach…</span>
                ) : m.error ? (
                  <span className="text-red-400 whitespace-pre-wrap">{m.error}</span>
                ) : (
                  <Markdown text={m.result || "(keine Antwort)"} />
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form className="mt-3 flex gap-2"
        onSubmit={(e) => { e.preventDefault(); setErr(""); if (input.trim()) send.mutate(input.trim()); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={tr("assistant_chat.nachricht_an_den_assistenten")}
          className="flex-1 rounded border border-line bg-card px-3 py-2 text-ink outline-none" />
        <button type="submit" disabled={send.isPending || !input.trim()}
          className="rounded bg-brand px-4 py-2 text-sm text-white disabled:opacity-50">{tr("assistant_chat.senden")}</button>
      </form>
    </div>
  );
}
