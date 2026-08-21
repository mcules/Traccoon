// Layer 2, the timeline. The heart of the operation: one column per second, and a click
// rewinds the room to that point.
//
// Two statements sit in one bar, and they are deliberately separated:
//
//   · **Height = amount.** How much happened in this second at all, square-root scaled against
//     the peak of the visible window. Linear scaling would squeeze a single second with 200
//     events and all the others into a line; the square root lets the outlier be an outlier
//     without flattening the rest. `max(6, …)` makes sure a second with *anything* in it stays
//     visible, while an empty second gets height 0, because otherwise "nothing happened" could
//     not be told apart from "almost nothing happened".
//   · **Colour = composition.** Every row is a child with `height: share %`. A column dominated
//     by red is therefore a second full of failures, exactly the place one clicks on.
//
// The numbers come from `timeline.ts` (layer 0). Formatting happens **here**, because
// `toLocale*` is forbidden there and `labelOf` therefore delivers numbers, not text.
//
// The window **slides** (`slice(-spalten)`): nothing is scrolled and no bar gets thinner. How
// many columns fit is measured by a `ResizeObserver`: a narrow tab means fewer seconds, not
// thinner bars. That is exactly why `TIMELINE_COLUMNS` stands there only as an upper bound.

import { tr } from "../../i18n";
import { useCallback, useMemo, useRef, useState } from "react";
import { REPLAY_CAP, TIMELINE_BUCKET_MS, TIMELINE_COLUMNS } from "./const.ts";
import { bucketize, labelOf } from "./timeline.ts";
import type { Bucket, LogEntry } from "./types.ts";

// ── Interface ───────────────────────────────────────────────────────────────────────────────

/** What the controls need from the log, **structurally**, not as a class.
 *
 *  Both the real `Recorder` (layer 0) and the `RecorderApi` of the feed fulfil this shape. A
 *  reference to the class itself would be narrower than necessary and would force a type
 *  assertion; `bounds()` is deliberately loose here (nullable, `dropped` optional), because
 *  that is exactly where the two versions differ. */
export interface LogSource {
  entries(): readonly LogEntry[];
  bounds(): { t0: number; t1: number; dropped?: boolean } | null;
}

export interface TimelineProps {
  /** The log. Only read. */
  recorder: LogSource;
  /** The only recomputation signal, raised throttled in the feed. */
  revision: number;
  /** Angesprungene Position in Epoch-ms, `null` = Gegenwart/Live. */
  seekTs: number | null;
  /** Gets `b.t` unchanged, so epoch ms, exactly what `Replay.seek` expects. */
  onSeek: (ts: number) => void;
  className?: string;
}

// ── The four rows ───────────────────────────────────────────────────────────────────────────
//
// Four colours, there are no more. Red is the same colour as `failed` in the agent monitor;
// violet, blue and grey are deliberately **not** status colours, because a message is not a
// success and a tool call is not waiting.

type SeriesKey = "tools" | "says" | "thinks" | "errors";

interface Series {
  key: SeriesKey;
  css: string;
  inside: string;
  many: string;
}

/** Order of the **label**: "2 tool calls, 1 message". */
const SERIES: readonly Series[] = [
  { key: "tools", css: "bg-sky-400", inside: "timeline.tool_call", many: "timeline.tool_calls" },
  { key: "says", css: "bg-violet-400", inside: "timeline.message", many: "timeline.messages" },
  { key: "thinks", css: "bg-slate-400", inside: "timeline.thinking_step", many: "timeline.thinking_steps" },
  { key: "errors", css: "bg-red-400", inside: "timeline.error", many: "timeline.errors" },
];

/** Order in the **stack**, from top to bottom. Errors lie on top: they are what one has to be
 *  able to recognise in passing. */
const BATCH: readonly SeriesKey[] = ["errors", "says", "tools", "thinks"];

// ── Geometrie ───────────────────────────────────────────────────────────────────────────────

/** Bar width and gap in CSS pixels. Fixed: the price is that fewer seconds fit into a narrow
 *  window, and that is cheaper than illegibly thin bars. */
const COLUMN_PX = 4;
const GAP_PX = 1;
/** Below this many columns the display is no longer worth it; then it simply gets crowded. */
const MIN_COLUMNS = 24;

// ── Beschriftung ────────────────────────────────────────────────────────────────────────────

