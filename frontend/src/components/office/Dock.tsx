// Schicht 2 — das Dock unter der Bühne. Vier Reiter: drei auf dieselben Daten —
// **Chat** (was gesagt wurde), **Agenten** (wer da ist) und **Werkzeuge** (was getan wurde) —
// und einer daneben: die **Personalakte** (was eine Rolle über alle Läufe hinweg tut).
//
// Die Akte ist bewusst der Fremdkörper hier. Die ersten drei Reiter lesen das Log dieser
// Sitzung und frieren mit dem Raum ein; die Akte fragt selbst beim Server nach und hat ihr
// eigenes Zeitfenster über Läufe **und** Sitzungen hinweg. Sie sitzt trotzdem im Dock und
// nicht im Inspektor: der Inspektor sagt in seinem Kopf ausdrücklich zu, ohne eigene Abfrage
// auszukommen, und hätte in seiner 45 %-Kachel auch keinen Platz dafür. Der Reiter ist eine
// Liste von **Rollen** — genau die Achse, die dem `agents`-Reiter fehlt.
//
// ── Woher der Text kommt, und was das kostet ────────────────────────────────────────────────
//
// Der `Recorder` bewahrt **Kommandos** auf, keine Ereignisse (`LogEntry = {ts, seq, cmds}`) —
// das ist die Naht, die den Replay ohne Momentaufnahmen möglich macht. Der Chat liest deshalb
// aus den Kommandos `say`/`think`/`deliver`/`gate`/`done` und nicht aus `agent_text` &
// Verwandtschaft. Praktisch ist das dasselbe: `user_message` und `agent_text` werden in
// `mapEvent` genau zu diesen Kommandos.
//
// Eine Ausnahme gibt es, und sie ist bewusst: `system`-Meldungen (Abbruch, Kappung,
// Kompaktierung) erzeugen laut `mapEvent` **kein** Kommando und erscheinen deshalb hier nicht.
// Sie nachzureichen hieße, den Ereignisstrom ein zweites Mal vorzuhalten — dieselbe Datenlage
// an zwei Orten, mit zwei Kappungsregeln. Wenn sie gebraucht werden, gehören sie in `mapEvent`.
//
// ── Einfrieren ──────────────────────────────────────────────────────────────────────────────
//
// Beim Zurückspulen zeigt das Dock denselben Moment wie der Raum: alles mit `ts > seekTs` wird
// weggelassen. Ein Dock, das weiterläuft, während die Bühne in der Vergangenheit steht, wäre
// zwei Ansichten desselben Laufs, die sich widersprechen.

import { tr } from "../../i18n";
import { useEffect, useMemo, useRef } from "react";
import Personalakte from "./Personalakte.tsx";
import type { Scope } from "./api.ts";
import type { Cmd, Roster, RosterEntry } from "./types.ts";
import type { LogQuelle } from "./Timeline.tsx";
import {
  GATE_TEXT, dauerText, passtZumFilter, statusFarbe, statusText, tokenText, uhrText, usdText, zahl,
} from "./TopBar.tsx";

// ── Kappung ─────────────────────────────────────────────────────────────────────────────────
//
// Alle drei Listen können vierstellig werden. Gekappt wird vom **ältesten** Ende — dieselbe
// Richtung wie Log und Ereignisfenster — und es wird gesagt, dass gekappt wurde.

const CHAT_CAP = 200;
const WERKZEUG_CAP = 200;
const AGENT_CAP = 80;

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

export type DockTab = "chat" | "agents" | "tools" | "akte";

export const DOCK_TABS: readonly { key: DockTab; label: string; icon: string }[] = [
  { key: "chat", label: "dock.chat", icon: "💬" },
  { key: "agents", label: "dock.agenten", icon: "🤖" },
  { key: "tools", label: "dock.werkzeuge", icon: "🔧" },
  { key: "akte", label: "dock.akte", icon: "📇" },
];

