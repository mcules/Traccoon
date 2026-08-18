// Schicht 2 — die Kopfzeile des „Büros": Summen, Live-Zustand, Geschwindigkeit, Sitzungsreiter.
//
// Sie ist außerdem die Heimat der Formatierhilfen, die Dock und Inspektor mitbenutzen
// (`zahl`, `tokenText`, `usdText`, `dauerText`, `uhrText`, `ST_FARBE`, `ST_TEXT`). Das ist kein
// Zufall und keine Bequemlichkeit: Welle J besteht aus genau vier Dateien, eine fünfte für
// „gemeinsame Kleinigkeiten" wäre eine Datei mehr im Vertrag. Wichtiger ist, dass es **eine**
// Stelle gibt — zwei Dollar-Formatierungen driften garantiert auseinander.
//
// ── Die zwei Entscheidungen, die man hier sehen muss ────────────────────────────────────────
//
// 1. **„≥" vor dem Betrag.** Traccoon kann heute nicht sagen, ob „0,00 $" heißt *dieses Modell
//    ist gratis* oder *für dieses Modell steht kein Preis im Katalog*. `cost_partial` ist genau
//    diese Unterscheidung; das „≥" ist der einzige ehrliche Weg, sie anzuzeigen, ohne eine Zahl
//    zu erfinden. Der `title` sagt dazu, warum.
// 2. **Sitzungsreiter sind ein Filter, kein Kanalwechsel.** Traccoon kennt keine „Sessions" —
//    eine Sitzung ist hier ein Ticket (bzw. ein Laufbaum). Die Auswahl dimmt Figuren,
//    die nicht dazugehören; sie entfernt sie **nicht**. Entfernen verschöbe die Sitzplatzvergabe
//    (`hash32(runId) % 12` bleibt zwar, aber der Raum bliebe halb leer und die Übergaben zeigten
//    ins Nichts) und machte den Replay unrund.
// 3. **`kiosk` ist ein Schalter, keine zweite Kopfzeile.** Der Wandschirm zeigt dieselben
//    Summen, dieselbe Pille, denselben Fehlerkasten — nur ohne alles Anklickbare.



import { tr } from "../../i18n";
import type { Scope } from "./api.ts";
import type { GateKind, Roster, RosterEntry, RunStatus } from "./types.ts";
import type { FeedTotals } from "./useOfficeFeed.ts";

// ── Gates ───────────────────────────────────────────────────────────────────────────────────

/** Warum jemand wartet, in Klartext. Der rohe `blocker_kind` des Backends (`ask_human`,
 *  `permission`, `assistant_perm`, `review`) ist schon in `mapEvent` zu diesen drei Arten
 *  verdichtet — hier steht nur noch, wie sie heißen. */
export const GATE_TEXT: Record<GateKind, string> = {
  question: "buero.gate_question",
  permission: "buero.gate_permission",
  plan: "buero.gate_plan",
};

// ── Statusfarben ────────────────────────────────────────────────────────────────────────────

/** **Wörtlich** aus `components/AgentMonitor.tsx:5-8`. Zwei Ansichten desselben Laufs dürfen
 *  sich nicht widersprechen — wer hier eine Farbe „verbessert", erzeugt genau das.
 *  `loop_exhausted` fehlt dort und bekommt deshalb auch hier nur den Rückfall `text-muted`. */
export const ST_FARBE: Record<string, string> = {
  running: "text-yellow-400", success: "text-green-400", done: "text-green-400",
  failed: "text-red-400", blocked: "text-orange-400", planned: "text-sky-400",
};

/** Dieselben Zustände als Schlüssel; übersetzt wird in statusText(). */
export const ST_TEXT: Record<string, string> = {
  running: "buero.st_running", success: "buero.st_success", failed: "buero.st_failed",
  blocked: "buero.st_blocked", planned: "buero.st_planned",
  loop_exhausted: "buero.st_loop_exhausted",
};