function two(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Clock time of a bar in the **local time of the browser**.
 *
 *  `labelOf` computes in UTC: layer 0 does not know the time zone and must not know it,
 *  because otherwise the same log would show two different timelines in two tabs. It is
 *  resolved exactly here, by passing the bar moment shifted by the local offset of **this**
 *  moment into `labelOf` (per bar, so that the switch to summer time is right as well).
 *  The rest of the application shows times in local time as well (`lib/formatTime.ts`), and a
 *  timeline in UTC would contradict every other timestamp on the screen. */
function placeNumbers(b: Bucket): ReturnType<typeof labelOf> {
  const offset = new Date(b.t).getTimezoneOffset() * 60_000;
  return labelOf({ ...b, t: b.t - offset });
}

/** "12:34:56 · 2 tool calls, 1 message": **only** the numbers that are not 0, one event in the
 *  singular. A label saying "0 errors" talks about something that did not happen. */
export function barLabel(b: Bucket): string {
  const l = placeNumbers(b);
  const uhr = `${two(l.h)}:${two(l.m)}:${two(l.s)}`;
  const parts: string[] = [];
  for (const r of SERIES) {
    const n = l[r.key];
    if (n > 0) parts.push(`${n} ${tr(n === 1 ? r.inside : r.many)}`);
  }
  return parts.length ? `${uhr} · ${parts.join(", ")}` : `${uhr} · ${tr("timeline.no_events")}`;
}

/** Only the clock time, for the edge label below the bar. */
function time(b: Bucket): string {
  const l = placeNumbers(b);
  return `${two(l.h)}:${two(l.m)}:${two(l.s)}`;
}

// ── The component ───────────────────────────────────────────────────────────────────────────

export default function Timeline({ recorder, revision, seekTs, onSeek, className }: TimelineProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const [width, setWidth] = useState(0);
  /** Roving keyboard focus (roving tabindex): 220 tab stops would not be operation. */
  const [focus, setFocus] = useState<number | null>(null);

  // Summarise the log into seconds again as soon as something has changed. `revision` is the
  // signal; `recorder` itself only changes identity on a session change.
  const all = useMemo(() => bucketize(recorder.entries()), [recorder, revision]);
  const limits = recorder.bounds();
  const capped = limits?.dropped === true;

  /** Width measurement as a **callback ref**, not as an effect.
   *
   *  A `useEffect` with an empty dependency list would run exactly once, and on the first
   *  render the bar does not exist yet, because an empty log shows a hint instead. The effect
   *  would find `null`, never run again, and the bar would compute with the full width
   *  forever. The callback on the other hand takes hold exactly when the element appears. */
  const setBox = useCallback((el: HTMLDivElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    boxRef.current = el;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.observe(el);
    observerRef.current = ro;
  }, []);

  const fit = width > 0
    ? Math.floor((width + GAP_PX) / (COLUMN_PX + GAP_PX))
    : TIMELINE_COLUMNS;
  const columns = Math.max(MIN_COLUMNS, Math.min(TIMELINE_COLUMNS, fit));
  const visible = all.length > columns ? all.slice(-columns) : all;
  const hidden = all.length - visible.length;

  // Peak of the **visible** window: the bar answers "how full was this second compared to what
  // I am looking at right now", and an outlier from three hours ago that has long slid out of
  // the window must not determine the picture any more.
  let peak = 0;
  for (const b of visible) {
    const t = b.tools + b.says + b.thinks + b.errors;
    if (t > peak) peak = t;
  }

  const targetT = seekTs === null ? null : Math.floor(seekTs / TIMELINE_BUCKET_MS) * TIMELINE_BUCKET_MS;
  const currentIdx = targetT === null ? -1 : visible.findIndex((b) => b.t === targetT);
  const tabIdx = focus !== null && focus < visible.length
    ? focus
    : (currentIdx >= 0 ? currentIdx : visible.length - 1);

  /** Move the focus **without** jumping. Every jump rebuilds the engine and replays the log
   *  from the start; with a held arrow key that would be two hundred rebuilds in one second.
   *  Triggering therefore happens on enter or space, so with the button itself. */
  const move = (to: number) => {
    if (visible.length === 0) return;
    const i = Math.max(0, Math.min(visible.length - 1, to));
    setFocus(i);
    boxRef.current?.querySelector<HTMLElement>(`[data-spalte="${i}"]`)?.focus();
  };

  const key = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const i = Number(e.currentTarget.dataset.column);
    if (e.key === "ArrowLeft") { e.preventDefault(); move(i - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); move(i + 1); }
    else if (e.key === "Home") { e.preventDefault(); move(0); }
    else if (e.key === "End") { e.preventDefault(); move(visible.length - 1); }
  };

  if (all.length === 0) {
    return (
      <div className={`rounded border border-line bg-card px-3 py-2 text-xs text-muted ${className ?? ""}`}>
        Noch keine Ereignisse in dieser Sitzung.
      </div>
    );
  }

  const capTitle = capped
    ? `Der Anfang der Sitzung ist nicht mehr im Speicher: das Büro hält höchstens `
      + `${REPLAY_CAP.toLocaleString("de-DE")} Ereignisse und verwirft die ältesten.`
      + (hidden > 0 ? ` Zusätzlich liegen ${hidden} Sekunden links außerhalb des Fensters.` : "")
    : `${hidden} Sekunden liegen links außerhalb des Fensters.`;

  return (
    <div className={`rounded border border-line bg-card px-2 py-1.5 ${className ?? ""}`}>
      <div
        ref={setBox}
        role="group"
        aria-label={tr("timeline.title")}
        // `overflow-hidden`: if the measurement miscounts by one column (rounding, scrollbar),
        // the bar should be clipped instead of tearing the layout behind it open.
        className="flex h-16 items-end overflow-hidden"
        style={{ gap: `${GAP_PX}px` }}
      >
        {(capped || hidden > 0) && (
          <div
            title={capTitle}
            aria-label={capTitle}
            className={`h-full shrink-0 rounded-sm ${capped ? "bg-line" : "bg-line/50"}`}
            style={{ width: `${COLUMN_PX}px` }}
          />
        )}
        {visible.map((b, i) => {
          const total = b.tools + b.says + b.thinks + b.errors;
          // Square-root scaling against the peak. `gesamt === 0` explicitly gives 0 and not the
          // minimum height; otherwise every empty second would claim something had happened.
          const h = total === 0 || peak === 0
            ? 0
            : Math.max(6, Math.round((Math.sqrt(total) / Math.sqrt(peak)) * 100));
          const label = barLabel(b);
          const is = i === currentIdx;
          return (
            <button
              key={b.t}
              type="button"
              data-spalte={i}
              tabIndex={i === tabIdx ? 0 : -1}
              // A statement about the app ("this is where the room stands right now"), not
              // about the focus, which is why `aria-current` and not `aria-selected`.
              aria-current={is ? "true" : undefined}
              aria-label={label}
              title={label}
              onClick={() => onSeek(b.t)}
              onKeyDown={key}
              onFocus={() => setFocus(i)}
              className={"group flex h-full shrink-0 cursor-pointer flex-col justify-end rounded-sm "
                + "outline-none focus-visible:ring-1 focus-visible:ring-brand "
                + (is ? "bg-brand/20" : "hover:bg-line/40")}
              style={{ width: `${COLUMN_PX}px` }}
            >
              {h > 0 ? (
                <span className="flex w-full flex-col overflow-hidden rounded-sm" style={{ height: `${h}%` }}>
                  {BATCH.map((key) => {
                    const n = b[key];
                    if (n === 0) return null;
                    const r = SERIES.find((x) => x.key === key)!;
                    return (
                      <span
                        key={key}
                        className={`w-full ${r.css}`}
                        style={{ height: `${(n / total) * 100}%` }}
                      />
                    );
                  })}
                </span>
              ) : (
                // Empty second: a dot on the base line. Without it the time axis would be
                // invisible in quiet places and one would not know where to click.
                <span className="w-full bg-line" style={{ height: "1px" }} />
              )}
            </button>
          );
        })}
      </div>
      <div className="mt-1 flex items-center justify-between text-[11px] text-muted">
        <span>{time(visible[0])}</span>
        <span>
          {capped && <span className="mr-2" title={capTitle}>⚠ Anfang verworfen</span>}
          {visible.length} {visible.length === 1 ? "Sekunde" : "Sekunden"}
        </span>
        <span>{time(visible[visible.length - 1])}</span>
      </div>
    </div>
  );
}
