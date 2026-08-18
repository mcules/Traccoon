// Schicht 2 — die Personalakte: was eine **Rolle** über alle ihre Läufe hinweg getan hat.
//
// ══ Warum das ein eigener Dock-Reiter ist und nicht in den Inspektor gehört ═══════════════════
//
// Der Inspektor sagt in seinem eigenen Kopf zu, ohne eigene Abfrage auszukommen — alles darin
// stammt aus Roster und Log —, und er sitzt in einer 45 %-hohen Kachel. Die Akte ist das
// Gegenteil: ein Aggregat über Läufe **und** Sitzungen hinweg, mit eigenem Zeitfenster, eigenem
// Lade- und eigenem Fehlerzustand. Sie in den Inspektor zu quetschen hieße, ihm eine Abfrage
// unterzuschieben, die er ausdrücklich nicht haben wollte.
//
// Ein Klick auf eine Figur setzt `selectedId`. Der Inspektor zeigt daraufhin weiter den
// **einzelnen Lauf**, die Akte springt zur **Rolle** dieser Figur. Beide Wahrheiten stehen
// nebeneinander, und keine gibt sich für die andere aus.
//
// ══ Die Ehrlichkeit ist der ganze Punkt ══════════════════════════════════════════════════════
//
//   1. **Drei Balken, keine Erfolgsquote.** *Abgeliefert* · *wartet auf einen Menschen* ·
//      *abgebrochen*. Die drei Zahlen kommen **fertig vom Server**; hier wird nichts aus
//      `success / runs` gerechnet. Genau daran hängt, ob `architect` 78 % oder 6 % zeigt und
//      `project_manager` 64 % oder 0 %: `planned` ist ein abgelieferter Plan, `blocked` ist ein
//      Mensch, der nicht geantwortet hat — beides ist kein Fehlschlag der Rolle.
//   2. **Farben wörtlich aus `TopBar.tsx`**, die sie ihrerseits wörtlich aus `AgentMonitor.tsx`
//      hat. Zwei Ansichten desselben Laufs dürfen sich nie widersprechen.
//   3. **Kosten mit „≥".** Jede Kostenzeile im Bestand ist unbepreist; ein Betrag ohne Zeichen
//      behauptete eine Genauigkeit, die es nicht gibt.
//   4. **Runden ≠ Schritte.** Ø 6,9 Runden der Agentenschleife gegen Ø 21,5 Schritte im
//      Ereignisstrom — zwei verschiedene Dinge, also zwei verschiedene Beschriftungen.
//   5. **Dauer als Median/p90/max plus Histogramm**, nie als Mittelwert: ein Lauf über 36,5
//      Stunden macht jeden Durchschnitt zur Karikatur.
//   6. **Das Fenster wird benannt.** `run_retention_days` löscht ältere Läufe — „Lieblingswerk-
//      zeuge" heißt „der letzten 30 Tage", nicht „jemals".
//
// ══ Keine Diagrammbibliothek ═════════════════════════════════════════════════════════════════
//
// Alles hier sind `div`s mit Prozentbreiten, genau wie die Zeitleiste ihre Balken malt. Das
// Dockerfile macht bei jedem Bau `npm install` **ohne** Lockfile — eine neue Abhängigkeit wäre
// ein Risiko für den Bau und kaufte drei gestapelte Rechtecke.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { officeApi, type AgentRecord, type Scope } from "./api.ts";
import { dauerText, statusFarbe, statusText, tokenText, usdText, zahl } from "./TopBar.tsx";

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** Die wählbaren Fenster. Der lange Text steht daneben, weil er in der Überschrift landet:
 *  eine Kennzahl ohne ihr Fenster ist keine Kennzahl. 30 Tage sind der Standardwert von
 *  `run_retention_days` — darüber hinaus gibt es schlicht keine Läufe mehr, und ein Fenster,
 *  das mehr verspricht, als die Aufbewahrung hergibt, wäre eine leere Zusage. */