export interface DockProps {
  scope: Scope;
  tab: DockTab;
  onTabChange: (t: DockTab) => void;
  recorder: LogQuelle;
  /** Neuberechnungssignal des Feeds. */
  revision: number;
  roster: Roster;
  /** Sitzungsfilter, `null` = Alle. Dimmt, entfernt nicht. */
  filter: string | null;
  /** `null` = Gegenwart, sonst Epoch-ms: das Dock friert auf diesen Moment ein. */
  seekTs: number | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  className?: string;
}

// ── Abgeleitete Zeilen ──────────────────────────────────────────────────────────────────────

interface ChatZeile {
  key: string;
  ts: number;
  id: string;
  icon: string;
  text: string;
  /** Klasse für den Text — Fehler und Gates heben sich ab. */
  css?: string;
}

function chatAus(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], bis: number | null): ChatZeile[] {
  const out: ChatZeile[] = [];
  for (const e of log) {
    if (bis !== null && e.ts > bis) continue;
    e.cmds.forEach((c, i) => {
      const key = `${e.seq}:${i}`;
      if (c.k === "say") out.push({ key, ts: e.ts, id: c.id, icon: "💬", text: c.text });
      else if (c.k === "think") out.push({ key, ts: e.ts, id: c.id, icon: "💭", text: c.text, css: "italic text-muted" });
      else if (c.k === "deliver") out.push({ key, ts: e.ts, id: c.id, icon: "📨", text: c.text || tr("dock.uebergibt_ergebnis") });
      else if (c.k === "gate") out.push({ key, ts: e.ts, id: c.id, icon: "⏸", text: c.text || tr(GATE_TEXT[c.kind]), css: "text-orange-400" });
      else if (c.k === "done") {
        out.push({
          key, ts: e.ts, id: c.id, icon: c.ok ? "✅" : "❌",
          text: c.text || (c.ok ? "fertig" : "abgebrochen"),
          css: c.ok ? undefined : "text-red-400",
        });
      }
    });
  }
  return out;
}

interface WerkzeugZeile {
  key: string;
  ts: number;
  id: string;
  tool: string;
  target?: string;
  /** `null` = kein Ende im Fenster (läuft noch oder das Ende ist herausgekappt). */
  dauer: number | null;
  /** Dreiwertig **plus** `undefined` für „läuft noch". `null` heißt *unbekannt*, nicht *gut*. */
  ok: boolean | null | undefined;
}

/** Paart `tool` mit dem nächsten `toolEnd` derselben Figur.
 *
 *  Die Kommandos tragen keine `tool_use_id` — die bleibt im Ereignis. Das genügt trotzdem: ein
 *  Lauf ruft seine Werkzeuge nacheinander auf (der Worker wartet jeden Aufruf ab), also ist
 *  „das nächste Ende derselben Figur" auch das richtige. Die Dauer ist damit der Abstand der
 *  Wanduhrzeiten, was beim Altdaten-Pfad (beide aus **einer** Zeile synthetisiert) korrekt 0
 *  ergibt und nicht die Ersatzdauer der Bühne — die ist eine Darstellungsentscheidung und keine
 *  Messung, und eine erfundene Dauer in einer Liste, die Dauern zeigt, wäre eine Lüge. */
function werkzeugeAus(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], bis: number | null): WerkzeugZeile[] {
  const offen = new Map<string, WerkzeugZeile>();
  const out: WerkzeugZeile[] = [];
  for (const e of log) {
    if (bis !== null && e.ts > bis) continue;
    e.cmds.forEach((c, i) => {
      if (c.k === "tool") {
        const z: WerkzeugZeile = {
          key: `${e.seq}:${i}`, ts: e.ts, id: c.id, tool: c.tool,
          target: c.target, dauer: null, ok: undefined,
        };
        // Ein zweiter Start ohne Ende verdrängt den ersten nur aus der Paarung — in der Liste
        // bleibt er stehen (er lief ja) und behält sein „läuft noch".
        offen.set(c.id, z);
        out.push(z);
      } else if (c.k === "toolEnd") {
        const z = offen.get(c.id);
        if (!z) return;
        offen.delete(c.id);
        z.ok = c.ok;
        z.dauer = Math.max(0, e.ts - z.ts);
      }
    });
  }
  // `out` steht in Startreihenfolge; die offenen Einträge sind bereits darin enthalten.
  return out;
}

