// Layer 2, the header of the "office": totals, live state, speed, session tabs.
//
// It is also the home of the formatting helpers that dock and inspector use as well (`zahl`,
// `tokenText`, `usdText`, `dauerText`, `uhrText`, `ST_FARBE`, `ST_TEXT`). That is neither
// chance nor convenience: this wave consists of exactly four files, and a fifth one for
// "shared trifles" would be one file more in the contract. More importantly there should be
// **one** place: two dollar formattings are guaranteed to drift apart.
//
// ── The two decisions one has to see here ───────────────────────────────────────────────────
//
// 1. **"≥" in front of the amount.** Traccoon cannot say today whether "$0.00" means *this
//    model is free* or *there is no price for this model in the catalog*. `cost_partial` is
//    exactly that distinction, and the "≥" is the only honest way to show it without inventing
//    a number. The `title` says why.
// 2. **Session tabs are a filter, not a channel switch.** Traccoon knows no "sessions": a
//    session here is a ticket (respectively a run tree). The selection dims figures that do
//    not belong to it; it does **not** remove them. Removing would shift the seat allocation
//    (`hash32(runId) % 12` would stay, but the room would remain half empty and the handovers
//    would point into nothing) and make the replay uneven.
// 3. **`kiosk` is a switch, not a second header.** The wall screen shows the same totals, the
//    same pill, the same error box, only without anything clickable.



import { tr } from "../../i18n";
import type { Scope } from "./api.ts";
import type { GateKind, Roster, RosterEntry, RunStatus } from "./types.ts";
import type { FeedTotals } from "./useOfficeFeed.ts";
import { BUTTON_SMALL } from "../ui";

// ── Gates ───────────────────────────────────────────────────────────────────────────────────

/** Why somebody is waiting, in plain text. The raw `blocker_kind` of the backend
 *  (`ask_human`, `permission`, `assistant_perm`, `review`) is already condensed to these
 *  three kinds in `mapEvent`; here only their names stand. */
export const GATE_TEXT: Record<GateKind, string> = {
  question: "buero.gate_question",
  permission: "buero.gate_permission",
  plan: "buero.gate_plan",
};

// ── Statusfarben ────────────────────────────────────────────────────────────────────────────

/** **Verbatim** from `components/AgentMonitor.tsx:5-8`. Two views of the same run must not
 *  contradict each other, and whoever "improves" a colour here produces exactly that.
 *  `loop_exhausted` is missing there and therefore only gets the fallback `text-muted` here. */
export const ST_COLOR: Record<string, string> = {
  running: "text-yellow-400", success: "text-green-400", done: "text-green-400",
  failed: "text-red-400", blocked: "text-orange-400", planned: "text-sky-400",
};

/** The same states as keys; the translation happens in statusText(). */
export const ST_TEXT: Record<string, string> = {
  running: "buero.st_running", success: "buero.st_success", failed: "buero.st_failed",
  blocked: "buero.st_blocked", planned: "buero.st_planned",
  loop_exhausted: "buero.st_loop_exhausted",
};

export function statusText(s: RunStatus | string | null | undefined): string {
  if (!s) return tr("buero.st_unbekannt");
  return ST_TEXT[s] ? tr(ST_TEXT[s]) : s;
}

export function statusColor(s: RunStatus | string | null | undefined): string {
  return (s && ST_COLOR[s]) || "text-muted";
}

// ── Zahlen ──────────────────────────────────────────────────────────────────────────────────

/** Integer with German thousands separators. By hand, so that the same number looks the same
 *  in every browser: `toLocaleString()` without a language follows the browser setting. */
export function number(n: number): string {
  const s = Math.round(Math.abs(n)).toString();
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 === 0) out += ".";
    out += s[i];
  }
  return (n < 0 ? "−" : "") + out;
}

function comma(v: number, dec: number): string {
  return v.toFixed(dec).replace(".", ",");
}