const FENSTER: readonly { h: number; kurz: string; lang: string }[] = [
  { h: 24, kurz: "24 h", lang: "der letzten 24 Stunden" },
  { h: 24 * 7, kurz: "7 Tage", lang: "der letzten 7 Tage" },
  { h: 24 * 30, kurz: "30 Tage", lang: "der letzten 30 Tage" },
];

/** Vorgabe: das ganze Aufbewahrungsfenster. Die Akte ist ein Rückblick, kein Wecker. */
const FENSTER_STD = 24 * 30;

/** So viele Werkzeuge je Rolle holt und zeigt die Rangliste. Darunter beginnt der lange
 *  Schwanz aus Einmalaufrufen, den niemand liest. */
const WERKZEUG_LIMIT = 8;

/** Ein Aggregat über Wochen ändert sich nicht im Sekundentakt. */
const AKTE_REFETCH_MS = 60_000;

// ── Farben ──────────────────────────────────────────────────────────────────────────────────

/** Textfarbe → Flächenfarbe.
 *
 *  Der **Schlüssel** ist wörtlich das, was `statusFarbe` liefert; der Wert ist dieselbe Farbe
 *  als Fläche. Zwei Gründe für diesen Umweg statt eines `replace("text-", "bg-")`:
 *  Tailwind liest Klassennamen als Text aus dem Quelltext — ein zur Laufzeit
 *  zusammengesetzter Name existierte im fertigen CSS gar nicht. Und driftet `ST_FARBE`
 *  eines Tages, fehlt hier der Eintrag: der Balken wird grau statt still falsch. */
const FLAECHE: Record<string, string> = {
  "text-green-400": "bg-green-400",
  "text-orange-400": "bg-orange-400",
  "text-red-400": "bg-red-400",
  "text-yellow-400": "bg-yellow-400",
  "text-sky-400": "bg-sky-400",
};

function flaeche(status: string): string {
  return FLAECHE[statusFarbe(status)] ?? "bg-line";
}

// ── Die drei Balken ─────────────────────────────────────────────────────────────────────────
//
// `status` ist hier **kein** Filter, sondern nur die Quelle der Farbe: die Zahlen selbst
// rechnet der Server. Steht `success` grün im Agenten-Monitor, ist „abgeliefert" hier
// dasselbe Grün — sonst sähe derselbe Lauf in zwei Ansichten verschieden aus.

interface BalkenArt {
  key: "delivered" | "waiting" | "aborted";
  label: string;
  /** Für die Farbe, siehe oben. */
  status: string;
  titel: string;
}

const BALKEN: readonly BalkenArt[] = [
  {
    key: "delivered", label: "abgeliefert", status: "success",
    titel: "Erfolgreich beendet oder ein fertiger Plan (`success` + `planned`). Ein Plan, der "
      + "auf Freigabe wartet, ist Arbeit, die abgeliefert wurde — kein Fehlschlag.",
  },
  {
    key: "waiting", label: "wartet auf einen Menschen", status: "blocked",
    titel: "Der Lauf hängt an einer Frage, einer Berechtigung oder einer Freigabe (`blocked`). "
      + "Das ist kein Fehler des Agenten, sondern eine offene Zusage von uns.",
  },
  {
    key: "aborted", label: "abgebrochen", status: "failed",
    titel: "Fehlgeschlagen oder in der Schleife steckengeblieben (`failed` + `loop_exhausted`) — "
      + "die Fälle, in denen die Rolle wirklich nicht geliefert hat.",
  },
];

// ── Zahlen ──────────────────────────────────────────────────────────────────────────────────

/** Eine Nachkommastelle mit deutschem Komma. Von Hand statt `toLocaleString`, damit dieselbe
 *  Zahl in jedem Browser gleich aussieht — dieselbe Begründung wie bei `zahl` in `TopBar`. */
function komma1(v: number): string {
  return v.toFixed(1).replace(".", ",");
}

function prozent(teil: number, ganzes: number): number {
  return ganzes > 0 ? (teil / ganzes) * 100 : 0;
}

