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
  ein: string;
  viele: string;
}

/** Order of the **label**: "2 tool calls, 1 message". */
const SERIES: readonly Series[] = [
  { key: "tools", css: "bg-sky-400", ein: "timeline.tool_ein", viele: "timeline.tool_viele" },
  { key: "says", css: "bg-violet-400", ein: "timeline.says_ein", viele: "timeline.says_viele" },
  { key: "thinks", css: "bg-slate-400", ein: "timeline.thinks_ein", viele: "timeline.thinks_viele" },
  { key: "errors", css: "bg-red-400", ein: "timeline.errors_ein", viele: "timeline.errors_viele" },
];

/** Order in the **stack**, from top to bottom. Errors lie on top: they are what one has to be
 *  able to recognise in passing. */
const STAPEL: readonly SeriesKey[] = ["errors", "says", "tools", "thinks"];

// ── Geometrie ───────────────────────────────────────────────────────────────────────────────

/** Bar width and gap in CSS pixels. Fixed: the price is that fewer seconds fit into a narrow
 *  window, and that is cheaper than illegibly thin bars. */
const SPALTE_PX = 4;
const LUECKE_PX = 1;
/** Below this many columns the display is no longer worth it; then it simply gets crowded. */
const MIN_SPALTEN = 24;

// ── Beschriftung ────────────────────────────────────────────────────────────────────────────

