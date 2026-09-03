import { useCallback, useEffect, useRef, useState } from 'react';
import { tr, language } from "../../i18n";
import { useStore } from '../lib/store';
import { api, type AssistantMessage, type AssistantSession } from '../lib/api';
import { getActiveEditor } from '../lib/activeEditor';
import Icon from './Icon';
import AssistantAnswer from './AssistantAnswer';

/**
 * The assistant, in the sidebar.
 *
 * The same conversation it holds everywhere else — the messenger, its own web
 * interface — because there is one assistant and splitting the thread per door
 * would mean explaining the context again at each of them.
 *
 * What it does with a note it does itself: it has a key for this app's agent
 * API, so "put that in my notes" is a write it performs, and the note updates
 * under the cursor like any other change from outside. That is why there is no
 * "apply this answer" button here — there is nothing to copy across.
 *
 * An answer takes as long as it takes, so a message that is still being worked
 * on is polled for; the polling stops as soon as nothing is outstanding.
 */
export default function AssistantPanel() {
  const activePath = useStore((s) => s.activePath);
  const notify = useStore((s) => s.notify);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [name, setName] = useState('Assistent');
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  /**
   * Several conversations at once.
   *
   * One thread for everything means every question drags the last one's context
   * behind it — and the assistant summarises a long history before it answers,
   * so an unrelated question pays for all of it. Separate conversations keep
   * separate contexts, which is the point of having more than one.
   *
   * Which one is open is remembered per device: the phone and the desk are
   * usually in the middle of different things.
   */
  const [sessions, setSessions] = useState<AssistantSession[]>([]);
  const [sessionId, setSessionId] = useState<number | undefined>(() => {
    try {
      const v = Number(localStorage.getItem('assistant-session'));
      return v > 0 ? v : undefined;
    } catch {
      return undefined;
    }
  });
  const [picking, setPicking] = useState(false);
  /** Closed conversations are hidden by default; the switcher can show them. */
  const [showClosed, setShowClosed] = useState(false);
  const chooseSession = (id: number | undefined) => {
    setSessionId(id);
    setPicking(false);
    setMessages([]);
    try {
      if (id) localStorage.setItem('assistant-session', String(id));
      else localStorage.removeItem('assistant-session');
    } catch { /* private mode */ }
  };
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [withNote, setWithNote] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);

  // There is no asking whether there is an assistant any more: it is the
  // house's own and it is simply there. The bridge had a setting for it because
  // it held a token of its own for a machine it did not otherwise know.
  useEffect(() => {
    setEnabled(true);
    setName(tr("notes_assistant.name"));
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await api.assistantChat(30, sessionId);
      setMessages(r.messages);
      setError('');
      // Without an explicit one, the assistant answers in its newest
      // conversation — adopt that so the switcher shows where one actually is.
      if (!sessionId && r.messages.length && r.messages[0].session_id) {
        setSessionId(r.messages[0].session_id);
      }
    } catch (e: any) {
      setError(e.message || tr("notes_assistant.unreachable"));
    }
  }, [sessionId]);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.assistantSessions(showClosed));
    } catch {
      setSessions([]);
    }
  }, [showClosed]);

  useEffect(() => {
    if (!enabled) return;
    void load();
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled) return;
    void loadSessions();
  }, [enabled, loadSessions]);

  // Ohne gewaehlte Unterhaltung waere keine Nummer hervorgehoben. Eine noch
  // leere neue Unterhaltung bringt keine Nachricht mit, aus der sich die
  // Auswahl ableiten liesse - dann die neueste offene nehmen.
  useEffect(() => {
    const offen = sessions.filter((x) => !x.closed_at);
    if (!offen.length) return;
    // Auch wenn noch eine Unterhaltung gemerkt ist, die es nicht mehr gibt oder
    // die geschlossen wurde - sonst zeigt die Leiste auf nichts.
    if (sessionId && offen.some((x) => x.id === sessionId)) return;
    chooseSession(offen[0].id);
  }, [sessions, sessionId]);

  // Poll only while something is still running — an idle panel costs nothing.
  const waiting = messages.some((m) => ['new', 'approved', 'running', 'awaiting'].includes(m.status));
  useEffect(() => {
    if (!enabled || !waiting) return;
    const id = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(id);
  }, [enabled, waiting, load]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  /**
   * How long the answer has been coming.
   *
   * A question like "good morning" sends it through mail, calendar and notes,
   * and that takes minutes. Without a clock, "thinking…" and "crashed" look the
   * same, and the only way to find out is to give up and ask again.
   */
  const [, tick] = useState(0);
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(() => tick((n) => n + 1), 10_000);
    return () => window.clearInterval(id);
  }, [waiting]);
  const seit = (iso: string): string => {
    const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (!Number.isFinite(sec) || sec < 20) return '…';
    if (sec < 90) return ` … seit ${sec} Sekunden`;
    return ` … seit ${Math.round(sec / 60)} Minuten`;
  };

  const send = async (text: string) => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      const gesendet = await api.assistantSend(body, sessionId);
      if (!sessionId && gesendet?.session_id) chooseSession(gesendet.session_id);
      setDraft('');
      await load();
      void loadSessions();
    } catch (e: any) {
      notify(e.message || tr("notes_assistant.message_lost"));
    } finally {
      setSending(false);
    }
  };

  /** The question, with what is on screen behind it. */
  const compose = (question: string): string => {
    if (!withNote || !activePath) return question;
    const view = getActiveEditor();
    const sel = view?.state.selection.main;
    const selected = view && sel && !sel.empty ? view.state.sliceDoc(sel.from, sel.to) : '';
    const where = selected
      ? `In der Notiz „${activePath}" ist das hier markiert:\n\n${selected}\n\n`
      : `Ich sehe gerade die Notiz „${activePath}".\n\n`;
    return where + question;
  };

  const decide = async (id: number, decision: 'once' | 'always' | 'never') => {
    try {
      await api.assistantDecide(id, decision);
      await load();
    } catch (e: any) {
      notify(e.message || tr("notes_assistant.answer_lost"));
    }
  };

  if (enabled === null) return <div className="panel-empty">…</div>;
  if (!enabled) {
    return (
      <div className="panel-empty assistant-off">
        Kein Assistent eingerichtet. In den Einstellungen unter „Assistent" Adresse und Zugang
        eintragen.
      </div>
    );
  }

  const aktuell = sessions.find((x) => x.id === sessionId);
  const wann = (s: AssistantSession) => {
    const t = s.last_message_at ?? s.created_at;
    const d = new Date(t);
    return isNaN(d.getTime()) ? '' : d.toLocaleString(language(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="assistant-panel">
      <div className="assistant-head">
        <button
          className="assistant-picker"
          title={tr("notes_assistant.switch_conversation")}
          onClick={() => { setPicking((v) => !v); void loadSessions(); }}
        >
          <Icon name="message-square" size={14} />
          <span className="assistant-picker-title">{aktuell?.title || 'Unterhaltung'}</span>
          {sessions.some((x) => x.running && x.id !== sessionId) && <span className="assistant-busy-dot" title={tr("notes_assistant.busy_elsewhere")} />}
          <Icon name="chevrons-up-down" size={13} />
        </button>
        <button
          className="tool-btn"
          title={tr("notes_assistant.new_conversation")}
          onClick={async () => {
            try {
              const s = await api.assistantNewSession('');
              chooseSession(s.id);
              await loadSessions();
            } catch (e: any) {
              notify(e.message || tr("notes_assistant.no_conversation"));
            }
          }}
        >
          <Icon name="plus" size={15} />
        </button>
      </div>

      {picking && (
        <div className="assistant-sessions">
          <label className="assistant-closed-toggle">
            <input
              type="checkbox"
              checked={showClosed}
              onChange={(e) => {
                setShowClosed(e.target.checked);
              }}
            />
            Geschlossene zeigen
          </label>
          {sessions.length === 0 && (
            <div className="panel-empty">{showClosed ? 'Nichts geschlossen.' : tr("notes_assistant.no_conversation_yet")}</div>
          )}
          {sessions.map((s) => (
            <div key={s.id} className={`assistant-session${s.id === sessionId ? ' active' : ''}`}>
              <button className="assistant-session-open" onClick={() => chooseSession(s.id)}>
                <span className="assistant-session-title">{s.title || `Unterhaltung ${s.id}`}</span>
                {s.running && <span className="assistant-busy-dot" title={tr("notes_msg.arbeitet_gerade")} />}
                <span className="assistant-session-meta">
                  {s.message_count} · {wann(s)}
                </span>
              </button>
              <button
                className="tool-btn assistant-session-close"
                title={s.closed_at ? tr("notes_menu.open_again") : tr("common.close")}
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await api.assistantCloseSession(s.id, !s.closed_at);
                    // Die geschlossene aus dem Blick nehmen, sonst steht man in
                    // einer Unterhaltung, die man gerade weggeraeumt hat.
                    if (!s.closed_at && s.id === sessionId) chooseSession(undefined);
                    await loadSessions();
                  } catch (err: any) {
                    notify(err.message || tr("notes_assistant.did_not_work"));
                  }
                }}
              >
                <Icon name={s.closed_at ? 'rotate-ccw' : 'archive'} size={15} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="assistant-log" ref={listRef}>
        {error && <div className="assistant-error">{error}</div>}
        {messages.length === 0 && !error && (
          <div className="panel-empty">{tr("notes_assistant.nothing_yet")}</div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="assistant-turn">
            <div className="assistant-msg mine">{m.text}</div>
            {m.pending_tool && (
              <div className="assistant-ask">
                <div>
                  Darf {name} <b>{m.pending_tool}</b> benutzen?
                </div>
                <div className="assistant-ask-buttons">
                  <button className="tool-btn" onClick={() => void decide(m.id, 'once')}>
                    Diesmal
                  </button>
                  <button className="tool-btn" onClick={() => void decide(m.id, 'always')}>
                    Immer
                  </button>
                  <button className="tool-btn" onClick={() => void decide(m.id, 'never')}>
                    Nie
                  </button>
                </div>
              </div>
            )}
            {m.error && <div className="assistant-msg failed">{m.error}</div>}
            {m.result && <AssistantAnswer className="assistant-msg theirs" text={m.result} />}
            {!m.result && !m.error && !m.pending_tool && (
              <div className="assistant-msg thinking">
                <Icon name="refresh-cw" size={13} style={{ animation: 'spin 1s linear infinite' }} />
                {name} überlegt{seit(m.created_at)}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Wie in Claudian: die offenen Unterhaltungen als Nummern direkt ueber
          dem Eingabefeld. Der Umschalter oben zeigt Namen und kann schliessen;
          hier geht es nur ums schnelle Hin und Her, ohne die Hand zu bewegen. */}
      {sessions.filter((x) => !x.closed_at).length > 1 && (
        <div className="assistant-tabs">
          {sessions
            .filter((x) => !x.closed_at)
            .map((x, i) => (
              <button
                key={x.id}
                className={`assistant-tab${x.id === sessionId ? ' active' : ''}`}
                title={x.title || `Unterhaltung ${i + 1}`}
                onClick={() => chooseSession(x.id)}
              >
                {i + 1}
                {x.running && <span className="assistant-busy-dot" />}
              </button>
            ))}
        </div>
      )}

      <div className="assistant-compose">
        <label className="assistant-context" title={tr("notes_assistant.send_note_and_selection")}>
          <input type="checkbox" checked={withNote} onChange={(e) => setWithNote(e.target.checked)} />
          {tr("notes_assistant.send_note")}
        </label>
        <textarea
          className="assistant-input"
          placeholder={`An ${name} …`}
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter is a new line — the convention every
            // other chat window uses.
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send(compose(draft));
            }
          }}
        />
        <button className="btn assistant-send" disabled={!draft.trim() || sending} onClick={() => void send(compose(draft))}>
          <Icon name="arrow-up-right" size={15} />
          Senden
        </button>
      </div>
    </div>
  );
}