/** Wie lange her, grob. Der genaue Zeitpunkt steht im `title`. */
function herText(iso: string | null | undefined, jetzt: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const d = Math.max(0, jetzt - t);
  const min = Math.round(d / 60_000);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} min`;
  const std = Math.round(min / 60);
  if (std < 48) return `vor ${std} h`;
  return `vor ${Math.round(std / 24)} Tagen`;
}

/** Beschriftung eines Histogramm-Eimers. `lt_ms` ist die **obere** Grenze; fehlt sie, ist es
 *  der offene Eimer ganz oben. */
function eimerText(lt: number | null | undefined, vorher: number | null): string {
  if (lt === null || lt === undefined || !Number.isFinite(lt)) {
    return vorher !== null ? `> ${dauerText(vorher)}` : "darüber";
  }
  return vorher !== null ? `${dauerText(vorher)} – ${dauerText(lt)}` : `< ${dauerText(lt)}`;
}

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

export interface PersonalakteProps {
  scope: Scope;
  /** Rolle, die aufgeklappt und angesteuert werden soll — die Rolle der ausgewählten Figur.
   *  `null`/fehlt = niemand ist ausgewählt, dann bleibt die Wahl beim Betrachter. Ein Wechsel
   *  klappt die neue Rolle auf; das Zuklappen von Hand bleibt danach bestehen. */
  focusAgent?: string | null;
  /** Vorgewähltes Zeitfenster in Stunden. Nur beim Einhängen gelesen. */
  initialSinceHours?: number;
  className?: string;
}

// ── Die Komponente ──────────────────────────────────────────────────────────────────────────

export default function Personalakte({
  scope, focusAgent, initialSinceHours, className,
}: PersonalakteProps): JSX.Element {
  const [stunden, setStunden] = useState<number>(initialSinceHours ?? FENSTER_STD);
  const [offen, setOffen] = useState<string | null>(focusAgent ?? null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const akte = useQuery({
    queryKey: ["office", "agents", scopeKey, stunden],
    queryFn: () => officeApi.agents(scope, { sinceHours: stunden, toolLimit: WERKZEUG_LIMIT }),
    refetchInterval: AKTE_REFETCH_MS,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: 1,
  });

  // Die gewählte Figur bestimmt die aufgeklappte Rolle — und holt sie ins Bild. Ohne das
  // Scrollen zeigte die Akte bei zwölf Rollen zwar die richtige, aber unterhalb des Randes.
  useEffect(() => {
    if (!focusAgent) return;
    setOffen(focusAgent);
    boxRef.current
      ?.querySelector<HTMLElement>(`[data-rolle="${CSS.escape(focusAgent)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [focusAgent]);

  const gemessen = akte.data?.since_hours ?? stunden;
  const fensterText = FENSTER.find((f) => f.h === gemessen)?.lang
    ?? `der letzten ${Math.round(gemessen / 24)} Tage`;

  // Reihenfolge: die vielbeschäftigte Rolle zuerst, bei Gleichstand die zuletzt aktive. Das
  // ist die Reihenfolge, in der man hinsieht — und sie ist stabil, weil sie nicht davon
  // abhängt, wer gerade ausgewählt ist.
  const rollen = useMemo(() => {
    const kopie = [...(akte.data?.agents ?? [])];
    kopie.sort((a, b) => {
      const d = (b.runs ?? 0) - (a.runs ?? 0);
      if (d !== 0) return d;
      const ta = a.last_run_at ? Date.parse(a.last_run_at) : 0;
      const tb = b.last_run_at ? Date.parse(b.last_run_at) : 0;
      if (tb !== ta) return tb - ta;
      return a.agent < b.agent ? -1 : 1;
    });
    return kopie;
  }, [akte.data]);

  const jetzt = Date.now();

  return (
    <div ref={boxRef} className={`space-y-2 ${className ?? ""}`}>
      {/* Kopf: das Fenster ist Teil der Aussage, also steht es ganz oben und ist umstellbar. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line pb-1.5 text-[11px]">
        <span className="text-muted">
          📇 Kennzahlen je Rolle, <b className="text-ink">{fensterText}</b>
        </span>
        <div className="flex-1" />
        <span role="group" aria-label="Zeitfenster" className="flex overflow-hidden rounded border border-line">
          {FENSTER.map((f) => (
            <button key={f.h} type="button" onClick={() => setStunden(f.h)}
              aria-pressed={stunden === f.h}
              title={`Kennzahlen ${f.lang}`}
              className={"px-1.5 py-0.5 " + (stunden === f.h ? "bg-brand text-white" : "text-muted hover:bg-surface")}>
              {f.kurz}
            </button>
          ))}
        </span>
      </div>

      <p className="text-[11px] text-muted">
        Ältere Läufe löscht die Aufbewahrung (<span className="font-mono">run_retention_days</span>),
        deshalb steht hier nie „jemals", sondern immer ein Fenster.
      </p>

      {akte.isLoading && <div className="py-4 text-center text-xs text-muted">Akte wird geladen…</div>}

      {akte.error && (
        <div className="rounded border border-red-400/40 bg-red-400/5 px-2 py-1 text-xs text-red-400">
          Akte nicht ladbar: {(akte.error as Error).message}
        </div>
      )}

      {!akte.isLoading && !akte.error && rollen.length === 0 && (
        <div className="py-4 text-center text-xs text-muted">
          Kein Lauf {fensterText}.
        </div>
      )}

      {rollen.map((r) => (
        <Rollenkarte key={r.agent} r={r} jetzt={jetzt}
          offen={offen === r.agent}
          onToggle={() => setOffen((v) => (v === r.agent ? null : r.agent))} />
      ))}
    </div>
  );
}

// ── Eine Rolle ──────────────────────────────────────────────────────────────────────────────

function Rollenkarte({ r, jetzt, offen, onToggle }: {
  r: AgentRecord;
  jetzt: number;
  offen: boolean;
  onToggle: () => void;
}) {
  const runs = r.runs ?? 0;
  const werte: Record<BalkenArt["key"], number> = {
    delivered: r.delivered ?? 0,
    waiting: r.waiting ?? 0,
    aborted: r.aborted ?? 0,
  };
  const beurteilt = werte.delivered + werte.waiting + werte.aborted;
  // Was übrig bleibt, läuft noch (oder hat einen Status, den der Server keiner der drei
  // Gruppen zuordnet). Es bekommt einen eigenen, grauen Rest — es unter die drei zu
  // verteilen wäre genau die Sorte stiller Rechnung, die diese Ansicht vermeiden soll.
  const rest = Math.max(0, runs - beurteilt);
  const basis = beurteilt + rest;

  return (
    <div data-rolle={r.agent}
      className={"rounded border px-2 py-1.5 " + (offen ? "border-brand bg-brand/5" : "border-line")}>
      <button type="button" onClick={onToggle} aria-expanded={offen}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 text-left text-xs">
        <span className="shrink-0 text-[11px] text-muted">{offen ? "▾" : "▸"}</span>
        <span className="font-medium">{r.agent}</span>
        <span className="text-muted">{zahl(runs)} {runs === 1 ? "Lauf" : "Läufe"}</span>
        {(r.running ?? 0) > 0 && (
          <span className="text-yellow-400" title={`${r.running} laufen gerade`}>
            ● {zahl(r.running ?? 0)} aktiv
          </span>
        )}
        <div className="flex-1" />
        <span className="text-muted" title="Letzter Lauf dieser Rolle im gewählten Fenster.">
          {herText(r.last_run_at, jetzt)}
        </span>
      </button>

      <Balkenband werte={werte} rest={rest} basis={basis} />

      {offen && <Details r={r} basis={basis} werte={werte} rest={rest} />}
    </div>
  );
}

// ── Das Balkenband ──────────────────────────────────────────────────────────────────────────
//
// Ein gestapelter Balken, drei `div`s mit Prozentbreiten (plus dem grauen Rest). Die Zahlen
// stehen daneben — ein Balken allein sagt „ungefähr ein Drittel", und ungefähr ist bei
// zwölf Läufen zu wenig.

function Balkenband({ werte, rest, basis }: {
  werte: Record<BalkenArt["key"], number>;
  rest: number;
  basis: number;
}) {
  return (
    <div className="mt-1">
      <div className="flex h-2 w-full overflow-hidden rounded-sm bg-line/40">
        {BALKEN.map((b) => {
          const n = werte[b.key];
          if (n <= 0) return null;
          return (
            <div key={b.key} className={flaeche(b.status)}
              style={{ width: `${prozent(n, basis)}%` }}
              title={`${b.label}: ${zahl(n)} von ${zahl(basis)}`} />
          );
        })}
        {rest > 0 && (
          <div className="bg-line" style={{ width: `${prozent(rest, basis)}%` }}
            title={`läuft noch oder ohne Urteil: ${zahl(rest)} von ${zahl(basis)}`} />
        )}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0 text-[11px]">
        {BALKEN.map((b) => (
          <span key={b.key} className={werte[b.key] > 0 ? statusFarbe(b.status) : "text-muted"}
            title={b.titel}>
            {b.label} {zahl(werte[b.key])}
            <span className="text-muted"> ({Math.round(prozent(werte[b.key], basis))} %)</span>
          </span>
        ))}
        {rest > 0 && (
          <span className="text-muted" title="Läufe ohne abschließendes Urteil — meist noch laufend.">
            läuft noch {zahl(rest)}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Der aufgeklappte Teil ───────────────────────────────────────────────────────────────────

function Details({ r, basis, werte, rest }: {
  r: AgentRecord;
  basis: number;
  werte: Record<BalkenArt["key"], number>;
  rest: number;
}) {
  const status = r.by_status ?? {};
  const statusZeilen = Object.entries(status).filter(([, n]) => n > 0);
  const dauer = r.duration ?? {};
  const eimer = dauer.buckets ?? [];
  const eimerMax = eimer.reduce((m, b) => Math.max(m, b.n ?? 0), 0);
  const werkzeuge = r.tools ?? [];
  const werkzeugMax = werkzeuge.reduce((m, t) => Math.max(m, t.n ?? 0), 0);
  // `!== false` und nicht `=== true`: fehlt das Feld, ist unbekannt, ob alles bepreist war —
  // und unbekannt gehört auf die Seite der Untergrenze. Ein Betrag ohne „≥" wäre eine
  // Genauigkeitsbehauptung, die niemand geprüft hat.
  const unbepreist = r.cost_partial !== false;

  return (
    <div className="mt-2 space-y-2 border-t border-line pt-2 text-[11px]">
      {/* Woher die drei Balken kommen — roh, unverdichtet, zum Nachrechnen. */}
      {statusZeilen.length > 0 && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5"
          title={"Die rohen Laufstatus. Aus ihnen bildet der Server die drei Balken darüber — "
            + "hier steht, was er gezählt hat."}>
          {statusZeilen.map(([s, n]) => (
            <span key={s} className={statusFarbe(s)}>
              {statusText(s)} <b className="text-ink">{zahl(n)}</b>
            </span>
          ))}
        </div>
      )}

      {/* Runden und Schritte — getrennt beschriftet, weil es zwei verschiedene Dinge sind. */}
      <dl className="grid grid-cols-[7.5rem_1fr] gap-x-2 gap-y-1">
        <Feld label="Runden"
          titel={"Durchläufe der Agentenschleife (runs.iterations) — wie oft das Modell erneut "
            + "gefragt wurde. Im Bestand im Schnitt 6,9."}>
          Ø {komma1(r.iterations_avg ?? 0)} · max {zahl(r.iterations_max ?? 0)}
        </Feld>
        <Feld label="Schritte"
          titel={"Zeilen im Ereignisstrom (run_steps) — Werkzeugaufrufe, Nachrichten, "
            + "Bearbeitungen. Im Bestand im Schnitt 21,5, also etwas völlig anderes als eine Runde."}>
          Ø {komma1(r.steps_avg ?? 0)} · max {zahl(r.steps_max ?? 0)}
        </Feld>
        <Feld label="Tokens"
          titel={`Cache gelesen ${zahl(r.cache_read_tokens ?? 0)}`}>
          {tokenText((r.in_tokens ?? 0) + (r.out_tokens ?? 0))}
          <span className="text-muted">
            {" "}({zahl(r.in_tokens ?? 0)} ein · {zahl(r.out_tokens ?? 0)} aus)
          </span>
        </Feld>
        <Feld label="Kosten"
          titel={unbepreist
            ? "Für mindestens einen Posten fehlt ein Preis im Katalog (oder die Zeile stammt aus "
              + "der Zeit davor). Der Betrag ist eine Untergrenze — der wahre liegt darüber."
            : "Vollständig gegen den Preiskatalog abgerechnet."}>
          {usdText(r.cost_usd ?? 0, unbepreist)}
        </Feld>
        <Feld label="Läufe"
          titel="Grundgesamtheit der drei Balken.">
          {zahl(basis)}
          <span className="text-muted">
            {" "}({zahl(werte.delivered)} + {zahl(werte.waiting)} + {zahl(werte.aborted)}
            {rest > 0 ? ` + ${zahl(rest)} laufend` : ""})
          </span>
        </Feld>
      </dl>

      {/* Dauer: Median, p90, Maximum — und die Verteilung darunter. */}
      <div>
        <div className="mb-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">Dauer</span>
          <span className="text-muted" title="Die Hälfte aller Läufe war schneller als das.">
            Median <b className="text-ink">{dauerText(dauer.p50_ms)}</b>
          </span>
          <span className="text-muted" title="Neun von zehn Läufen waren schneller als das.">
            p90 <b className="text-ink">{dauerText(dauer.p90_ms)}</b>
          </span>
          <span className="text-muted" title="Der längste Lauf im Fenster.">
            max <b className="text-ink">{dauerText(dauer.max_ms)}</b>
          </span>
        </div>
        <p className="mb-1 text-[11px] text-muted">
          Kein Mittelwert: ein einzelner Lauf über 36 Stunden verschöbe ihn so weit, dass er
          über keinen einzigen echten Lauf mehr etwas aussagt.
        </p>
        {eimer.length === 0 ? (
          <div className="text-muted">Keine Verteilung im Fenster.</div>
        ) : (
          <div className="space-y-0.5">
            {eimer.map((b, i) => {
              const n = b.n ?? 0;
              const label = eimerText(b.lt_ms, i > 0 ? (eimer[i - 1].lt_ms ?? null) : null);
              return (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="w-[6.5rem] shrink-0 text-right text-muted">{label}</span>
                  <span className="h-2 min-w-0 flex-1 rounded-sm bg-line/40">
                    <span className="block h-2 rounded-sm bg-sky-400"
                      style={{ width: `${prozent(n, eimerMax)}%` }} />
                  </span>
                  <span className="w-8 shrink-0 text-right text-muted">{zahl(n)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Werkzeuge: Rangliste mit Anzahl und Fehlschlägen. */}
      <div>
        <div className="mb-0.5 font-medium">Werkzeuge</div>
        {werkzeuge.length === 0 ? (
          <div className="text-muted">Kein Werkzeugaufruf im Fenster.</div>
        ) : (
          <div className="space-y-0.5">
            {werkzeuge.map((t) => (
              <div key={t.tool} className="flex items-center gap-1.5">
                <span className="w-[8rem] shrink-0 truncate font-mono text-[11px]" title={t.tool}>
                  {t.tool}
                </span>
                <span className="h-2 min-w-0 flex-1 rounded-sm bg-line/40">
                  <span className="block h-2 rounded-sm bg-sky-400"
                    style={{ width: `${prozent(t.n ?? 0, werkzeugMax)}%` }} />
                </span>
                <span className="w-8 shrink-0 text-right text-muted">{zahl(t.n ?? 0)}</span>
                <span className={"w-14 shrink-0 text-right " + ((t.failed ?? 0) > 0 ? "text-red-400" : "text-muted")}
                  title={(t.failed ?? 0) > 0
                    ? `${t.failed} Aufrufe schlugen fehl (${t.ok ?? 0} liefen durch)`
                    : "Kein gemeldeter Fehlschlag. Altdaten ohne gemessenes Ergebnis zählen "
                      + "weder als Erfolg noch als Fehlschlag."}>
                  {(t.failed ?? 0) > 0 ? `✕ ${zahl(t.failed ?? 0)}` : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Feld({ label, titel, children }: {
  label: string;
  titel?: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <dt className="text-muted" title={titel}>{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}
