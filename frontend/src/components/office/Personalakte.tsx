// Layer 2, the personnel file: what a **role** has done across all its runs.
//
// ══ Why this is a dock tab of its own and does not belong in the inspector ═══════════════════
//
// The inspector promises in its own head to get by without a query of its own (everything in
// it comes from roster and log), and it sits in a tile 45 % high. The file is the opposite: an
// aggregate across runs **and** sessions, with a time window of its own, a loading state of
// its own and an error state of its own. Squeezing it into the inspector would mean slipping
// it a query it explicitly did not want.
//
// A click on a figure sets `selectedId`. The inspector then keeps showing the **single run**,
// the file jumps to the **role** of that figure. Both truths stand next to each other, and
// neither pretends to be the other.
//
// ══ The honesty is the whole point ═══════════════════════════════════════════════════════════
//
//   1. **Three bars, no success rate.** *Delivered* · *waiting for a human* · *aborted*. The
//      three numbers come **ready from the server**; nothing is computed from `success / runs`
//      here. On exactly that hangs whether `architect` shows 78 % or 6 % and
//      `project_manager` 64 % or 0 %: `planned` is a delivered plan, `blocked` is a human who
//      did not answer, and neither is a failure of the role.
//   2. **Colours verbatim from `TopBar.tsx`**, which in turn has them verbatim from
//      `AgentMonitor.tsx`. Two views of the same run must never contradict each other.
//   3. **Costs with "≥".** Every cost row in the existing data is unpriced; an amount without
//      a sign would claim a precision that does not exist.
//   4. **Rounds are not steps.** An average of 6.9 rounds of the agent loop against an average
//      of 21.5 steps in the event stream: two different things, so two different labels.
//   5. **Duration as median/p90/max plus a histogram**, never as an average: one run over 36.5
//      hours turns every average into a caricature.
//   6. **The window is named.** `run_retention_days` deletes older runs, so "favourite tools"
//      means "of the last 30 days", not "ever".
//
// ══ No chart library ═════════════════════════════════════════════════════════════════════════
//
// Everything here is `div`s with percentage widths, exactly as the timeline paints its bars.
// The Dockerfile runs `npm install` **without** a lockfile on every build, so a new dependency
// would be a risk for the build and would buy three stacked rectangles.

import { tr } from "../../i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { officeApi, type AgentRecord, type Scope } from "./api.ts";
import { dauerText, statusFarbe, statusText, tokenText, usdText, zahl } from "./TopBar.tsx";

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** The selectable windows. The long text stands beside it because it lands in the heading: a
 *  key figure without its window is not a key figure. 30 days is the default value of
 *  `run_retention_days`; beyond that there simply are no runs any more, and a window that
 *  promises more than the retention delivers would be an empty promise. */
const FENSTER: readonly { h: number; kurz: string; lang: string }[] = [
  { h: 24, kurz: "akte.fenster_24h_kurz", lang: "akte.fenster_24h" },
  { h: 24 * 7, kurz: "akte.fenster_7t_kurz", lang: "akte.fenster_7t" },
  { h: 24 * 30, kurz: "akte.fenster_30t_kurz", lang: "akte.fenster_30t" },
];

/** Default: the whole retention window. The file is a review, not an alarm clock. */
const FENSTER_STD = 24 * 30;

/** This many tools per role are fetched and shown by the ranking. Below that the long tail of
 *  one-off calls begins, which nobody reads. */
const WERKZEUG_LIMIT = 8;

/** An aggregate over weeks does not change every second. */
const AKTE_REFETCH_MS = 60_000;

// ── Farben ──────────────────────────────────────────────────────────────────────────────────

/** Text colour to area colour.
 *
 *  The **key** is verbatim what `statusFarbe` delivers; the value is the same colour as an
 *  area. Two reasons for this detour instead of a `replace("text-", "bg-")`: Tailwind reads
 *  class names as text from the source, so a name assembled at runtime would not exist in the
 *  finished CSS at all. And if `ST_FARBE` drifts one day, the entry is missing here: the bar
 *  turns grey instead of being silently wrong. */
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

// ── The three bars ──────────────────────────────────────────────────────────────────────────
//
// `status` is **not** a filter here but only the source of the colour: the numbers themselves
// are computed by the server. If `success` stands green in the agent monitor, "delivered" is
// the same green here; otherwise the same run would look different in two views.

interface BalkenArt {
  key: "delivered" | "waiting" | "aborted";
  label: string;
  /** For the colour, see above. */
  status: string;
  titel: string;
}

const BALKEN: readonly BalkenArt[] = [
  {
    key: "delivered", label: "akte.balken_abgeliefert", status: "success",
    titel: "akte.balken_abgeliefert_titel",
  },
  {
    key: "waiting", label: "akte.balken_wartet", status: "blocked",
    titel: "akte.balken_wartet_titel",
  },
  {
    key: "aborted", label: "akte.balken_abgebrochen", status: "failed",
    titel: "akte.balken_abgebrochen_titel",
  },
];