export function statusText(s: RunStatus | string | null | undefined): string {
  if (!s) return tr("buero.st_unbekannt");
  return ST_TEXT[s] ? tr(ST_TEXT[s]) : s;
}

export function statusFarbe(s: RunStatus | string | null | undefined): string {
  return (s && ST_FARBE[s]) || "text-muted";
}

// ── Zahlen ──────────────────────────────────────────────────────────────────────────────────

/** Ganzzahl mit deutschen Tausenderpunkten. Von Hand, damit dieselbe Zahl in jedem Browser
 *  gleich aussieht — `toLocaleString()` ohne Sprachangabe folgt der Browsereinstellung. */
export function zahl(n: number): string {
  const s = Math.round(Math.abs(n)).toString();
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 === 0) out += ".";
    out += s[i];
  }
  return (n < 0 ? "−" : "") + out;
}

function komma(v: number, dez: number): string {
  return v.toFixed(dez).replace(".", ",");
}

/** Tokens kurz: unter 10 000 genau, darüber gerundet. Die genaue Zahl steht im `title`. */
export function tokenText(n: number): string {
  if (n < 10_000) return zahl(n);
  if (n < 1_000_000) return `${komma(n / 1000, 1)} Tsd.`;
  return `${komma(n / 1_000_000, 2)} Mio.`;
}

/** Dollarbetrag im Format des restlichen Repos (`Dashboard`, `AgentMonitor`: `$0.0123`).
 *  Bewusst **nicht** eingedeutscht: dieselbe Zahl steht an drei anderen Stellen der Anwendung
 *  mit Dezimalpunkt, und zwei Schreibweisen für denselben Betrag sind schlimmer als eine
 *  fremdsprachige. `unvollstaendig` stellt das „≥" davor. */
export function usdText(v: number, unvollstaendig?: boolean): string {
  return `${unvollstaendig ? "≥ " : ""}$${(v || 0).toFixed(4)}`;
}

/** Dauer in der Schreibweise von `AgentMonitor.fmtDauer`. `null` = unbekannt. */
export function dauerText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  // Unter einer Sekunde in Millisekunden: Werkzeugaufrufe sind oft schneller als das, und
  // „0s" verschweigt den Unterschied zwischen „sehr schnell" und „gar nicht gemessen".
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function zwei(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** `HH:MM:SS` in Ortszeit. Schicht 2 ist die einzige Schicht, die die Zeitzone kennen darf —
 *  und der Rest der Anwendung zeigt Zeiten ebenfalls örtlich (`lib/formatTime.ts`). */
export function uhrText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—:—:—";
  const d = new Date(ms);
  return `${zwei(d.getHours())}:${zwei(d.getMinutes())}:${zwei(d.getSeconds())}`;
}

// ── Sitzungsreiter ──────────────────────────────────────────────────────────────────────────

/** Ohne Ticket bzw. ohne Projekt — ein eigener Reiter statt „verschwindet aus der Liste".
 *  Job- und Assistentenläufe sind echte Bewohner des Raums. */
export const OHNE_TICKET = "(ohne Ticket)";   // Gruppierungsschlüssel, kein Anzeigetext
export const OHNE_PROJEKT = "(ohne Projekt)";

/** Zu welchem Reiter ein Lauf gehört: im Projektumfang sein Ticket, global sein Projekt. */
export function sitzungsSchluessel(scope: Scope, r: RosterEntry): string {
  return scope.kind === "project"
    ? (r.issue_key || OHNE_TICKET)
    : (r.project_key || OHNE_PROJEKT);
}

/** `null` = „Alle". Wird von Bühne, Dock und Inspektor mitbenutzt, damit „gedimmt" überall
 *  dasselbe heißt. */
export function passtZumFilter(scope: Scope, r: RosterEntry, filter: string | null): boolean {
  return filter === null || sitzungsSchluessel(scope, r) === filter;
}

interface Reiter {
  key: string;
  laeuft: boolean;
  seit: number;
  anzahl: number;
}

