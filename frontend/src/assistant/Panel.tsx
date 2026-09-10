import { useCallback, useEffect, useRef, useState } from "react";
import { tr } from "../i18n";
import { toast } from "../toast";
import Markdown from "../components/Markdown";
import { BUTTON, BUTTON_SMALL, ICON, IconButton, INPUT_VALUE } from "../components/ui";
import { assistant, RUNNING, type Message, type Session, type Step } from "./api";
import { useAssistantOffers } from "./context";
import Steps from "./Steps";

/**
 * The conversation with the assistant, wherever you are standing.
 *
 * It lived in the notes, because that is where it was built. But there is one
 * assistant and one thread, and a panel that only opens on one page means
 * explaining the context again at every other door. So it moved out here and
 * the notes render it like everybody else.
 *
 * What it can be given along comes from the page, not from here (`context.tsx`):
 * a note in the notes, a month or a day in the calendar, the address of the
 * page everywhere else. The panel only asks what is on offer.
 *
 * An answer takes as long as it takes, so a message that is still being worked
 * on is polled for; the polling stops as soon as nothing is outstanding.
 */

/** Which conversation was open here last. Per device on purpose: the phone and
 *  the desk are usually in the middle of different things. */
const LAST = "traccoon.assistant.session";

export default function AssistantPanel({ compact = false }: { compact?: boolean }) {
  const offers = useAssistantOffers();
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [steps, setSteps] = useState<Record<number, Step[]>>({});
  const [sessionId, setSessionId] = useState<number | undefined>(() => {
    const stored = Number(localStorage.getItem(LAST) || 0);
    return stored > 0 ? stored : undefined;
  });
  const [showSessions, setShowSessions] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [withContext, setWithContext] = useState(true);
  const [chosen, setChosen] = useState("");
  // Ob die Wahl von Hand getroffen wurde. Solange nicht, folgt sie dem
  // Genauesten, was die Seite anbietet — wer vom Kalender in eine Notiz geht,
  // will die Notiz mitschicken und nicht weiter den Monat. Sobald jemand
  // selbst gewaehlt hat, bleibt seine Wahl stehen.
  const picked = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);
  const name = tr("notes_assistant.name");

  // What is on offer changes with the page. Keep a valid choice, take the first
  // one when the old choice is gone — a picker pointing at nothing sends nothing.
  useEffect(() => {
    if (!offers.length) return;
    const gone = !offers.some((o) => o.key === chosen);
    if (gone || !picked.current) setChosen(offers[0].key);
  }, [offers, chosen]);

  const chooseSession = useCallback((id: number | undefined) => {
    setSessionId(id);
    setSteps({});
    if (id) localStorage.setItem(LAST, String(id));
    else localStorage.removeItem(LAST);
  }, []);

  // `only` overrides the chosen conversation for this one read. Right after
  // sending into a conversation that did not exist a moment ago, the state
  // still holds the old one — reading with it would fetch the wrong messages
  // and the answer would look lost.
  const load = useCallback(async (only?: number) => {
    try {
      const r = await assistant.chat(30, only ?? sessionId);
      setMessages(r.messages);
      setError("");
      if (!only && !sessionId && r.messages.length && r.messages[0].session_id) {
        setSessionId(r.messages[0].session_id);
      }
    } catch (e: any) {
      setError(e?.message || tr("notes_assistant.unreachable"));
    }
  }, [sessionId]);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await assistant.sessions(showClosed));
    } catch {
      setSessions([]);
    }
  }, [showClosed]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadSessions(); }, [loadSessions]);

  // Ohne gewaehlte Unterhaltung waere keine hervorgehoben. Eine noch leere neue
  // bringt keine Nachricht mit, aus der sich die Auswahl ableiten liesse — dann
  // die neueste offene nehmen.
  useEffect(() => {
    const open = sessions.filter((x) => !x.closed_at);
    if (!open.length) return;
    if (sessionId && open.some((x) => x.id === sessionId)) return;
    chooseSession(open[0].id);
  }, [sessions, sessionId, chooseSession]);

  const waiting = messages.some((m) => RUNNING.includes(m.status));
  const running = messages.filter((m) => RUNNING.includes(m.status) && m.status !== "awaiting")
    .map((m) => m.id);
  const runningKey = running.join(",");

  /** Archive a conversation — from the list, or by right-clicking its number.
   *  An empty one is deleted rather than archived, and the server says so. */
  const archive = useCallback(async (s: Session) => {
    try {
      const r = await assistant.closeSession(s.id, !s.closed_at);
      // Whoever was standing in it has to be moved out, or the panel keeps
      // showing a conversation that is no longer in any list.
      if (!s.closed_at && s.id === sessionId) chooseSession(undefined);
      const rest = await assistant.sessions(false);
      // Die letzte weggeraeumt heisst nicht "kein Assistent mehr": ohne eine
      // offene Unterhaltung gibt es kein Eingabefeld, in das man schreiben
      // koennte, und der naechste Gedanke braucht dann erst einen Knopfdruck.
      if (rest.length === 0) {
        const frisch = await assistant.newSession();
        setSessions([frisch]);
        chooseSession(frisch.id);
      } else {
        setSessions(rest);
      }
      if (r?.deleted) toast(tr("assistant.empty_deleted"));
    } catch (e: any) {
      toast(e?.message || tr("common.error"), "error");
    }
  }, [sessionId, chooseSession, loadSessions]);

  // Was man ansieht, ist gelesen. Erst wenn nichts mehr laeuft: sonst waere die
  // Antwort, die gerade entsteht, schon als gesehen abgehakt, bevor sie da ist.
  useEffect(() => {
    if (!sessionId || waiting) return;
    if (!sessions.some((x) => x.id === sessionId && x.unread)) return;
    void assistant.markRead(sessionId)
      .then(() => setSessions((old) => old.map((x) =>
        x.id === sessionId ? { ...x, unread: false } : x)))
      .catch(() => {});
  }, [sessionId, waiting, sessions]);

  const loadSteps = useCallback(async (ids: number[]) => {
    for (const id of ids) {
      try {
        const have = steps[id] ?? [];
        const after = have.length ? have[have.length - 1].seq : 0;
        const r = await assistant.progress(id, after);
        if (!r.steps.length) continue;
        setSteps((old) => {
          // Merged by sequence number, not appended. `after` should make that
          // unnecessary, but two polls in flight at once would otherwise show
          // every step twice — and a list that repeats itself is worse than a
          // list that lags.
          const seen = new Set((old[id] ?? []).map((x) => x.seq));
          const fresh = r.steps.filter((x) => !seen.has(x.seq));
          if (!fresh.length) return old;
          return { ...old, [id]: [...(old[id] ?? []), ...fresh] };
        });
      } catch {
        // A step that cannot be fetched is not worth interrupting the answer
        // for: the message itself keeps arriving through `load`.
      }
    }
  }, [steps]);

  // The messages, and next to them what the running ones are doing. Same beat
  // for both, and none at all while nothing runs.
  useEffect(() => {
    if (!waiting) return;
    const tick = () => {
      void load();
      if (runningKey) void loadSteps(runningKey.split(",").map(Number));
    };
    tick();
    const id = window.setInterval(tick, 2500);
    return () => window.clearInterval(id);
  }, [waiting, load, runningKey, loadSteps]);

  // Die Marker sagen, was in den Unterhaltungen los ist — auch in denen, in
  // denen man gerade nicht steht. Dafuer muss die Liste sich bewegen: der Takt
  // oben holt nur die Nachrichten der offenen Unterhaltung, und ein Marker, der
  // seine Farbe von vorhin behaelt, sagt genau das Falsche.
  const regung = sessions.some((x) => !x.closed_at && (x.running || x.asking));
  useEffect(() => {
    if (!regung && !waiting) return;
    const id = window.setInterval(() => void loadSessions(), 4000);
    return () => window.clearInterval(id);
  }, [regung, waiting, loadSessions]);

  // Und einmal, sobald hier nichts mehr laeuft. Ohne das bliebe der Marker gelb,
  // nachdem alles abgebrochen wurde: der Takt darueber haelt dann ja auch an,
  // und der letzte Stand waere fuer immer der von kurz davor.
  useEffect(() => { void loadSessions(); }, [waiting, loadSessions]);

  // How long the answer has been coming. A question like "good morning" runs
  // through mail, calendar and notes and takes minutes; without a clock
  // "thinking…" and "crashed" look the same.
  const [, tick] = useState(0);
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(() => tick((n) => n + 1), 10_000);
    return () => window.clearInterval(id);
  }, [waiting]);
  const since = (iso: string): string => {
    const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (!Number.isFinite(sec) || sec < 20) return "…";
    if (sec < 90) return tr("notes_assistant.for_seconds", { n: String(sec) });
    return tr("notes_assistant.for_minutes", { n: String(Math.round(sec / 60)) });
  };

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  /** The question, with what the page offers in front of it. */
  const compose = (question: string): string => {
    if (!withContext) return question;
    const offer = offers.find((o) => o.key === chosen);
    const seen = offer?.get().trim();
    return seen ? `${seen}\n\n${question}` : question;
  };

  const send = async (text: string) => {
    const body = compose(text).trim();
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      const sent = await assistant.send(body, sessionId);
      const landed = sent?.session_id ?? undefined;
      if (!sessionId && landed) chooseSession(landed);
      setDraft("");
      await load(sessionId ? undefined : landed);
      void loadSessions();
    } catch (e: any) {
      toast(e?.message || tr("notes_assistant.message_lost"), "error");
    } finally {
      setSending(false);
    }
  };

  const decide = async (id: number, decision: "once" | "always" | "never") => {
    try {
      await assistant.decide(id, decision);
      await load();
    } catch (e: any) {
      toast(e?.message || tr("notes_assistant.answer_lost"), "error");
    }
  };

  // Die erste Nachricht, die wartet. Ab ihr wartet alles, was danach kommt —
  // also steht der Hinweis einmal darueber und nicht unter jeder einzelnen.
  const erstesWartende = messages.find((m) => m.status === "queued")?.id;

  const open = sessions.filter((x) => !x.closed_at);
  // Die Nummernreihe geht nach dem Alter, nicht nach der letzten Aktivitaet: eine
  // neue Unterhaltung stellt sich hinten an, und die Nummer, die eine Unterhaltung
  // einmal hat, behaelt sie. Nach Aktivitaet sortiert waeren die Zahlen bei jeder
  // Antwort andere — und dann ist eine Nummer kein Platz mehr, den man sich merkt.
  const marker = [...open].sort((a, b) => a.id - b.id);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Kopf: welche Unterhaltung, und der Weg zu den anderen. Die Liste legt
          sich darueber statt sich dazwischen zu schieben — aufgeklappt hat sie
          sonst den ganzen Wortwechsel nach unten geschoben. Deshalb `relative`
          hier und `absolute` dort. */}
      <div className="relative flex items-center gap-2 border-b border-line px-2 py-1.5">
        {/* Als Bedienelement erkennbar: Rahmen und Flaeche, damit man sieht, dass
            sich hier etwas aufklappt. Ohne beides sah es aus wie eine Ueberschrift. */}
        <button className={`flex min-w-0 flex-1 items-center gap-1.5 rounded border px-2 py-1
            text-left text-sm ${showSessions
              ? "border-brand bg-surface" : "border-line bg-surface/60 hover:bg-surface"}`}
          aria-expanded={showSessions}
          onClick={() => setShowSessions((v) => !v)}>
          <span className="min-w-0 flex-1 truncate text-ink">
            {sessions.find((x) => x.id === sessionId)?.title || tr("notes_assistant.new_conversation")}
          </span>
          <span className="shrink-0 text-muted">{showSessions ? "▴" : "▾"}</span>
        </button>
        {open.some((x) => x.running && x.id !== sessionId) && (
          <span title={tr("notes_assistant.busy_elsewhere")}
            className="h-2 w-2 shrink-0 rounded-full bg-brand" />
        )}
        <IconButton icon={ICON.fresh} title={tr("notes_assistant.new_conversation")}
          onClick={async () => {
            try {
              const s = await assistant.newSession();
              // Erst in die Liste, dann hinein: `chooseSession` allein reicht nicht,
              // solange `sessions` die neue noch nicht kennt — der Kopf zeigt sonst
              // weiter den alten Titel und die Marker die alte Auswahl.
              setSessions((old) => [s, ...old.filter((x) => x.id !== s.id)]);
              chooseSession(s.id);
              setShowSessions(false);
              await loadSessions();
            } catch (e: any) {
              toast(e?.message || tr("common.error"), "error");
            }
          }} />

        {showSessions && (
          <>
            {/* Fangflaeche: ein Klick daneben klappt wieder zu — dasselbe Paar
                aus Flaeche und Karte wie in `Layout.tsx`. */}
            <div className="fixed inset-0 z-30" onClick={() => setShowSessions(false)} />
            <div className="absolute inset-x-2 top-full z-40 mt-1 max-h-72 overflow-y-auto
                rounded-lg border border-line bg-card p-1 shadow-2xl">
              <label className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-muted">
                <input type="checkbox" checked={showClosed}
                  onChange={(e) => setShowClosed(e.target.checked)} />
                {tr("notes_assistant.show_closed")}
              </label>
              {sessions.length === 0 && (
                <div className="px-2 py-2 text-xs text-muted">
                  {tr("notes_assistant.no_conversation_yet")}
                </div>
              )}
              {sessions.map((s) => (
                <div key={s.id}
                  className={`flex items-center gap-1 rounded px-2 py-1 hover:bg-surface ${
                    s.id === sessionId ? "bg-surface" : ""}`}>
                  {/* Waehlen schliesst die Liste: sie hat ihre Frage beantwortet,
                      und offen verdeckt sie genau das, was man sehen wollte. */}
                  <button className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    onClick={() => { chooseSession(s.id); setShowSessions(false); }}>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">
                      {s.title || `#${s.id}`}
                    </span>
                    {s.running && <span className="h-2 w-2 shrink-0 rounded-full bg-brand"
                      title={tr("notes_msg.arbeitet_gerade")} />}
                    <span className="shrink-0 text-xs text-muted">{s.message_count}</span>
                  </button>
                  {/* Quiet on purpose, unlike `IconButton`: that one is filled with the
                      house colour, and a list of twenty rows would be a row of twenty
                      coloured squares — the warning stands in `ui.tsx` itself. Here the
                      row is the handgrip; this is what you reach for second. */}
                  <button type="button"
                    className="shrink-0 rounded px-1.5 py-0.5 text-sm text-muted
                      hover:bg-line hover:text-ink"
                    title={s.closed_at ? tr("notes_menu.open_again") : tr("common.close")}
                    aria-label={s.closed_at ? tr("notes_menu.open_again") : tr("common.close")}
                    onClick={async () => {
                      try {
                        await archive(s);
                      } catch (e: any) {
                        toast(e?.message || tr("notes_assistant.did_not_work"), "error");
                      }
                    }}>
                    {s.closed_at ? ICON.again : ICON.archive}
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div ref={listRef} data-assistant="log" className="min-h-0 flex-1 overflow-y-auto p-2.5">
        {error && <div className="px-2 py-2 text-sm text-red-400">{error}</div>}
        {messages.length === 0 && !error && (
          <div className="px-2 py-6 text-center text-sm text-muted">
            {tr("notes_assistant.nothing_yet")}
          </div>
        )}
        {messages.map((m) => (
          // Ein Wortwechsel ist eine Spalte, in der jede Blase ihre Seite waehlt:
          // was ich geschrieben habe rechts, was der Assistent sagt links. Farbe
          // allein traegt das nicht — zwei gedaempfte Flaechen nebeneinander
          // sehen sehr aehnlich aus.
          <div key={m.id} data-assistant="turn" className="mb-3.5 flex flex-col items-start gap-1.5">
            {/* Einmal, ueber der ersten wartenden Nachricht: von da an wartet
                alles. Unter jeder einzelnen stand derselbe Satz mehrfach und
                sah aus wie eine Antwort auf sie. */}
            {m.id === erstesWartende && (
              <div className="mb-1 w-full border-t border-line pt-1.5 text-xs text-muted">
                {tr("assistant.queued")}
              </div>
            )}
            <div data-assistant="mine"
              className="max-w-[85%] self-end whitespace-pre-wrap break-words rounded-lg
                         rounded-br-sm border border-green-500/35 bg-green-500/15
                         px-2.5 py-2 text-sm leading-relaxed text-ink">
              {m.text}
            </div>

            {m.pending_tool && (
              <div className="max-w-[85%] self-start rounded-lg rounded-bl-sm border
                              border-brand bg-surface px-2.5 py-2 text-xs text-ink">
                <div>{tr("notes_assistant.may_i_use", { name, tool: m.pending_tool })}</div>
                <div className="mt-2 flex gap-1.5">
                  <button className={BUTTON_SMALL.secondary}
                    onClick={() => void decide(m.id, "once")}>
                    {tr("notes_assistant.this_time")}
                  </button>
                  <button className={BUTTON_SMALL.secondary}
                    onClick={() => void decide(m.id, "always")}>
                    {tr("notes_assistant.always")}
                  </button>
                  <button className={BUTTON_SMALL.secondary}
                    onClick={() => void decide(m.id, "never")}>
                    {tr("notes_assistant.never")}
                  </button>
                </div>
              </div>
            )}

            {m.error && (
              <div className="max-w-[85%] self-start rounded-lg rounded-bl-sm border border-line
                              bg-surface px-2.5 py-2 text-sm text-amber-400">
                {m.error}
              </div>
            )}

            {m.result && (
              <div data-assistant="theirs"
                className="max-w-[85%] self-start rounded-lg rounded-bl-sm border border-line
                           border-l-[3px] border-l-brand bg-surface px-3 py-2.5 text-ink">
                <Markdown text={m.result} />
              </div>
            )}

            {/* Answered together with the message that came after it. Finished,
                even though it has no answer of its own — and it has to say so:
                read as "no result, no error", it looked like it was still being
                worked on, and a stop button appeared for a run that does not
                exist. */}
            {m.status === "merged" && (
              <div className="self-start text-xs text-muted">{tr("assistant.merged")}</div>
            )}

            {/* Still working: the steps stand here, and the moment the answer
                arrives this whole block is replaced by it. Beside them the way
                out — an answer takes as long as it takes, and sometimes the
                answer to "keep going" is no.

                Asked positively, by the status. "No result and no error" is not
                the same question and answers yes for every state that ends
                without either. */}
            {RUNNING.includes(m.status) && m.status !== "queued" && !m.result && !m.pending_tool && (
              <div className="flex w-full items-start gap-2">
                <div className="min-w-0 flex-1">
                  <Steps steps={steps[m.id] ?? []} name={name} since={since(m.created_at)} />
                </div>
                <button className={BUTTON_SMALL.secondary}
                  title={tr("assistant.stop_hint")}
                  onClick={async () => {
                    try {
                      await assistant.stop(m.id);
                      await load();
                    } catch (e: any) {
                      toast(e?.message || tr("common.error"), "error");
                    }
                  }}>
                  {tr("assistant.stop")}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Die offenen Unterhaltungen als Nummern direkt ueber dem Eingabefeld:
          schnelles Hin und Her, ohne die Hand zu bewegen.

          Die Farbe sagt, was in einer los ist, und die drei Faelle sind
          verschieden dringend: rot fragt nach jemandem, gelb braucht Zeit, gruen
          liegt fertig da. Trifft keiner zu, ist alles gelesen und der Marker
          bleibt still — ein Feld, das immer leuchtet, sagt nichts mehr. Der
          blaue Rahmen ist die aktive, unabhaengig von der Farbe. */}
      {open.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-line px-2 py-1">
          {marker.map((x, i) => {
            const zustand = x.asking
              ? { farbe: "bg-red-500/25 text-red-200", was: tr("assistant.marker_asking") }
              : x.running
                ? { farbe: "bg-amber-500/25 text-amber-200", was: tr("assistant.marker_working") }
                : x.unread
                  ? { farbe: "bg-green-500/25 text-green-200", was: tr("assistant.marker_unread") }
                  : { farbe: "bg-surface text-muted", was: "" };
            const aktiv = x.id === sessionId;
            return (
              <button key={x.id}
                title={[x.title || `#${x.id}`, zustand.was, tr("assistant.marker_archive_hint")]
                  .filter(Boolean).join(" — ")}
                className={`flex h-6 w-6 items-center justify-center rounded border text-xs ${
                  zustand.farbe} ${aktiv ? "border-brand" : "border-transparent"}`}
                onClick={() => chooseSession(x.id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  void archive(x);
                }}>
                {i + 1}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex flex-col gap-1.5 border-t border-line p-2">
        {offers.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={withContext}
                onChange={(e) => setWithContext(e.target.checked)} />
              {offers.length === 1
                ? offers[0].label
                : tr("assistant.send_along")}
            </label>
            {/* Mehr als ein Angebot ist eine Wahl, keine Reihe von Haken: der
                Kalender bietet Monat, Woche und Tag, und gemeint ist immer
                genau eines davon. */}
            {offers.length > 1 && (
              <select className="rounded border border-line bg-surface px-1 py-0.5 text-xs"
                value={chosen} disabled={!withContext}
                onChange={(e) => { picked.current = true; setChosen(e.target.value); }}>
                {offers.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
            )}
          </div>
        )}
        <textarea className={`${INPUT_VALUE} resize-y`} rows={compact ? 2 : 3}
          placeholder={tr("notes_assistant.to_assistant", { name })}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter is a new line — the convention every
            // other chat window uses.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }} />
        <button className={BUTTON.primary} disabled={!draft.trim() || sending}
          onClick={() => void send(draft)}>
          {tr("common.send")}
        </button>
      </div>
    </div>
  );
}