// ── Die Komponente ──────────────────────────────────────────────────────────────────────────

export default function Dock({
  scope, tab, onTabChange, recorder, revision, roster, filter, seekTs,
  selectedId, onSelect, className,
}: DockProps) {
  const log = useMemo(() => recorder.entries(), [recorder, revision]);
  const chat = useMemo(() => chatAus(log, seekTs), [log, seekTs]);
  const werkzeuge = useMemo(() => werkzeugeAus(log, seekTs), [log, seekTs]);

  const nachId = useMemo(() => {
    const m = new Map<string, RosterEntry>();
    for (const r of roster) m.set(r.agent_id, r);
    return m;
  }, [roster]);

  const agenten = useMemo(() => {
    const kopie = [...roster];
    // Laufendes zuerst, danach das Jüngste — die Reihenfolge, in der man hinsieht.
    kopie.sort((a, b) => {
      const la = a.status === "running", lb = b.status === "running";
      if (la !== lb) return la ? -1 : 1;
      return (b.started_at ? Date.parse(b.started_at) : 0) - (a.started_at ? Date.parse(a.started_at) : 0);
    });
    return kopie;
  }, [roster]);

  /** Die **Rolle** der ausgewählten Figur — der Sprungpunkt der Akte. Ein Lauf ohne Rolle
   *  (Job, Assistent) ergibt `null`: dann bleibt die Wahl in der Akte beim Betrachter, statt
   *  auf eine leere Rolle zu zeigen. */
  const gewaehlteRolle = selectedId ? (nachId.get(selectedId)?.agent || null) : null;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Im Livebetrieb ans Ende scrollen; beim Zurückspulen ausdrücklich **nicht** — dort hat der
  // Betrachter eine Stelle gewählt und will sie behalten.
  // Die Akte ist keine Liste, die nach unten wächst — sie ans Ende zu scrollen zeigte den
  // letzten Werkzeugbalken der letzten Rolle statt der Überschrift mit dem Zeitfenster.
  useEffect(() => {
    if (seekTs !== null || tab === "akte") return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [revision, tab, seekTs]);

  const name = (id: string): string => {
    const r = nachId.get(id);
    if (!r) return id;
    return r.agent || `Lauf ${r.run_id}`;
  };
  const gedimmt = (id: string): boolean => {
    const r = nachId.get(id);
    return r ? !passtZumFilter(scope, r, filter) : false;
  };

  return (
    <div className={`flex min-h-0 flex-col rounded border border-line bg-card ${className ?? ""}`}>
      <div className="flex shrink-0 gap-1 border-b border-line px-2 pt-1.5" role="tablist"
        aria-label="Dock">
        {DOCK_TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            onClick={() => onTabChange(t.key)}
            className={"rounded-t border-b-2 px-2.5 py-1 text-xs "
              + (tab === t.key ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink")}>
            {t.icon} {tr(t.label)}
            {/* Die Akte bekommt keine Zahl: wie viele Rollen es gibt, weiß erst ihre eigene
                Abfrage — eine Zahl aus dem Roster wäre eine andere Menge mit demselben
                Aussehen. */}
            {t.key !== "akte" && (
              <span className="ml-1 text-[11px] text-muted">
                {t.key === "chat" ? chat.length : t.key === "agents" ? roster.length : werkzeuge.length}
              </span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        {/* Der Einfrierhinweis gilt für die drei Log-Reiter. Die Akte friert nicht ein — sie
            nennt ihr eigenes Fenster in der eigenen Überschrift. */}
        {seekTs !== null && tab !== "akte" && (
          <span className="self-center pb-1 text-[11px] text-orange-400"
            title={tr("dock.selber_moment")}>
            {tr("dock.eingefroren_auf")} {uhrText(seekTs)}
          </span>
        )}
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {tab === "chat" && (
          <ChatListe zeilen={chat} name={name} gedimmt={gedimmt} onSelect={onSelect} />
        )}
        {tab === "agents" && (
          <AgentListe eintraege={agenten} scope={scope} filter={filter}
            selectedId={selectedId} onSelect={onSelect} />
        )}
        {tab === "tools" && (
          <WerkzeugListe zeilen={werkzeuge} name={name} gedimmt={gedimmt} onSelect={onSelect} />
        )}
        {tab === "akte" && (
          // Die Rolle der ausgewählten Figur, nicht die Figur selbst: der Inspektor darunter
          // zeigt weiter den einzelnen Lauf, die Akte die Rolle. Zwei Wahrheiten nebeneinander,
          // keine gibt sich für die andere aus.
          <Personalakte scope={scope} focusAgent={gewaehlteRolle} />
        )}
      </div>
    </div>
  );
}

// ── Kappungshinweis ─────────────────────────────────────────────────────────────────────────

function Gekappt({ n }: { n: number }) {
  if (n <= 0) return null;
  return (
    <div className="mb-1 border-b border-dashed border-line pb-1 text-[11px] text-muted">
      {tr("dock.gekappt", { anzahl: zahl(n) })}
    </div>
  );
}

function Leer({ text }: { text: string }) {
  return <div className="py-4 text-center text-xs text-muted">{text}</div>;
}

// ── Chat ────────────────────────────────────────────────────────────────────────────────────

function ChatListe({ zeilen, name, gedimmt, onSelect }: {
  zeilen: ChatZeile[];
  name: (id: string) => string;
  gedimmt: (id: string) => boolean;
  onSelect: (id: string) => void;
}) {
  if (zeilen.length === 0) return <Leer text={tr("dock.nichts_gesagt")} />;
  const zeige = zeilen.slice(-CHAT_CAP);
  return (
    <div className="space-y-1">
      <Gekappt n={zeilen.length - zeige.length} />
      {zeige.map((z) => (
        <div key={z.key} className={`flex gap-2 text-xs ${gedimmt(z.id) ? "opacity-40" : ""}`}>
          <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(z.ts)}</span>
          <span className="shrink-0">{z.icon}</span>
          <button type="button" onClick={() => onSelect(z.id)}
            className="shrink-0 max-w-[9rem] truncate text-left text-muted hover:text-brand"
            title={tr("dock.figur_waehlen", { name: name(z.id) })}>
            {name(z.id)}
          </button>
          <span className={`min-w-0 flex-1 break-words ${z.css ?? ""}`}>{z.text}</span>
        </div>
      ))}
    </div>
  );
}

// ── Agenten ─────────────────────────────────────────────────────────────────────────────────

function AgentListe({ eintraege, scope, filter, selectedId, onSelect }: {
  eintraege: RosterEntry[];
  scope: Scope;
  filter: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (eintraege.length === 0) return <Leer text={tr("dock.niemand_im_raum")} />;
  const zeige = eintraege.slice(0, AGENT_CAP);
  const jetzt = Date.now();
  return (
    <div className="space-y-1">
      {zeige.map((r) => {
        const start = r.started_at ? Date.parse(r.started_at) : NaN;
        const ende = r.ended_at ? Date.parse(r.ended_at) : (r.status === "running" ? jetzt : NaN);
        const dauer = Number.isFinite(start) && Number.isFinite(ende) ? ende - start : null;
        const aus = !passtZumFilter(scope, r, filter);
        return (
          <button key={r.agent_id} type="button" onClick={() => onSelect(r.agent_id)}
            aria-pressed={selectedId === r.agent_id}
            className={"flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded border px-2 py-1 text-left text-xs "
              + (selectedId === r.agent_id ? "border-brand bg-brand/5" : "border-transparent hover:border-line")
              + (aus ? " opacity-40" : "")}>
            <span className="font-medium">{r.agent || `Lauf ${r.run_id}`}</span>
            {r.phase && <span className="text-muted">{tr(r.phase === "plan" ? "dock.planung" : "dock.ausfuehrung")}</span>}
            <span className={statusFarbe(r.status)}>{statusText(r.status)}</span>
            {r.issue_key && <span className="font-mono text-[11px] text-brand">{r.issue_key}</span>}
            <div className="flex-1" />
            <span className="text-muted" title={r.provider ? `${r.provider} · ${r.model ?? "—"}` : undefined}>
              {r.model || "—"}
            </span>
            <span className="text-muted" title={`Eingabe ${zahl(r.in_tokens)} · Ausgabe ${zahl(r.out_tokens)}`}>
              {tokenText(r.in_tokens + r.out_tokens)}tok
            </span>
            <span className="text-muted">{usdText(r.cost_usd, r.cost_priced !== true)}</span>
            <span className="text-muted">{dauerText(dauer)}</span>
          </button>
        );
      })}
      <Gekappt n={eintraege.length - zeige.length} />
    </div>
  );
}

// ── Werkzeuge ───────────────────────────────────────────────────────────────────────────────

/** `ok === null` ist **unbekannt**, nicht grün: bei Altdaten hat niemand gemessen, ob der Aufruf
 *  durchlief. Ein Häkchen darauf wäre eine Behauptung über Daten, die es nicht gibt. */
function ergebnis(ok: boolean | null | undefined): { zeichen: string; css: string; titel: string } {
  if (ok === undefined) return { zeichen: "…", css: "text-muted", titel: tr("dock.laeuft_noch") };
  if (ok === true) return { zeichen: "✓", css: "text-green-400", titel: tr("buero.st_success") };
  if (ok === false) return { zeichen: "✕", css: "text-red-400", titel: tr("buero.st_failed") };
  return { zeichen: "?", css: "text-muted", titel: tr("dock.unbekannt_altdaten") };
}

function WerkzeugListe({ zeilen, name, gedimmt, onSelect }: {
  zeilen: WerkzeugZeile[];
  name: (id: string) => string;
  gedimmt: (id: string) => boolean;
  onSelect: (id: string) => void;
}) {
  if (zeilen.length === 0) return <Leer text={tr("dock.kein_werkzeug")} />;
  const zeige = zeilen.slice(-WERKZEUG_CAP);
  return (
    <div className="space-y-1">
      <Gekappt n={zeilen.length - zeige.length} />
      {zeige.map((z) => {
        const e = ergebnis(z.ok);
        return (
          <div key={z.key} className={`flex items-center gap-2 text-xs ${gedimmt(z.id) ? "opacity-40" : ""}`}>
            <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(z.ts)}</span>
            <span className={`shrink-0 ${e.css}`} title={e.titel}>{e.zeichen}</span>
            <button type="button" onClick={() => onSelect(z.id)}
              className="shrink-0 max-w-[8rem] truncate text-left text-muted hover:text-brand"
              title={tr("dock.figur_waehlen", { name: name(z.id) })}>
              {name(z.id)}
            </button>
            <span className="shrink-0 font-mono">{z.tool}</span>
            <span className="min-w-0 flex-1 truncate text-muted" title={z.target}>{z.target ?? ""}</span>
            <span className="shrink-0 text-muted">{z.ok === undefined ? "—" : dauerText(z.dauer)}</span>
          </div>
        );
      })}
    </div>
  );
}