function reiterAus(scope: Scope, roster: Roster): Reiter[] {
  const map = new Map<string, Reiter>();
  for (const r of roster) {
    const key = sitzungsSchluessel(scope, r);
    const seit = r.started_at ? Date.parse(r.started_at) : 0;
    const vorhanden = map.get(key);
    if (!vorhanden) {
      map.set(key, { key, laeuft: r.status === "running", seit: seit || 0, anzahl: 1 });
    } else {
      vorhanden.anzahl++;
      if (r.status === "running") vorhanden.laeuft = true;
      if (seit > vorhanden.seit) vorhanden.seit = seit;
    }
  }
  // Laufendes zuerst, danach das Jüngste — die Reihenfolge, in der man sie sucht.
  return [...map.values()].sort((a, b) =>
    (a.laeuft === b.laeuft ? b.seit - a.seit : (a.laeuft ? -1 : 1)));
}

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

/** 1× / 2× / 4×. Mehr ist bei einem Replay, der von vorn rechnet, keine Hilfe mehr. */
export type Tempo = 1 | 2 | 4;

export const TEMPI: readonly Tempo[] = [1, 2, 4];

export interface TopBarProps {
  scope: Scope;
  /** Titel der Sitzung (Ticket bzw. Laufbaum), falls bekannt. */
  titel?: string;
  roster: Roster;
  totals: FeedTotals;
  /** Socket offen **und** Backfill durch. */
  live: boolean;
  /** Angesprungene Position in Epoch-ms, `null` = Gegenwart. */
  seekTs: number | null;
  onBackToLive: () => void;
  speed: Tempo;
  onSpeedChange: (t: Tempo) => void;
  /** Sitzungsfilter, `null` = Alle. */
  filter: string | null;
  onFilterChange: (f: string | null) => void;
  /** Ist die Ansicht bereits die Vollbildseite? Dann heißt der Knopf „Verlassen". */
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
  /** Wandschirm: alles Bedienbare fällt weg, die Kopfzeile bleibt eine reine Anzeige.
   *  **Keine zweite Komponente** dafür — zwei Kopfzeilen drifteten garantiert auseinander,
   *  und die Frage „warum zeigt der Kiosk andere Summen als der Tab" will niemand haben. */
  kiosk?: boolean;
  /** Fehlermeldung des Feeds — sie gehört sichtbar nach oben, nicht in die Konsole. */
  error?: string;
}

// ── Die Komponente ──────────────────────────────────────────────────────────────────────────