// ── Zahlen ──────────────────────────────────────────────────────────────────────────────────

/** One decimal place with a German comma. By hand instead of `toLocaleString`, so that the
 *  same number looks the same in every browser, the same reasoning as with `zahl` in `TopBar`. */
function komma1(v: number): string {
  return v.toFixed(1).replace(".", ",");
}

function prozent(teil: number, ganzes: number): number {
  return ganzes > 0 ? (teil / ganzes) * 100 : 0;
}

/** How long ago, roughly. The exact moment stands in the `title`. */
function herText(iso: string | null | undefined, jetzt: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const d = Math.max(0, jetzt - t);
  const min = Math.round(d / 60_000);
  if (min < 1) return "gerade eben";
  if (min < 60) return tr("akte.vor_min", { anzahl: min });
  const std = Math.round(min / 60);
  if (std < 48) return tr("akte.vor_std", { anzahl: std });
  return tr("akte.vor_tagen", { anzahl: Math.round(std / 24) });
}

/** Label of a histogram bucket. `lt_ms` is the **upper** bound; when it is missing this is the
 *  open bucket at the top. */
function eimerText(lt: number | null | undefined, vorher: number | null): string {
  if (lt === null || lt === undefined || !Number.isFinite(lt)) {
    return vorher !== null ? `> ${dauerText(vorher)}` : tr("akte.darueber");
  }
  return vorher !== null ? `${dauerText(vorher)} – ${dauerText(lt)}` : `< ${dauerText(lt)}`;
}

// ── Interface ───────────────────────────────────────────────────────────────────────────────

export interface PersonalakteProps {
  scope: Scope;
  /** Role that should be expanded and scrolled to, the role of the selected figure.
   *  `null` or missing means nobody is selected, and then the choice stays with the viewer. A
   *  change expands the new role; collapsing by hand persists afterwards. */
  focusAgent?: string | null;
  /** Preselected time window in hours. Read only on mount. */
  initialSinceHours?: number;
  className?: string;
}

