import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import Markdown from "./Markdown";
import { KNOPF_KLEIN, KNOPF} from "./ui";

interface ChatMsg {
  id: number; text: string; status: string; result: string; error: string;
  pending_tool: string | null; created_at: string;
}
type Seite = { messages: ChatMsg[]; more: boolean };

const LAEUFT = ["new", "approved", "running", "awaiting"];

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
  const [archiv, setArchiv] = useState(false);
  // Older pages, fetched on demand. They stand before the live page and do not refresh
  // themselves: what is past does not change.
  const [aeltere, setAeltere] = useState<ChatMsg[]>([]);
  const [nochMehr, setNochMehr] = useState(false);
  const listeRef = useRef<HTMLDivElement>(null);
  const erstesBild = useRef(true);
  // Height before older messages were prepended, so that the view can stay where it stood.
  const hoeheVorher = useRef<number | null>(null);

  const { data } = useQuery({
    queryKey: ["assistant-chat", archiv],
    queryFn: () => api.get<Seite>(`/assistant/chat?limit=20${archiv ? "&archiv=1" : ""}`),
    refetchInterval: archiv ? false : 3000,
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["assistant-chat"] });
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const nachrichten = [...aeltere, ...(data?.messages || [])];
  const mehr = aeltere.length ? nochMehr : !!data?.more;

  // Switching between conversation and archive starts over; the pages of the one have
  // nothing to do with the other.
  useEffect(() => {
    setAeltere([]); setNochMehr(false); erstesBild.current = true;
  }, [archiv]);

  const send = useMutation({
    mutationFn: (text: string) => api.post("/assistant/chat", { text }),
    onSuccess: () => { setInput(""); inv(); },
    onError: fehler,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: string }) =>
      api.post(`/assistant/chat/${id}/decide`, { decision }),
    onSuccess: inv, onError: fehler,
  });
  const archivieren = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/chat/${id}/${archiv ? "unarchive" : "archive"}`),
    // The id comes back as the second argument, so the loaded older pages can drop that one
    // row instead of being thrown away and reloaded.
    onSuccess: (_antwort, id) => { setAeltere((v) => v.filter((m) => m.id !== id)); inv(); },
    onError: fehler,
  });
  const alleArchivieren = useMutation({
    mutationFn: () => api.post("/assistant/chat/archive-all"),
    onSuccess: () => { setAeltere([]); setNochMehr(false); erstesBild.current = true; inv(); },
    onError: fehler,
  });

  async function aeltereLaden() {
    const aelteste = nachrichten[0]?.id;
    if (!aelteste) return;
    hoeheVorher.current = listeRef.current?.scrollHeight ?? null;
    try {
      const seite = await api.get<Seite>(
        `/assistant/chat?limit=20&vor=${aelteste}${archiv ? "&archiv=1" : ""}`);
      setAeltere((v) => [...seite.messages, ...v]);
      setNochMehr(seite.more);
    } catch (e) { fehler(e); }
  }

  const letzteId = nachrichten[nachrichten.length - 1]?.id;
  const zustaende = nachrichten.map((m) => m.status).join();

  useLayoutEffect(() => {
    const el = listeRef.current;
    if (!el) return;
    // Older ones were prepended: keep the reading position instead of jumping.
    if (hoeheVorher.current !== null) {
      el.scrollTop += el.scrollHeight - hoeheVorher.current;
      hoeheVorher.current = null;
      return;
    }
    if (erstesBild.current) {
      // Standing at the bottom, not travelling there: an animation over fifty messages is
      // exactly the scrolling nobody asked for.
      el.scrollTop = el.scrollHeight;
      erstesBild.current = false;
      return;
    }
    // Later only when one is standing down there anyway; otherwise reading further up
    // would be torn away by every answer.
    const untenNah = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (untenNah) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [letzteId, zustaende, aeltere.length]);

  return (
    <div className="flex h-[calc(100vh-16rem)] flex-col">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-muted">{tr("assistant_chat.einleitung")}</p>
        <button onClick={() => setArchiv((v) => !v)}
          className={KNOPF_KLEIN.neben}>
          {archiv ? tr("assistant_chat.zurueck_zum_verlauf") : tr("assistant_chat.archiv_zeigen")}
        </button>
        {!archiv && nachrichten.length > 0 && (
          <button onClick={() => alleArchivieren.mutate()} disabled={alleArchivieren.isPending}
            className={KNOPF_KLEIN.neben}>
            {tr("assistant_chat.verlauf_archivieren")}
          </button>
        )}
      </div>
      {err && <div className="mb-2 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}

      <div ref={listeRef} className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-line bg-surface p-3">
        {mehr && (
          <div className="flex justify-center">
            <button onClick={aeltereLaden}
              className={KNOPF_KLEIN.neben}>
              {tr("assistant_chat.aeltere_laden")}
            </button>
          </div>
        )}
        {nachrichten.length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            {archiv ? tr("assistant_chat.archiv_leer") : tr("assistant_chat.leer")}
          </div>
        )}
        {nachrichten.map((m) => (
          <div key={m.id} className="group space-y-2">
            <div className="flex items-start justify-end gap-1">
              {/* Archiving hangs off the message, because that is what one is looking at. */}
              {!LAEUFT.includes(m.status) && (
                <button
                  onClick={() => archivieren.mutate(m.id)}
                  title={archiv ? tr("assistant_chat.zurueckholen") : tr("assistant_chat.archivieren")}
                  className="mt-1 text-xs text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
                  {archiv ? "↩" : "🗄"}
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
                    <div className="text-amber-400">🔐 {tr("assistant_chat.freigabe_noetig")} <code>{m.pending_tool}</code></div>
                    <div className="flex gap-1">
                      {(["once", "always", "never"] as const).map((d) => (
                        <button key={d} onClick={() => decide.mutate({ id: m.id, decision: d })}
                          className={KNOPF_KLEIN.neben}>
                          {d === "once" ? "Einmal" : d === "always" ? "Immer" : "Nie"}</button>
                      ))}
                    </div>
                  </div>
                ) : ["new", "approved", "running"].includes(m.status) ? (
                  <span className="text-muted">🔄 {tr("assistant_chat.denkt_nach")}</span>
                ) : m.error ? (
                  <span className="text-red-400 whitespace-pre-wrap">{m.error}</span>
                ) : (
                  <Markdown text={m.result || tr("assistant_chat.keine_antwort")} />
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      <form className="mt-3 flex gap-2"
        onSubmit={(e) => { e.preventDefault(); setErr(""); if (input.trim()) send.mutate(input.trim()); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={tr("assistant_chat.nachricht_an_den_assistenten")}
          className="flex-1 rounded border border-line bg-card px-3 py-2 text-ink outline-none" />
        <button type="submit" disabled={send.isPending || !input.trim()}
          className={KNOPF.haupt}>{tr("assistant_chat.senden")}</button>
      </form>
    </div>
  );
}