export default function TopBar({
  scope, titel, roster, totals, live, seekTs, onBackToLive,
  speed, onSpeedChange, filter, onFilterChange,
  fullscreen, onToggleFullscreen, error, kiosk,
}: TopBarProps) {
  const reiter = reiterAus(scope, roster);
  const tokenSumme = totals.in_tokens + totals.out_tokens;
  const tokenTitel = tr("buero.token_titel", {
    ein: zahl(totals.in_tokens), aus: zahl(totals.out_tokens),
    cache: zahl(totals.cache_read_tokens) });
  const kostenTitel = tr(totals.cost_partial ? "buero.kosten_teilweise" : "buero.kosten_geschaetzt",
    { abgerechnet: usdText(totals.cost_usd_billed) });

  return (
    <div className="rounded border border-line bg-card px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        {titel && (
          <span className="max-w-[22rem] truncate font-medium" title={titel}>🏢 {titel}</span>
        )}

        <span className="text-muted" title={tokenTitel}>
          🔢 <b className="text-ink">{tokenText(tokenSumme)}</b> {tr("buero.tokens")}
        </span>

        <span className="text-muted" title={kostenTitel}>
          💵 <b className="text-ink">{usdText(totals.cost_usd_estimated, totals.cost_partial)}</b>
        </span>

        <span className="text-muted"
          title={tr("buero.laufen_gerade", { laufend: totals.running, gesamt: totals.runs })}>
          🤖 <b className="text-ink">{totals.runs}</b> {tr(totals.runs === 1 ? "agent_monitor.lauf" : "agent_monitor.laeufe")}
          {totals.running > 0 && <span className="text-yellow-400"> · {tr("buero.aktiv", { anzahl: totals.running })}</span>}
        </span>

        <div className="flex-1" />

        {/* Live- bzw. Wiedergabe-Pille */}
        {seekTs !== null ? (
          <span className="flex items-center gap-2">
            <span className="rounded-full border border-orange-400/50 bg-orange-400/10 px-2 py-0.5 text-xs text-orange-400">
              ⏪ {tr("buero.wiedergabe")} {uhrText(seekTs)}
            </span>
            {!kiosk && (
              <button type="button" onClick={onBackToLive}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">
                {tr("buero.zurueck_zu_live")}
              </button>
            )}
          </span>
        ) : live ? (
          <span className="rounded-full border border-green-400/50 bg-green-400/10 px-2 py-0.5 text-xs text-green-400"
            title={tr("buero.strom_haengt")}>
            ● Live
          </span>
        ) : (
          <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted"
            title={tr("buero.kein_strom")}>
            ○ Getrennt
          </span>
        )}

        {/* Geschwindigkeit */}
        {!kiosk && (
          <span role="group" aria-label="Geschwindigkeit" className="flex overflow-hidden rounded border border-line">
            {TEMPI.map((t) => (
              <button key={t} type="button" onClick={() => onSpeedChange(t)}
                aria-pressed={speed === t}
                title={`Wiedergabe in ${t}-facher Geschwindigkeit`}
                className={"px-2 py-0.5 text-xs "
                  + (speed === t ? "bg-brand text-white" : "text-muted hover:bg-surface")}>
                {t}×
              </button>
            ))}
          </span>
        )}

        {!kiosk && onToggleFullscreen && (
          <button type="button" onClick={onToggleFullscreen}
            className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand"
            title={tr(fullscreen ? "buero.zurueck_reiter" : "buero.ganze_seite")}>
            {fullscreen ? "⤡ Vollbild verlassen" : "⤢ Vollbild"}
          </button>
        )}
      </div>

      {/* Sitzungsreiter — ein Filter auf den Roster, kein zweites Log.
          Im Kiosk weg: ein Filter, den niemand umstellen kann, ist eine Behauptung über
          den Raum, keine Bedienung. */}
      {!kiosk && reiter.length > 1 && (
        <div className="mt-2 flex gap-1 overflow-x-auto pb-0.5" role="group"
          aria-label={tr(scope.kind === "project" ? "buero.tickets_ansicht" : "buero.projekte_ansicht")}>
          <ReiterKnopf aktiv={filter === null} onClick={() => onFilterChange(null)}
            titel={tr("buero.alle_figuren")}>
            {tr("buero.alle")}
          </ReiterKnopf>
          {reiter.map((r) => (
            <ReiterKnopf key={r.key} aktiv={filter === r.key}
              onClick={() => onFilterChange(filter === r.key ? null : r.key)}
              titel={`${r.anzahl} ${tr(r.anzahl === 1 ? "agent_monitor.lauf" : "agent_monitor.laeufe")}`
                + (r.laeuft ? `, ${tr("buero.davon_laeuft_einer")}` : "")
                + ` — ${tr("buero.andere_gedimmt")}`}>
              {r.laeuft && <span className="mr-1 text-yellow-400">●</span>}
              {r.key}
            </ReiterKnopf>
          ))}
        </div>
      )}

      {error && (
        <div className="mt-2 rounded border border-red-400/40 bg-red-400/5 px-2 py-1 text-xs text-red-400">
          {error}
        </div>
      )}
    </div>
  );
}

function ReiterKnopf({ aktiv, onClick, titel, children }: {
  aktiv: boolean; onClick: () => void; titel: string; children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={aktiv} title={titel}
      className={"shrink-0 whitespace-nowrap rounded border px-2 py-0.5 font-mono text-xs "
        + (aktiv ? "border-brand bg-brand/10 text-brand" : "border-line text-muted hover:border-brand")}>
      {children}
    </button>
  );
}
