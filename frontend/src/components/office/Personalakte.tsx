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
import { durationText, statusColor, statusText, tokenText, usdText, number } from "./TopBar.tsx";

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** The selectable windows. The long text stands beside it because it lands in the heading: a
 *  key figure without its window is not a key figure. 30 days is the default value of
 *  `run_retention_days`; beyond that there simply are no runs any more, and a window that
 *  promises more than the retention delivers would be an empty promise. */
const WINDOW: readonly { h: number; kurz: string; long: string }[] = [
  { h: 24, kurz: "personnel_file.24_h", long: "personnel_file.last_24_hours" },
  { h: 24 * 7, kurz: "personnel_file.7_days", long: "personnel_file.last_7_days" },
  { h: 24 * 30, kurz: "personnel_file.30_days", long: "personnel_file.last_30_days" },
];

/** Default: the whole retention window. The file is a review, not an alarm clock. */
const WINDOW_STD = 24 * 30;

/** This many tools per role are fetched and shown by the ranking. Below that the long tail of
 *  one-off calls begins, which nobody reads. */
const TOOL_LIMIT = 8;

/** An aggregate over weeks does not change every second. */
const FILE_REFETCH_MS = 60_000;

// ── Farben ──────────────────────────────────────────────────────────────────────────────────

/** Text colour to area colour.
 *
 *  The **key** is verbatim what `statusFarbe` delivers; the value is the same colour as an
 *  area. Two reasons for this detour instead of a `replace("text-", "bg-")`: Tailwind reads
 *  class names as text from the source, so a name assembled at runtime would not exist in the
 *  finished CSS at all. And if `ST_FARBE` drifts one day, the entry is missing here: the bar
 *  turns grey instead of being silently wrong. */
const AREA: Record<string, string> = {
  "text-green-400": "bg-green-400",
  "text-orange-400": "bg-orange-400",
  "text-red-400": "bg-red-400",
  "text-yellow-400": "bg-yellow-400",
  "text-sky-400": "bg-sky-400",
};

function area(status: string): string {
  return AREA[statusColor(status)] ?? "bg-line";
}

// ── The three bars ──────────────────────────────────────────────────────────────────────────
//
// `status` is **not** a filter here but only the source of the colour: the numbers themselves
// are computed by the server. If `success` stands green in the agent monitor, "delivered" is
// the same green here; otherwise the same run would look different in two views.

interface BarArt {
  key: "delivered" | "waiting" | "aborted";
  label: string;
  /** For the colour, see above. */
  status: string;
  title: string;
}

const BAR: readonly BarArt[] = [
  {
    key: "delivered", label: "personnel_file.delivered", status: "success",
    title: "personnel_file.finished_successfully_finished_plan",
  },
  {
    key: "waiting", label: "personnel_file.waiting_person", status: "blocked",
    title: "personnel_file.run_hangs_question_permission",
  },
  {
    key: "aborted", label: "personnel_file.aborted", status: "failed",
    title: "personnel_file.failed_stuck_loop_failed",
  },
];

// ── Zahlen ──────────────────────────────────────────────────────────────────────────────────

/** One decimal place with a German comma. By hand instead of `toLocaleString`, so that the
 *  same number looks the same in every browser, the same reasoning as with `zahl` in `TopBar`. */
function comma1(v: number): string {
  return v.toFixed(1).replace(".", ",");
}

function percent(part: number, whole: number): number {
  return whole > 0 ? (part / whole) * 100 : 0;
}

/** How long ago, roughly. The exact moment stands in the `title`. */
function agoText(iso: string | null | undefined, now: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const d = Math.max(0, now - t);
  const min = Math.round(d / 60_000);
  if (min < 1) return "gerade eben";
  if (min < 60) return tr("personnel_file.count_min_ago", { count: min });
  const std = Math.round(min / 60);
  if (std < 48) return tr("personnel_file.count_h_ago", { count: std });
  return tr("personnel_file.count_days_ago", { count: Math.round(std / 24) });
}

/** Label of a histogram bucket. `lt_ms` is the **upper** bound; when it is missing this is the
 *  open bucket at the top. */
function bucketText(lt: number | null | undefined, before: number | null): string {
  if (lt === null || lt === undefined || !Number.isFinite(lt)) {
    return before !== null ? `> ${durationText(before)}` : tr("personnel_file.above");
  }
  return before !== null ? `${durationText(before)} – ${durationText(lt)}` : `< ${durationText(lt)}`;
}

// ── Interface ───────────────────────────────────────────────────────────────────────────────

