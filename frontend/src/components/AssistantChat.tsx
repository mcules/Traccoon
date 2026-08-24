import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import Markdown from "./Markdown";
import { BUTTON_SMALL, BUTTON} from "./ui";

interface ChatMsg {
  id: number; text: string; status: string; result: string; error: string;
  pending_tool: string | null; created_at: string; session_id: number | null;
}
type Page = { messages: ChatMsg[]; more: boolean };

interface Session {
  id: number; agent: string; title: string; created_at: string;
  last_message_at: string | null; closed_at: string | null;
  message_count: number; running: boolean;
}

/**
 * Which conversation was open here last. Deliberately in the browser and not on the server:
 * the pointer on the server belongs to the channel `web` and follows the sending, this here
 * only decides what is shown on opening — and it may differ per device without either being
 * wrong.
 */
const LAST_SESSION = "traccoon.assistant.session";

const RUNNING = ["new", "approved", "running", "awaiting"];

/**
 * The conversation with the assistant.
 *
 * Two things used to be wrong with it. The last fifty messages came at once, and the view
 * then scrolled through all of them, visibly and animated, down to the current one on every
 * opening: one watched a year of one's own chat go by before being allowed to type. And
 * nothing ever left the window, because there was no way to put a finished conversation
 * away.
 *
 * So: the newest twenty, standing at the bottom without any travel, older ones on request,
 * and an archive for what is done. Archived, not deleted, because the assistant reads the
 * conversation as context and a message that vanishes takes the sense of the answer that
 * followed it with it.
 */