function zwei(n: number): string {
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
function ortsZahlen(b: Bucket): ReturnType<typeof labelOf> {
  const versatz = new Date(b.t).getTimezoneOffset() * 60_000;
  return labelOf({ ...b, t: b.t - versatz });
}

/** "12:34:56 · 2 tool calls, 1 message": **only** the numbers that are not 0, one event in the
 *  singular. A label saying "0 errors" talks about something that did not happen. */
export function balkenLabel(b: Bucket): string {
  const l = ortsZahlen(b);
  const uhr = `${zwei(l.h)}:${zwei(l.m)}:${zwei(l.s)}`;
  const parts: string[] = [];
  for (const r of SERIES) {
    const n = l[r.key];
    if (n > 0) parts.push(`${n} ${tr(n === 1 ? r.ein : r.viele)}`);
  }
  return parts.length ? `${uhr} · ${parts.join(", ")}` : `${uhr} · ${tr("timeline.keine_ereignisse")}`;
}

/** Only the clock time, for the edge label below the bar. */
function uhrzeit(b: Bucket): string {
  const l = ortsZahlen(b);
  return `${zwei(l.h)}:${zwei(l.m)}:${zwei(l.s)}`;
}

// ── The component ───────────────────────────────────────────────────────────────────────────

export default function Timeline({ recorder, revision, seekTs, onSeek, className }: TimelineProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const beobachterRef = useRef<ResizeObserver | null>(null);
  const [breite, setBreite] = useState(0);
  /** Roving keyboard focus (roving tabindex): 220 tab stops would not be operation. */
  const [fokus, setFokus] = useState<number | null>(null);

  // Summarise the log into seconds again as soon as something has changed. `revision` is the
  // signal; `recorder` itself only changes identity on a session change.
  const all = useMemo(() => bucketize(recorder.entries()), [recorder, revision]);
  const grenzen = recorder.bounds();
  const gekappt = grenzen?.dropped === true;

  /** Width measurement as a **callback ref**, not as an effect.
   *
   *  A `useEffect` with an empty dependency list would run exactly once, and on the first
   *  render the bar does not exist yet, because an empty log shows a hint instead. The effect
   *  would find `null`, never run again, and the bar would compute with the full width
   *  forever. The callback on the other hand takes hold exactly when the element appears. */
  const setzeBox = useCallback((el: HTMLDivElement | null) => {
    beobachterRef.current?.disconnect();
    beobachterRef.current = null;
    boxRef.current = el;
    if (!el) return;
    setBreite(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setBreite(e.contentRect.width);
    });
    ro.observe(el);
    beobachterRef.current = ro;
  }, []);

  const passen = breite > 0
    ? Math.floor((breite + LUECKE_PX) / (SPALTE_PX + LUECKE_PX))
    : TIMELINE_COLUMNS;
  const spalten = Math.max(MIN_SPALTEN, Math.min(TIMELINE_COLUMNS, passen));
  const visible = all.length > spalten ? all.slice(-spalten) : all;
  const versteckt = all.length - visible.length;

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
  const tabIdx = fokus !== null && fokus < visible.length
    ? fokus
    : (currentIdx >= 0 ? currentIdx : visible.length - 1);

  /** Move the focus **without** jumping. Every jump rebuilds the engine and replays the log
   *  from the start; with a held arrow key that would be two hundred rebuilds in one second.
   *  Triggering therefore happens on enter or space, so with the button itself. */
  const bewege = (zu: number) => {
    if (visible.length === 0) return;
    const i = Math.max(0, Math.min(visible.length - 1, zu));
    setFokus(i);
    boxRef.current?.querySelector<HTMLElement>(`[data-spalte="${i}"]`)?.focus();
  };

  const taste = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const i = Number(e.currentTarget.dataset.spalte);
    if (e.key === "ArrowLeft") { e.preventDefault(); bewege(i - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); bewege(i + 1); }
    else if (e.key === "Home") { e.preventDefault(); bewege(0); }
    else if (e.key === "End") { e.preventDefault(); bewege(visible.length - 1); }
  };

  if (all.length === 0) {
    return (
      <div className={`rounded border border-line bg-card px-3 py-2 text-xs text-muted ${className ?? ""}`}>
        Noch keine Ereignisse in dieser Sitzung.
      </div>
    );
  }

  const kappTitle = gekappt
    ? `Der Anfang der Sitzung ist nicht mehr im Speicher: das Büro hält höchstens `
      + `${REPLAY_CAP.toLocaleString("de-DE")} Ereignisse und verwirft die ältesten.`
      + (versteckt > 0 ? ` Zusätzlich liegen ${versteckt} Sekunden links außerhalb des Fensters.` : "")
    : `${versteckt} Sekunden liegen links außerhalb des Fensters.`;

  return (
    <div className={`rounded border border-line bg-card px-2 py-1.5 ${className ?? ""}`}>
      <div
        ref={setzeBox}
        role="group"
        aria-label="Zeitleiste — ein Balken je Sekunde"
        // `overflow-hidden`: if the measurement miscounts by one column (rounding, scrollbar),
        // the bar should be clipped instead of tearing the layout behind it open.
        className="flex h-16 items-end overflow-hidden"
        style={{ gap: `${LUECKE_PX}px` }}
      >
        {(gekappt || versteckt > 0) && (
          <div
            title={kappTitle}
            aria-label={kappTitle}
            className={`h-full shrink-0 rounded-sm ${gekappt ? "bg-line" : "bg-line/50"}`}
            style={{ width: `${SPALTE_PX}px` }}
          />
        )}
        {visible.map((b, i) => {
          const gesamt = b.tools + b.says + b.thinks + b.errors;
          // Square-root scaling against the peak. `gesamt === 0` explicitly gives 0 and not the
          // minimum height; otherwise every empty second would claim something had happened.
          const h = gesamt === 0 || peak === 0
            ? 0
            : Math.max(6, Math.round((Math.sqrt(gesamt) / Math.sqrt(peak)) * 100));
          const label = balkenLabel(b);
          const ist = i === currentIdx;
          return (
            <button
              key={b.t}
              type="button"
              data-spalte={i}
              tabIndex={i === tabIdx ? 0 : -1}
              // A statement about the app ("this is where the room stands right now"), not
              // about the focus, which is why `aria-current` and not `aria-selected`.
              aria-current={ist ? "true" : undefined}
              aria-label={label}
              title={label}
              onClick={() => onSeek(b.t)}
              onKeyDown={taste}
              onFocus={() => setFokus(i)}
              className={"group flex h-full shrink-0 cursor-pointer flex-col justify-end rounded-sm "
                + "outline-none focus-visible:ring-1 focus-visible:ring-brand "
                + (ist ? "bg-brand/20" : "hover:bg-line/40")}
              style={{ width: `${SPALTE_PX}px` }}
            >
              {h > 0 ? (
                <span className="flex w-full flex-col overflow-hidden rounded-sm" style={{ height: `${h}%` }}>
                  {STAPEL.map((key) => {
                    const n = b[key];
                    if (n === 0) return null;
                    const r = SERIES.find((x) => x.key === key)!;
                    return (
                      <span
                        key={key}
                        className={`w-full ${r.css}`}
                        style={{ height: `${(n / gesamt) * 100}%` }}
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
        <span>{uhrzeit(visible[0])}</span>
        <span>
          {gekappt && <span className="mr-2" title={kappTitle}>⚠ Anfang verworfen</span>}
          {visible.length} {visible.length === 1 ? "Sekunde" : "Sekunden"}
        </span>
        <span>{uhrzeit(visible[visible.length - 1])}</span>
      </div>
    </div>
  );
}