// ── The component ───────────────────────────────────────────────────────────────────────────

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

  // The selected figure determines the expanded role and brings it into view. Without the
  // scrolling the file would show the right role of twelve, but below the edge.
  useEffect(() => {
    if (!focusAgent) return;
    setOffen(focusAgent);
    boxRef.current
      ?.querySelector<HTMLElement>(`[data-rolle="${CSS.escape(focusAgent)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [focusAgent]);

  const gemessen = akte.data?.since_hours ?? stunden;
  const treffer = FENSTER.find((f) => f.h === gemessen);
  const fensterText = treffer ? tr(treffer.lang)
    : tr("akte.fenster_tage", { anzahl: Math.round(gemessen / 24) });

  // Order: the busiest role first, on a tie the most recently active one. That is the order in
  // which one looks, and it is stable because it does not depend on who is selected right
  // now.
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
        {tr("akte.aufbewahrung_hinweis")}
      </p>

      {akte.isLoading && <div className="py-4 text-center text-xs text-muted">{tr("akte.wird_geladen")}</div>}

      {akte.error && (
        <div className="rounded border border-red-400/40 bg-red-400/5 px-2 py-1 text-xs text-red-400">
          {tr("akte.nicht_ladbar")}: {(akte.error as Error).message}
        </div>
      )}

      {!akte.isLoading && !akte.error && rollen.length === 0 && (
        <div className="py-4 text-center text-xs text-muted">
          {tr("akte.kein_lauf", { fenster: fensterText })}
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

// ── One role ────────────────────────────────────────────────────────────────────────────────

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
  // What is left over is still running (or has a status the server assigns to none of the
  // three groups). It gets a grey remainder of its own; distributing it among the three would
  // be exactly the kind of silent arithmetic this view is meant to avoid.
  const rest = Math.max(0, runs - beurteilt);
  const basis = beurteilt + rest;

  return (
    <div data-rolle={r.agent}
      className={"rounded border px-2 py-1.5 " + (offen ? "border-brand bg-brand/5" : "border-line")}>
      <button type="button" onClick={onToggle} aria-expanded={offen}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 text-left text-xs">
        <span className="shrink-0 text-[11px] text-muted">{offen ? "▾" : "▸"}</span>
        <span className="font-medium">{r.agent}</span>
        <span className="text-muted">{zahl(runs)} {tr(runs === 1 ? "agent_monitor.lauf" : "agent_monitor.laeufe")}</span>
        {(r.running ?? 0) > 0 && (
          <span className="text-yellow-400" title={`${r.running} laufen gerade`}>
            ● {zahl(r.running ?? 0)} aktiv
          </span>
        )}
        <div className="flex-1" />
        <span className="text-muted" title={tr("akte.letzter_lauf")}>
          {herText(r.last_run_at, jetzt)}
        </span>
      </button>

      <Balkenband werte={werte} rest={rest} basis={basis} />

      {offen && <Details r={r} basis={basis} werte={werte} rest={rest} />}
    </div>
  );
}

// ── The bar band ────────────────────────────────────────────────────────────────────────────
//
// One stacked bar, three `div`s with percentage widths (plus the grey remainder). The numbers
// stand beside it: a bar alone says "about a third", and about is too little with twelve
// runs.

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
              title={`${tr(b.label)}: ${zahl(n)} ${tr("akte.von")} ${zahl(basis)}`} />
          );
        })}
        {rest > 0 && (
          <div className="bg-line" style={{ width: `${prozent(rest, basis)}%` }}
            title={`${tr("akte.ohne_urteil")}: ${zahl(rest)} ${tr("akte.von")} ${zahl(basis)}`} />
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
          <span className="text-muted" title={tr("akte.ohne_urteil_titel")}>
            {tr("dock.laeuft_noch")} {zahl(rest)}
          </span>
        )}
      </div>
    </div>
  );
}

// ── The expanded part ───────────────────────────────────────────────────────────────────────

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
  // `!== false` and not `=== true`: if the field is missing it is unknown whether everything
  // was priced, and unknown belongs on the side of the lower bound. An amount without "≥"
  // would be a precision claim nobody checked.
  const unbepreist = r.cost_partial !== false;

  return (
    <div className="mt-2 space-y-2 border-t border-line pt-2 text-[11px]">
      {/* Woher die drei Balken kommen — roh, unverdichtet, zum Nachrechnen. */}
      {statusZeilen.length > 0 && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5"
          title={tr("akte.rohe_status")}>
          {statusZeilen.map(([s, n]) => (
            <span key={s} className={statusFarbe(s)}>
              {statusText(s)} <b className="text-ink">{zahl(n)}</b>
            </span>
          ))}
        </div>
      )}

      {/* Runden und Schritte — getrennt beschriftet, weil es zwei verschiedene Dinge sind. */}
      <dl className="grid grid-cols-[7.5rem_1fr] gap-x-2 gap-y-1">
        <Feld label={tr("akte.runden")} titel={tr("akte.runden_titel")}>
          Ø {komma1(r.iterations_avg ?? 0)} · max {zahl(r.iterations_max ?? 0)}
        </Feld>
        <Feld label={tr("akte.schritte")} titel={tr("akte.schritte_titel")}>
          Ø {komma1(r.steps_avg ?? 0)} · max {zahl(r.steps_max ?? 0)}
        </Feld>
        <Feld label={tr("buero.tokens")}
          titel={tr("akte.cache_gelesen", { anzahl: zahl(r.cache_read_tokens ?? 0) })}>
          {tokenText((r.in_tokens ?? 0) + (r.out_tokens ?? 0))}
          <span className="text-muted">
            {" "}({zahl(r.in_tokens ?? 0)} {tr("akte.ein")} · {zahl(r.out_tokens ?? 0)} {tr("akte.aus")})
          </span>
        </Feld>
        <Feld label={tr("akte.kosten")}
          titel={unbepreist
            ? tr("akte.kosten_teilweise")
            : tr("akte.kosten_voll")}>
          {usdText(r.cost_usd ?? 0, unbepreist)}
        </Feld>
        <Feld label={tr("agent_monitor.laeufe")} titel={tr("akte.laeufe_titel")}>
          {zahl(basis)}
          <span className="text-muted">
            {" "}({zahl(werte.delivered)} + {zahl(werte.waiting)} + {zahl(werte.aborted)}
            {rest > 0 ? ` + ${zahl(rest)} ${tr("akte.laufend")}` : ""})
          </span>
        </Feld>
      </dl>

      {/* Dauer: Median, p90, Maximum — und die Verteilung darunter. */}
      <div>
        <div className="mb-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{tr("akte.dauer")}</span>
          <span className="text-muted" title={tr("akte.median_titel")}>
            {tr("akte.median")} <b className="text-ink">{dauerText(dauer.p50_ms)}</b>
          </span>
          <span className="text-muted" title={tr("akte.p90_titel")}>
            p90 <b className="text-ink">{dauerText(dauer.p90_ms)}</b>
          </span>
          <span className="text-muted" title={tr("akte.max_titel")}>
            max <b className="text-ink">{dauerText(dauer.max_ms)}</b>
          </span>
        </div>
        <p className="mb-1 text-[11px] text-muted">
          {tr("akte.kein_mittelwert")}
        </p>
        {eimer.length === 0 ? (
          <div className="text-muted">{tr("akte.keine_verteilung")}</div>
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
        <div className="mb-0.5 font-medium">{tr("dock.werkzeuge")}</div>
        {werkzeuge.length === 0 ? (
          <div className="text-muted">{tr("akte.kein_werkzeugaufruf")}</div>
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
                    ? tr("akte.aufrufe_fehl", { fehl: t.failed ?? 0, ok: t.ok ?? 0 })
                    : tr("akte.kein_fehlschlag")}>
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