export default function AssistantChat() {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [err, setErr] = useState("");
  const [showArchive, setShowArchive] = useState(false);
  const [sessionId, setSessionId] = useState<number | null>(() => {
    const stored = Number(localStorage.getItem(LAST_SESSION) || 0);
    return stored > 0 ? stored : null;
  });
  const [showClosed, setShowClosed] = useState(false);
  // Older pages, fetched on demand. They stand before the live page and do not refresh
  // themselves: what is past does not change.
  const [older, setOlder] = useState<ChatMsg[]>([]);
  const [stillMore, setStillMore] = useState(false);
  const listingRef = useRef<HTMLDivElement>(null);
  const firstImage = useRef(true);
  // Height before older messages were prepended, so that the view can stay where it stood.
  const heightBefore = useRef<number | null>(null);

  // The switcher is drawn from this. `running` says where something is still going on, so
  // that one does not switch away from the answer one is waiting for without noticing.
  const { data: sessions } = useQuery({
    queryKey: ["assistant-sessions", showClosed],
    queryFn: () => api.get<Session[]>(`/assistant/sessions${showClosed ? "?closed=1" : ""}`),
    refetchInterval: 5000,
  });
  const { data } = useQuery({
    queryKey: ["assistant-chat", showArchive, sessionId],
    queryFn: () => api.get<Page>(
      `/assistant/chat?limit=20${showArchive ? "&archive=1" : ""}` +
      (sessionId ? `&session_id=${sessionId}` : "")),
    refetchInterval: showArchive ? false : 3000,
  });
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["assistant-chat"] });
    qc.invalidateQueries({ queryKey: ["assistant-sessions"] });
  };
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const messages = [...older, ...(data?.messages || [])];
  const more = older.length ? stillMore : !!data?.more;

  // Switching between conversation and archive starts over; the pages of the one have
  // nothing to do with the other.
  useEffect(() => {
    setOlder([]); setStillMore(false); firstImage.current = true;
  }, [showArchive, sessionId]);

  // The conversation a message was answered in is remembered locally, so a reload lands
  // where one left off.
  useEffect(() => {
    if (sessionId) localStorage.setItem(LAST_SESSION, String(sessionId));
    else localStorage.removeItem(LAST_SESSION);
  }, [sessionId]);

  // A conversation that is gone (deleted by a job) must not leave the view stuck on a number
  // that answers nothing: back to "no particular one", which shows everything.
  useEffect(() => {
    if (sessionId && sessions && !showClosed && !sessions.some((s) => s.id === sessionId)) {
      api.get<Session[]>("/assistant/sessions?closed=1")
        .then((closed) => { if (!closed.some((s) => s.id === sessionId)) setSessionId(null); })
        .catch(() => undefined);
    }
  }, [sessions, sessionId, showClosed]);

  const send = useMutation({
    mutationFn: (text: string) => api.post<ChatMsg>("/assistant/chat",
      { text, ...(sessionId ? { session_id: sessionId } : {}) }),
    // Without a conversation of its own the server picks one (the pointer, or a fresh one).
    // Which one that was comes back with the message, so the view follows along instead of
    // staying on "everything".
    onSuccess: (message) => { setInput(""); setSessionId(message.session_id); inv(); },
    onError: error,
  });
  const newSession = useMutation({
    mutationFn: () => api.post<Session>("/assistant/sessions", {}),
    onSuccess: (s) => { setShowClosed(false); setSessionId(s.id); inv(); },
    onError: error,
  });
  const closeSession = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/sessions/${id}/close`),
    onSuccess: () => { setSessionId(null); inv(); },
    onError: error,
  });
  const reopenSession = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/sessions/${id}/reopen`),
    onSuccess: () => { setShowClosed(false); inv(); },
    onError: error,
  });
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) =>
      api.patch(`/assistant/sessions/${id}`, { title }),
    onSuccess: inv, onError: error,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: string }) =>
      api.post(`/assistant/chat/${id}/decide`, { decision }),
    onSuccess: inv, onError: error,
  });
  const archive = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/chat/${id}/${showArchive ? "unarchive" : "archive"}`),
    // The id comes back as the second argument, so the loaded older pages can drop that one
    // row instead of being thrown away and reloaded.
    onSuccess: (_answer, id) => { setOlder((v) => v.filter((m) => m.id !== id)); inv(); },
    onError: error,
  });
  const allArchive = useMutation({
    mutationFn: () => api.post("/assistant/chat/archive-all"),
    onSuccess: () => { setOlder([]); setStillMore(false); firstImage.current = true; inv(); },
    onError: error,
  });

  async function loadOlder() {
    const oldest = messages[0]?.id;
    if (!oldest) return;
    heightBefore.current = listingRef.current?.scrollHeight ?? null;
    try {
      const page = await api.get<Page>(
        `/assistant/chat?limit=20&before=${oldest}${showArchive ? "&archive=1" : ""}` +
        (sessionId ? `&session_id=${sessionId}` : ""));
      setOlder((v) => [...page.messages, ...v]);
      setStillMore(page.more);
    } catch (e) { error(e); }
  }

  const current = (sessions || []).find((s) => s.id === sessionId) || null;
  const lastId = messages[messages.length - 1]?.id;
  const states = messages.map((m) => m.status).join();

  useLayoutEffect(() => {
    const el = listingRef.current;
    if (!el) return;
    // Older ones were prepended: keep the reading position instead of jumping.
    if (heightBefore.current !== null) {
      el.scrollTop += el.scrollHeight - heightBefore.current;
      heightBefore.current = null;
      return;
    }
    if (firstImage.current) {
      // Standing at the bottom, not travelling there: an animation over fifty messages is
      // exactly the scrolling nobody asked for.
      el.scrollTop = el.scrollHeight;
      firstImage.current = false;
      return;
    }
    // Later only when one is standing down there anyway; otherwise reading further up
    // would be torn away by every answer.
    const belowNear = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (belowNear) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [lastId, states, older.length]);

  return (
    <div className="flex h-[calc(100vh-16rem)] flex-col">
      {/* The switcher. A conversation is picked here, a new one begun, the current one put
          away — deleting is deliberately not among them: that is a workflow action, so that
          clearing out can be scheduled instead of clicked. */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="session-picker">{tr("assistant_chat.sessions")}</label>
        <select id="session-picker" value={sessionId ?? ""}
          onChange={(e) => setSessionId(e.target.value ? Number(e.target.value) : null)}
          className="max-w-[18rem] flex-1 rounded border border-line bg-card px-2 py-1 text-sm text-ink outline-none">
          <option value="">{tr("assistant_chat.sessions")}</option>
          {(sessions || []).map((s) => (
            <option key={s.id} value={s.id}>
              {(s.running ? "🔄 " : "") + (s.title || tr("assistant_chat.untitled"))
               + (s.agent !== "assistent" ? ` [${s.agent}]` : "")}
            </option>
          ))}
        </select>
        <button onClick={() => newSession.mutate()} disabled={newSession.isPending}
          title={tr("assistant_chat.new_conversation")} className={BUTTON_SMALL.secondary}>+</button>
        {current && !current.closed_at && (
          <button onClick={() => closeSession.mutate(current.id)} disabled={closeSession.isPending}
            title={tr("assistant_chat.close_conversation")}
            className={BUTTON_SMALL.secondary}>×</button>
        )}
        {current?.closed_at && (
          <button onClick={() => reopenSession.mutate(current.id)} disabled={reopenSession.isPending}
            className={BUTTON_SMALL.secondary}>{tr("assistant_chat.reopen")}</button>
        )}
        {current && (
          <button
            onClick={() => {
              const title = window.prompt(tr("assistant_chat.rename"), current.title);
              if (title && title.trim()) rename.mutate({ id: current.id, title: title.trim() });
            }}
            title={tr("assistant_chat.rename")} className={BUTTON_SMALL.secondary}>✎</button>
        )}
        <button onClick={() => setShowClosed((v) => !v)} className={BUTTON_SMALL.secondary}>
          {showClosed ? tr("assistant_chat.show_open") : tr("assistant_chat.show_closed")}
        </button>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-muted">{tr("assistant_chat.tell_assistant_what_do")}</p>
        <button onClick={() => setShowArchive((v) => !v)}
          className={BUTTON_SMALL.secondary}>
          {showArchive ? tr("assistant_chat.back_conversation") : tr("assistant_chat.archive")}
        </button>
        {!showArchive && messages.length > 0 && (
          <button onClick={() => allArchive.mutate()} disabled={allArchive.isPending}
            className={BUTTON_SMALL.secondary}>
            {tr("assistant_chat.archive_conversation")}
          </button>
        )}
      </div>
      {err && <div className="mb-2 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}

      <div ref={listingRef} className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-line bg-surface p-3">
        {more && (
          <div className="flex justify-center">
            <button onClick={loadOlder}
              className={BUTTON_SMALL.secondary}>
              {tr("assistant_chat.load_older")}
            </button>
          </div>
        )}
        {messages.length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            {showArchive ? tr("assistant_chat.nothing_archive") : tr("assistant_chat.nothing_yet_try_which")}
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="group space-y-2">
            <div className="flex items-start justify-end gap-1">
              {/* Archiving hangs off the message, because that is what one is looking at. */}
              {!RUNNING.includes(m.status) && (
                <button
                  onClick={() => archive.mutate(m.id)}
                  title={showArchive ? tr("assistant_chat.restore") : tr("assistant_chat.archive_2")}
                  className="mt-1 text-xs text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
                  {showArchive ? "↩" : "🗄"}
                </button>
              )}
              <div className="max-w-[80%] rounded-lg rounded-br-sm bg-brand px-3 py-2 text-sm text-white whitespace-pre-wrap">
                {m.text}
              </div>
            </div>
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-lg rounded-bl-sm border border-line bg-card px-3 py-2 text-sm text-ink">
                {m.status === "awaiting" ? (
                  <div className="space-y-2">
                    <div className="text-amber-400">🔐 {tr("assistant_chat.permission_needed")} <code>{m.pending_tool}</code></div>
                    <div className="flex gap-1">
                      {(["once", "always", "never"] as const).map((d) => (
                        <button key={d} onClick={() => decide.mutate({ id: m.id, decision: d })}
                          className={BUTTON_SMALL.secondary}>
                          {d === "once" ? tr("assistant_chat.once") : d === "always" ? "Immer" : "Nie"}</button>
                      ))}
                    </div>
                  </div>
                ) : ["new", "approved", "running"].includes(m.status) ? (
                  <span className="text-muted">🔄 {tr("assistant_chat.thinking")}</span>
                ) : m.error ? (
                  <span className="text-red-400 whitespace-pre-wrap">{m.error}</span>
                ) : (
                  <Markdown text={m.result || tr("assistant_chat.no_answer")} />
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <form className="mt-3 flex gap-2"
        onSubmit={(e) => { e.preventDefault(); setErr(""); if (input.trim()) send.mutate(input.trim()); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={tr("assistant_chat.message_to_the_assistant")}
          className="flex-1 rounded border border-line bg-card px-3 py-2 text-ink outline-none" />
        <button type="submit" disabled={send.isPending || !input.trim()}
          className={BUTTON.primary}>{tr("assistant_chat.send")}</button>
      </form>
    </div>
  );
}
