// Layer 0: the timeline as numbers.
//
// One bar per second, four series. What the component makes of it deliberately does **not**
// stand here, but it belongs to the understanding of the series:
//
//   · **Height = amount.** How much happened in this second at all (square-root scaling
//     against the peak, so that a single second with 400 events does not flatten all the
//     others).
//   · **Colour = composition.** What the second consisted of. A second dominated by red is a
//     second full of failures, and that is the place one clicks on.
//
// Both are display decisions and live in the React layer. Here stand only the counters;
// otherwise there would be two places where the same data is decided about.
//
// `toLocale*` is forbidden in layer 0 (PIXEL-CONTRACT.md 3.1): it depends on the language and
// time zone of the browser, so the same log would give two different timelines in two tabs.
// `labelOf` therefore delivers **numbers**, not text.

import type { Bucket, LogEntry } from "./types.ts";
import { TIMELINE_BUCKET_MS, TIMELINE_CAP } from "./const.ts";

/** Summarises the log into second bars.
 *
 *  The bars are **gapless**: even a second without any event gets its empty bar. Only that
 *  way is the horizontal a real time axis on which a click halfway along means half the time
 *  as well. Leaving empty seconds out would make a twenty minute pause optically as wide as a
 *  single second.
 *
 *  `t` is the **wall clock** in ms (rounded down to the second), not `engine.t`: it is
 *  exactly the value `Replay.seek` takes. Converting into simulation time would have to
 *  compute the whole replay just to label a bar.
 *
 *  Beyond `TIMELINE_CAP` the **oldest** bars are discarded, the same direction `REPLAY_CAP`
 *  truncates in. The view is a short term memory; the most recent is what one opens it for. */
export function bucketize(log: readonly LogEntry[], from?: number, to?: number): Bucket[] {
  if (log.length === 0) return [];

  let lo = from;
  let hi = to;
  if (lo === undefined || hi === undefined) {
    // Minimum and maximum, not the first and last entry: the log stands in `seq` order, and
    // that is not chronological under `WORKER_CONCURRENCY > 1`.
    let a = log[0].ts;
    let b = a;
    for (const e of log) {
      if (e.ts < a) a = e.ts;
      if (e.ts > b) b = e.ts;
    }
    if (lo === undefined) lo = a;
    if (hi === undefined) hi = b;
  }
  if (hi < lo) hi = lo;

  const first = floorBucket(lo);
  const last = floorBucket(hi);
  let count = (last - first) / TIMELINE_BUCKET_MS + 1;
  let start = first;
  if (count > TIMELINE_CAP) {
    start = last - (TIMELINE_CAP - 1) * TIMELINE_BUCKET_MS;
    count = TIMELINE_CAP;
  }

  const out: Bucket[] = new Array(count);
  for (let i = 0; i < count; i++) {
    out[i] = { t: start + i * TIMELINE_BUCKET_MS, says: 0, tools: 0, thinks: 0, errors: 0 };
  }

  for (const e of log) {
    const idx = (floorBucket(e.ts) - start) / TIMELINE_BUCKET_MS;
    if (idx < 0 || idx >= count) continue;
    const b = out[idx];
    for (const c of e.cmds) {
      switch (c.k) {
        case "say":
        case "deliver":
          // A handover is an utterance with a target, which for the timeline is the same.
          b.says++;
          break;
        case "tool":
          b.tools++;
          break;
        case "think":
          b.thinks++;
          break;
        case "toolEnd":
          // Three valued: `null` means *unknown* and counts towards nothing. Whoever writes
          // `ok ?? true` or `!ok` paints colour on guessed data.
          if (c.ok === false) b.errors++;
          break;
        case "done":
          if (!c.ok) b.errors++;
          break;
        case "status":
          if (c.status === "failed" || c.status === "loop_exhausted") b.errors++;
          break;
        default:
          break;
      }
    }
  }
  return out;
}

/** The numbers behind a bar, unformatted.
 *
 *  `h`/`m`/`s` are **UTC**. Only layer 2 knows the time zone; resolving it would need the
 *  environment of the browser here and turn the same data into two different timelines. */
export function labelOf(b: Bucket): {
  h: number; m: number; s: number;
  tools: number; says: number; thinks: number; errors: number;
} {
  const secs = Math.floor(b.t / 1000);
  return {
    h: Math.floor(secs / 3600) % 24,
    m: Math.floor(secs / 60) % 60,
    s: ((secs % 60) + 60) % 60,
    tools: b.tools,
    says: b.says,
    thinks: b.thinks,
    errors: b.errors,
  };
}

function floorBucket(ts: number): number {
  return Math.floor(ts / TIMELINE_BUCKET_MS) * TIMELINE_BUCKET_MS;
}