/** Tokens in short: exact below 10 000, rounded above. The exact number stands in the `title`. */
export function tokenText(n: number): string {
  if (n < 10_000) return number(n);
  if (n < 1_000_000) return `${comma(n / 1000, 1)} Tsd.`;
  return `${comma(n / 1_000_000, 2)} Mio.`;
}

/** Dollar amount in the format of the rest of the repository (`Dashboard`, `AgentMonitor`:
 *  `$0.0123`). Deliberately **not** germanised: the same number stands in three other places
 *  of the application with a decimal point, and two notations for the same amount are worse
 *  than a foreign language one. `unvollstaendig` puts the "≥" in front. */
export function usdText(v: number, incomplete?: boolean): string {
  return `${incomplete ? "≥ " : ""}$${(v || 0).toFixed(4)}`;
}

/** Duration in the notation of `AgentMonitor.fmtDauer`. `null` = unknown. */
export function durationText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  // Below one second in milliseconds: tool calls are often faster than that, and "0s" hides
  // the difference between "very fast" and "not measured at all".
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function two(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** `HH:MM:SS` in local time. Layer 2 is the only layer that may know the time zone, and the
 *  rest of the application shows times locally as well (`lib/formatTime.ts`). */
export function uhrText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—:—:—";
  const d = new Date(ms);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

// ── Sitzungsreiter ──────────────────────────────────────────────────────────────────────────

/** Without a ticket respectively without a project: a tab of its own instead of "disappears
 *  from the list". Job and assistant runs are real inhabitants of the room. */
export const WITHOUT_TICKET = "(ohne Ticket)";   // Gruppierungsschlüssel, kein Anzeigetext
export const WITHOUT_PROJECT = "(ohne Projekt)";

/** Which tab a run belongs to: its ticket in the project scope, its project globally. */
export function sessionKey(scope: Scope, r: RosterEntry): string {
  return scope.kind === "project"
    ? (r.issue_key || WITHOUT_TICKET)
    : (r.project_key || WITHOUT_PROJECT);
}

/** `null` = "all". Used by stage, dock and inspector as well, so that "dimmed" means the same
 *  everywhere. */
export function fitsToFilter(scope: Scope, r: RosterEntry, filter: string | null): boolean {
  return filter === null || sessionKey(scope, r) === filter;
}

interface Tab {
  key: string;
  runs: boolean;
  since: number;
  count: number;
}

function tabFrom(scope: Scope, roster: Roster): Tab[] {
  const map = new Map<string, Tab>();
  for (const r of roster) {
    const key = sessionKey(scope, r);
    const since = r.started_at ? Date.parse(r.started_at) : 0;
    const existing = map.get(key);
    if (!existing) {
      map.set(key, { key, runs: r.status === "running", since: since || 0, count: 1 });
    } else {
      existing.count++;
      if (r.status === "running") existing.runs = true;
      if (since > existing.since) existing.since = since;
    }
  }
  // Running first, then the most recent: the order in which one looks for them.
  return [...map.values()].sort((a, b) =>
    (a.runs === b.runs ? b.since - a.since : (a.runs ? -1 : 1)));
}

// ── Interface ───────────────────────────────────────────────────────────────────────────────

/** 1x / 2x / 4x. More is no help with a replay that computes from the start. */
export type Pace = 1 | 2 | 4;

export const TEMPI: readonly Pace[] = [1, 2, 4];

export interface TopBarProps {
  scope: Scope;
  /** Title of the session (ticket respectively run tree), when known. */
  title?: string;
  roster: Roster;
  totals: FeedTotals;
  /** Socket open **and** backfill done. */
  live: boolean;
  /** Angesprungene Position in Epoch-ms, `null` = Gegenwart. */
  seekTs: number | null;
  onBackToLive: () => void;
  speed: Pace;
  onSpeedChange: (t: Pace) => void;
  /** Session filter, `null` = all. */
  filter: string | null;
  onFilterChange: (f: string | null) => void;
  /** Way into the full screen page. Absent on the page itself: leaving it happens over the
   *  area rail, which stays standing beside it, and a second way out would only be a second
   *  place to look for it. */
  onFullscreen?: () => void;
  /** Wall screen: everything operable falls away, the header stays a pure display.
   *  **No second component** for that: two headers would be guaranteed to drift apart, and
   *  nobody wants the question "why does the kiosk show different totals than the tab". */
  kiosk?: boolean;
  /** Error message of the feed; it belongs visibly at the top, not in the console. */
  error?: string;
}

// ── The component ───────────────────────────────────────────────────────────────────────────

export default function TopBar({
  scope, title: title, roster, totals, live, seekTs, onBackToLive,
  speed, onSpeedChange, filter, onFilterChange,
  onFullscreen, error, kiosk,
}: TopBarProps) {
  const tab = tabFrom(scope, roster);
  const tokenSum_total = totals.in_tokens + totals.out_tokens;
  const tokenTitle = tr("buero.token_titel", {
    inside: number(totals.in_tokens), from: number(totals.out_tokens),
    cache: number(totals.cache_read_tokens) });
  const costTitle = tr(totals.cost_partial ? "buero.kosten_teilweise" : "buero.kosten_geschaetzt",
    { abgerechnet: usdText(totals.cost_usd_billed) });

  return (
    <div className="rounded border border-line bg-card px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        {title && (
          <span className="max-w-[22rem] truncate font-medium" title={title}>🏢 {title}</span>
        )}

        <span className="text-muted" title={tokenTitle}>
          🔢 <b className="text-ink">{tokenText(tokenSum_total)}</b> {tr("buero.tokens")}
        </span>

        <span className="text-muted" title={costTitle}>
          💵 <b className="text-ink">{usdText(totals.cost_usd_estimated, totals.cost_partial)}</b>
        </span>

        <span className="text-muted"
          title={tr("buero.laufen_gerade", { running: totals.running, total: totals.runs })}>
          🤖 <b className="text-ink">{totals.runs}</b> {tr(totals.runs === 1 ? "agent_monitor.lauf" : "agent_monitor.laeufe")}
          {totals.running > 0 && <span className="text-yellow-400"> · {tr("buero.aktiv", { count: totals.running })}</span>}
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
                className={BUTTON_SMALL.secondary}>
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

        {!kiosk && onFullscreen && (
          <button type="button" onClick={onFullscreen}
            className={BUTTON_SMALL.secondary}
            title={tr("buero.ganze_seite")}>
            ⤢ {tr("buero.vollbild")}
          </button>
        )}
      </div>

      {/* Sitzungsreiter — ein Filter auf den Roster, kein zweites Log.
          Im Kiosk weg: ein Filter, den niemand umstellen kann, ist eine Behauptung über
          den Raum, keine Bedienung. */}
      {!kiosk && tab.length > 1 && (
        <div className="mt-2 flex gap-1 overflow-x-auto pb-0.5" role="group"
          aria-label={tr(scope.kind === "project" ? "buero.tickets_ansicht" : "buero.projekte_ansicht")}>
          <TabButton active={filter === null} onClick={() => onFilterChange(null)}
            title={tr("buero.alle_figuren")}>
            {tr("buero.alle")}
          </TabButton>
          {tab.map((r) => (
            <TabButton key={r.key} active={filter === r.key}
              onClick={() => onFilterChange(filter === r.key ? null : r.key)}
              title={`${r.count} ${tr(r.count === 1 ? "agent_monitor.lauf" : "agent_monitor.laeufe")}`
                + (r.runs ? `, ${tr("buero.davon_laeuft_einer")}` : "")
                + ` — ${tr("buero.andere_gedimmt")}`}>
              {r.runs && <span className="mr-1 text-yellow-400">●</span>}
              {r.key}
            </TabButton>
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

function TabButton({ active, onClick, title: title, children }: {
  active: boolean; onClick: () => void; title: string; children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} title={title}
      className={"shrink-0 whitespace-nowrap rounded border px-2 py-0.5 font-mono text-xs "
        + (active ? "border-brand bg-brand/10 text-brand" : "border-line text-muted hover:border-brand")}>
      {children}
    </button>
  );
}
