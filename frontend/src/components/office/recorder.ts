// Layer 0: the log. Everything the room has ever seen, in arrival order.
//
// The recorder is the **only** place where deduplication happens. That is not thrift but the
// reason it exists: Traccoon's events come in over three paths, and all three can overlap.
//
//   · On connecting, the WS bridge buffers, then fetches the history over `GET …/events` and
//     discards the buffered ones with `seq <= seq_to`; the boundary is tight and may be,
//     because a duplicate gets stuck here.
//   · A reconnect after a network hiccup delivers rows that were already there.
//   · A manual reload in the frontend pulls the window once more.
//
// A `Set<number>` over `seq` handles all three in one place. Every second check somewhere else
// would be a place where they later drift apart.

import type { Ev, LogEntry, Roster } from "./types.ts";
import { REPLAY_CAP } from "./const.ts";
import { mapEvent } from "./mapEvent.ts";

export class Recorder {
  /** One-number staleness check. It moves on **every** growth and on **every** discard, so a
   *  comparison of numbers is enough to know whether a running `Replay` still sits on the log
   *  it saw while being built. A content check would be O(n) per frame. */
  revision = 0;

  private log: LogEntry[] = [];
  /** Seen `seq`. Stays complete even when the log is truncated at the front: a row that
   *  disappeared because of the cap must not come back at the **end** through a late
   *  duplicate, where it would stand wrongly in time and the room would play it a second time. */
  private seen = new Set<number>();
  private roster: Roster = [];
  private droppedAny = false;

  /** The roster comes from the same response as the events and answers what is missing in the
   *  events (role, parent run, model). `mapEvent` needs it; the recorder only passes it
   *  through. */
  setRoster(roster: Roster): void {
    this.roster = roster;
  }

  /** Takes an event in.
   *
   *  `false` means: after this call the log has **not** simply grown by this one row. Either
   *  the `seq` was already there (duplicate) or the cap displaced something at the front for
   *  it. Both are reasons not to blindly continue a running replay, but the reliable answer to
   *  that is `revision`, not this return value. */
  push(ev: Ev): boolean {
    if (this.seen.has(ev.seq)) return false;
    this.seen.add(ev.seq);

    // The mapping table stands in `mapEvent.ts` and nowhere else. If even one special case
    // stood here, there would be two places where a tool name becomes a picture.
    const cmds = mapEvent(ev, this.roster);
    this.log.push({ ts: parseTs(ev.ts), seq: ev.seq, cmds });
    this.revision++;

    let dropped = false;
    while (this.log.length > REPLAY_CAP) {
      this.log.shift();
      this.droppedAny = true;
      this.revision++;
      dropped = true;
    }
    return !dropped;
  }

  /** Hands out a **copy**.
   *
   *  The bug this prevents: a live cursor running over a log whose `shift()` slides away under
   *  it. Every discard at the head shifts all indices by one, the cursor skips the shifted
   *  entries, and edits and whole agents never turn up in the room. The bug looks like an
   *  engine bug and is an aliasing bug; that is why the own collection never leaves this
   *  object.
   *
   *  The `LogEntry` themselves are shared, not copied: they are immutable after creation, and
   *  deep copying 20 000 entries per frame would be the most expensive line of the whole
   *  view. */
  entries(): readonly LogEntry[] {
    return this.log.slice();
  }

  /** Bounds of the log.
   *
   *  `t0`/`t1` are the minimum and maximum of the **wall clock**, not the first and last
   *  entry: the log stands in `seq` order, and under `WORKER_CONCURRENCY > 1` the timestamp
   *  that arrived last can be older than an earlier one. For the timeline the latest time
   *  counts, not the latest row.
   *
   *  `seq0`/`seq1` on the other hand are the bounds of the arrival order, which the reconnect
   *  protocol needs (discarding buffered events with `seq <= seq_to`), and they are something
   *  other than the time bounds. Both from one pass, because both cost the same pass. */
  bounds(): { t0: number; t1: number; seq0: number; seq1: number; dropped: boolean } {
    if (this.log.length === 0) {
      return { t0: 0, t1: 0, seq0: 0, seq1: 0, dropped: this.droppedAny };
    }
    let t0 = this.log[0].ts;
    let t1 = t0;
    let seq0 = this.log[0].seq;
    let seq1 = seq0;
    for (const e of this.log) {
      if (e.ts < t0) t0 = e.ts;
      if (e.ts > t1) t1 = e.ts;
      if (e.seq < seq0) seq0 = e.seq;
      if (e.seq > seq1) seq1 = e.seq;
    }
    return { t0, t1, seq0, seq1, dropped: this.droppedAny };
  }

  /** On a session change. `revision` is **counted up**, not set to 0: a replay that happened
   *  to have remembered `revision === 0` would otherwise think itself current. */
  reset(): void {
    this.log = [];
    this.seen.clear();
    this.roster = [];
    this.droppedAny = false;
    this.revision++;
  }
}

/** ISO-8601 to ms. `Date.parse` is allowed and necessary: it is not a clock but a pure
 *  function on a string (`new Date`/`Date.now` would be neither). An unreadable timestamp
 *  becomes 0 instead of `NaN`; `NaN` would eat its way through every time computation of the
 *  replay and freeze the room silently. */
function parseTs(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : 0;
}