export interface PersonnelfileProps {
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

export default function Personnelfile({
  scope, focusAgent, initialSinceHours, className,
}: PersonnelfileProps): JSX.Element {
  const [hours, setHours] = useState<number>(initialSinceHours ?? WINDOW_STD);
  const [open, setOpen] = useState<string | null>(focusAgent ?? null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const file = useQuery({
    queryKey: ["office", "agents", scopeKey, hours],
    queryFn: () => officeApi.agents(scope, { sinceHours: hours, toolLimit: TOOL_LIMIT }),
    refetchInterval: FILE_REFETCH_MS,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: 1,
  });

  // The selected figure determines the expanded role and brings it into view. Without the
  // scrolling the file would show the right role of twelve, but below the edge.
  useEffect(() => {
    if (!focusAgent) return;
    setOpen(focusAgent);
    boxRef.current
      ?.querySelector<HTMLElement>(`[data-rolle="${CSS.escape(focusAgent)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [focusAgent]);

  const measured = file.data?.since_hours ?? hours;
  const hits = WINDOW.find((f) => f.h === measured);
  const windowText = hits ? tr(hits.long)
    : tr("personnel_file.last_count_days", { count: Math.round(measured / 24) });

  // Order: the busiest role first, on a tie the most recently active one. That is the order in
  // which one looks, and it is stable because it does not depend on who is selected right
  // now.
  const roles = useMemo(() => {
    const copy = [...(file.data?.agents ?? [])];
    copy.sort((a, b) => {
      const d = (b.runs ?? 0) - (a.runs ?? 0);
      if (d !== 0) return d;
      const ta = a.last_run_at ? Date.parse(a.last_run_at) : 0;
      const tb = b.last_run_at ? Date.parse(b.last_run_at) : 0;
      if (tb !== ta) return tb - ta;
      return a.agent < b.agent ? -1 : 1;
    });
    return copy;
  }, [file.data]);

  const now = Date.now();

  return (
    <div ref={boxRef} className={`space-y-2 ${className ?? ""}`}>
      {/* Head: the window is part of the statement, so it stands at the very top and is switchable. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line pb-1.5 text-[11px]">
        <span className="text-muted">
          📇 Kennzahlen je Rolle, <b className="text-ink">{windowText}</b>
        </span>
        <div className="flex-1" />
        <span role="group" aria-label="Zeitfenster" className="flex overflow-hidden rounded border border-line">
          {WINDOW.map((f) => (
            <button key={f.h} type="button" onClick={() => setHours(f.h)}
              aria-pressed={hours === f.h}
              title={`Kennzahlen ${f.long}`}
              className={"px-1.5 py-0.5 " + (hours === f.h ? "bg-brand text-white" : "text-muted hover:bg-surface")}>
              {f.kurz}
            </button>
          ))}
        </span>
      </div>

      <p className="text-[11px] text-muted">
        {tr("personnel_file.older_runs_removed_retention")}
      </p>

      {file.isLoading && <div className="py-4 text-center text-xs text-muted">{tr("personnel_file.loading_file")}</div>}

      {file.error && (
        <div className="rounded border border-red-400/40 bg-red-400/5 px-2 py-1 text-xs text-red-400">
          {tr("personnel_file.file_not_loadable")}: {(file.error as Error).message}
        </div>
      )}

      {!file.isLoading && !file.error && roles.length === 0 && (
        <div className="py-4 text-center text-xs text-muted">
          {tr("personnel_file.no_run_window", { window: windowText })}
        </div>
      )}

      {roles.map((r) => (
        <Rolecard key={r.agent} r={r} now={now}
          open={open === r.agent}
          onToggle={() => setOpen((v) => (v === r.agent ? null : r.agent))} />
      ))}
    </div>
  );
}

// ── One role ────────────────────────────────────────────────────────────────────────────────

function Rolecard({ r, now: now, open: open, onToggle }: {
  r: AgentRecord;
  now: number;
  open: boolean;
  onToggle: () => void;
}) {
  const runs = r.runs ?? 0;
  const values: Record<BarArt["key"], number> = {
    delivered: r.delivered ?? 0,
    waiting: r.waiting ?? 0,
    aborted: r.aborted ?? 0,
  };
  const judged = values.delivered + values.waiting + values.aborted;
  // What is left over is still running (or has a status the server assigns to none of the
  // three groups). It gets a grey remainder of its own; distributing it among the three would
  // be exactly the kind of silent arithmetic this view is meant to avoid.
  const remainder = Math.max(0, runs - judged);
  const basis = judged + remainder;

  return (
    <div data-rolle={r.agent}
      className={"rounded border px-2 py-1.5 " + (open ? "border-brand bg-brand/5" : "border-line")}>
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 text-left text-xs">
        <span className="shrink-0 text-[11px] text-muted">{open ? "▾" : "▸"}</span>
        <span className="font-medium">{r.agent}</span>
        <span className="text-muted">{number(runs)} {tr(runs === 1 ? "agent_monitor.run" : "agent_monitor.runs")}</span>
        {(r.running ?? 0) > 0 && (
          <span className="text-yellow-400" title={`${r.running} laufen gerade`}>
            ● {number(r.running ?? 0)} aktiv
          </span>
        )}
        <div className="flex-1" />
        <span className="text-muted" title={tr("personnel_file.last_run_role_chosen")}>
          {agoText(r.last_run_at, now)}
        </span>
      </button>

      <Barband values={values} rest={remainder} basis={basis} />

      {open && <Details r={r} basis={basis} values={values} rest={remainder} />}
    </div>
  );
}

// ── The bar band ────────────────────────────────────────────────────────────────────────────
//
// One stacked bar, three `div`s with percentage widths (plus the grey remainder). The numbers
// stand beside it: a bar alone says "about a third", and about is too little with twelve
// runs.

function Barband({ values: values, rest: remainder, basis }: {
  values: Record<BarArt["key"], number>;
  rest: number;
  basis: number;
}) {
  return (
    <div className="mt-1">
      <div className="flex h-2 w-full overflow-hidden rounded-sm bg-line/40">
        {BAR.map((b) => {
          const n = values[b.key];
          if (n <= 0) return null;
          return (
            <div key={b.key} className={area(b.status)}
              style={{ width: `${percent(n, basis)}%` }}
              title={`${tr(b.label)}: ${number(n)} ${tr("personnel_file.text_3")} ${number(basis)}`} />
          );
        })}
        {remainder > 0 && (
          <div className="bg-line" style={{ width: `${percent(remainder, basis)}%` }}
            title={`${tr("personnel_file.still_running_without_verdict")}: ${number(remainder)} ${tr("personnel_file.text_3")} ${number(basis)}`} />
        )}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0 text-[11px]">
        {BAR.map((b) => (
          <span key={b.key} className={values[b.key] > 0 ? statusColor(b.status) : "text-muted"}
            title={b.title}>
            {b.label} {number(values[b.key])}
            <span className="text-muted"> ({Math.round(percent(values[b.key], basis))} %)</span>
          </span>
        ))}
        {remainder > 0 && (
          <span className="text-muted" title={tr("personnel_file.runs_without_final_verdict")}>
            {tr("dock.still_running")} {number(remainder)}
          </span>
        )}
      </div>
    </div>
  );
}

// ── The expanded part ───────────────────────────────────────────────────────────────────────

function Details({ r, basis, values: values, rest: remainder }: {
  r: AgentRecord;
  basis: number;
  values: Record<BarArt["key"], number>;
  rest: number;
}) {
  const status = r.by_status ?? {};
  const statusLines = Object.entries(status).filter(([, n]) => n > 0);
  const duration = r.duration ?? {};
  const bucket = duration.buckets ?? [];
  const bucketMax = bucket.reduce((m, b) => Math.max(m, b.n ?? 0), 0);
  const tools = r.tools ?? [];
  const toolMax = tools.reduce((m, t) => Math.max(m, t.n ?? 0), 0);
  // `!== false` and not `=== true`: if the field is missing it is unknown whether everything
  // was priced, and unknown belongs on the side of the lower bound. An amount without "≥"
  // would be a precision claim nobody checked.
  const unpriced = r.cost_partial !== false;

  return (
    <div className="mt-2 space-y-2 border-t border-line pt-2 text-[11px]">
      {/* Where the three bars come from — raw, uncondensed, for checking. */}
      {statusLines.length > 0 && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5"
          title={tr("personnel_file.raw_run_states_server")}>
          {statusLines.map(([s, n]) => (
            <span key={s} className={statusColor(s)}>
              {statusText(s)} <b className="text-ink">{number(n)}</b>
            </span>
          ))}
        </div>
      )}

      {/* Rounds and steps — labelled separately, because they are two different things. */}
      <dl className="grid grid-cols-[7.5rem_1fr] gap-x-2 gap-y-1">
        <Field label={tr("personnel_file.rounds")} title={tr("personnel_file.passes_through_agent_loop")}>
          Ø {comma1(r.iterations_avg ?? 0)} · max {number(r.iterations_max ?? 0)}
        </Field>
        <Field label={tr("personnel_file.steps")} title={tr("personnel_file.lines_event_stream_run")}>
          Ø {comma1(r.steps_avg ?? 0)} · max {number(r.steps_max ?? 0)}
        </Field>
        <Field label={tr("office_room.tokens")}
          title={tr("personnel_file.cache_read_count", { count: number(r.cache_read_tokens ?? 0) })}>
          {tokenText((r.in_tokens ?? 0) + (r.out_tokens ?? 0))}
          <span className="text-muted">
            {" "}({number(r.in_tokens ?? 0)} {tr("personnel_file.text_2")} · {number(r.out_tokens ?? 0)} {tr("personnel_file.text")})
          </span>
        </Field>
        <Field label={tr("personnel_file.cost")}
          title={unpriced
            ? tr("personnel_file.least_one_item_no")
            : tr("personnel_file.fully_priced_against_catalog")}>
          {usdText(r.cost_usd ?? 0, unpriced)}
        </Field>
        <Field label={tr("agent_monitor.runs")} title={tr("personnel_file.population_behind_three_bars")}>
          {number(basis)}
          <span className="text-muted">
            {" "}({number(values.delivered)} + {number(values.waiting)} + {number(values.aborted)}
            {remainder > 0 ? ` + ${number(remainder)} ${tr("personnel_file.running")}` : ""})
          </span>
        </Field>
      </dl>

      {/* Duration: median, p90, maximum — and the distribution below it. */}
      <div>
        <div className="mb-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{tr("personnel_file.duration")}</span>
          <span className="text-muted" title={tr("personnel_file.half_all_runs_faster")}>
            {tr("personnel_file.median")} <b className="text-ink">{durationText(duration.p50_ms)}</b>
          </span>
          <span className="text-muted" title={tr("personnel_file.nine_ten_runs_faster")}>
            p90 <b className="text-ink">{durationText(duration.p90_ms)}</b>
          </span>
          <span className="text-muted" title={tr("personnel_file.longest_run_window")}>
            max <b className="text-ink">{durationText(duration.max_ms)}</b>
          </span>
        </div>
        <p className="mb-1 text-[11px] text-muted">
          {tr("personnel_file.no_average_single_36")}
        </p>
        {bucket.length === 0 ? (
          <div className="text-muted">{tr("personnel_file.no_distribution_window")}</div>
        ) : (
          <div className="space-y-0.5">
            {bucket.map((b, i) => {
              const n = b.n ?? 0;
              const label = bucketText(b.lt_ms, i > 0 ? (bucket[i - 1].lt_ms ?? null) : null);
              return (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="w-[6.5rem] shrink-0 text-right text-muted">{label}</span>
                  <span className="h-2 min-w-0 flex-1 rounded-sm bg-line/40">
                    <span className="block h-2 rounded-sm bg-sky-400"
                      style={{ width: `${percent(n, bucketMax)}%` }} />
                  </span>
                  <span className="w-8 shrink-0 text-right text-muted">{number(n)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Tools: a ranking with counts and failures. */}
      <div>
        <div className="mb-0.5 font-medium">{tr("dock.tools")}</div>
        {tools.length === 0 ? (
          <div className="text-muted">{tr("personnel_file.no_tool_call_window")}</div>
        ) : (
          <div className="space-y-0.5">
            {tools.map((t) => (
              <div key={t.tool} className="flex items-center gap-1.5">
                <span className="w-[8rem] shrink-0 truncate font-mono text-[11px]" title={t.tool}>
                  {t.tool}
                </span>
                <span className="h-2 min-w-0 flex-1 rounded-sm bg-line/40">
                  <span className="block h-2 rounded-sm bg-sky-400"
                    style={{ width: `${percent(t.n ?? 0, toolMax)}%` }} />
                </span>
                <span className="w-8 shrink-0 text-right text-muted">{number(t.n ?? 0)}</span>
                <span className={"w-14 shrink-0 text-right " + ((t.failed ?? 0) > 0 ? "text-red-400" : "text-muted")}
                  title={(t.failed ?? 0) > 0
                    ? tr("personnel_file.fail_calls_failed_ok", { fail: t.failed ?? 0, ok: t.ok ?? 0 })
                    : tr("personnel_file.no_reported_failure_old")}>
                  {(t.failed ?? 0) > 0 ? `✕ ${number(t.failed ?? 0)}` : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, title: title, children }: {
  label: string;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <dt className="text-muted" title={title}>{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}
