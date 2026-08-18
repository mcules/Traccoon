// Layer 0: rewinding without snapshots.
//
// The whole trick: the engine knows time only through `tick(dt)`, and `dt` comes from the
// **event timestamps**, never from a wall clock. A bubble that was four seconds old back then
// is four seconds old again when rewinding. "Jump to t" is therefore simply "new engine,
// replay the log from the start": no state has to be serialised, versioned or migrated, and
// there is no second truth that could deviate from the first.
//
// Three things that are easily got wrong here:
//
//  1. **The log is never sorted by `ts`.** It stands in arrival order (`seq`). Two workers
//     running in parallel produce non-monotonic timestamps; sorting by `ts` would put the
//     effect before the cause, the tool result before the tool start.
//  2. **Gaps are clamped on both sides**: `dt = min(MAX_GAP_MS, max(0, ts - prev))`. Clamping
//     upwards alone is not enough: with several workers `ts` can run backwards relative to
//     `seq`, and a negative `dt` would turn the engine back.
//  3. **Equal timestamps take effect together.** A tool start and the derived `file_edit` of
//     the same row carry the same time; if the clock ran on in between, the picture would
//     depend on how many companions the backend happened to produce.

import type { Frame, LogEntry, Room } from "./types.ts";
import { Engine } from "./engine.ts";
import { LIVE_STEP_MS, MAX_GAP_MS, REPLAY_STEP_MS } from "./const.ts";

export class Replay {
  private log: readonly LogEntry[];
  private room: Room | undefined;
  private eng: Engine;
  /** Index of the next entry not yet applied. */
  private i = 0;
  /** Wall clock position in ms. Runs **monotonically**: an entry with an older `ts` does not
   *  move it back, it simply takes effect without advancing time (see the clamping above). */
  private p = 0;
  /** Timestamp of the last applied entry, the reference point of the gap clamping. */
  private anchor = 0;
  /** Simulation time that has already passed since `anchor`. */
  private spent = 0;
  private _from = 0;
  private _to = 0;

  constructor(log: readonly LogEntry[], room?: Room) {
    this.log = log;
    this.room = room;
    this.measure();
    this.eng = new Engine(room);
    this.p = this._from;
    this.anchor = this._from;
  }

  get position(): number { return this.p; }
  get from(): number { return this._from; }
  get to(): number { return this._to; }

  /** Jumps to a point in time. **Always** from the start, forwards as well.
   *
   *  A forward seek could in theory continue, but then the result would depend on where one
   *  stood before, and exactly that independence is the reason this design exists: `seek(t)`
   *  delivers the same picture no matter how one got there. */
  seek(ts: number): void {
    this.eng = new Engine(this.room);
    this.i = 0;
    this.p = this._from;
    this.anchor = this._from;
    this.spent = 0;
    this.run(ts, REPLAY_STEP_MS);
  }

  /** Runs on, without a rebuild. This is live operation: `dtMs` is the distance between two
   *  frames, measured and capped by the rAF loop in layer 2 (`MAX_FRAME_MS`). */
  advance(dtMs: number): void {
    if (!(dtMs > 0)) return;
    this.run(this.p + dtMs, LIVE_STEP_MS);
  }

  /** To the end of the log, so back into the present. If the position is already there it
   *  costs nothing; otherwise every click on "live" would be a full rebuild. */
  toLive(): void {
    if (this.p >= this._to) return;
    this.run(this._to, REPLAY_STEP_MS);
  }

  frame(): Frame {
    return this.eng.frame();
  }

  /** Takes a grown log without rebuilding the room.
   *
   *  Necessary for live operation: the recorder appends rows and `entries()` returns a new
   *  copy every time. Without this path, layer 2 would have to build a new `Replay` on every
   *  event and recompute three hours of log. If the already replayed beginning is no longer
   *  the same (truncation at the head, session change), an honest rebuild happens. */
  extend(log: readonly LogEntry[]): void {
    const keep =
      log.length >= this.i &&
      (this.i === 0 || (log[this.i - 1] !== undefined && log[this.i - 1].seq === this.log[this.i - 1].seq));
    this.log = log;
    this.measure();
    if (keep) return;
    const at = this.p;
    this.seek(at);
  }

  // ── The one integrator ─────────────────────────────────────────────────────

  /** Replays until `untilTs`. `seek` and `advance` **both** go through here, only with a
   *  different step size, and that this yields the same result is exactly the dt split
   *  invariance from PIXEL-CONTRACT.md 3.4. With two integrators one would have to test their
   *  equality; this way it is built in. */
  private run(untilTs: number, stepCap: number): void {
    while (this.i < this.log.length) {
      const at = this.log[this.i].ts;
      if (at > untilTs) break;
      this.settle(at, stepCap);
      // All commands of the same timestamp together, before the clock runs on.
      // The comparison is against `at`, not against `this.p`: a straggler with an older time
      // belongs to its own timestamp, not to the current position.
      while (this.i < this.log.length && this.log[this.i].ts === at) {
        for (const c of this.log[this.i].cmds) this.eng.apply(c);
        this.i++;
      }
      if (at > this.anchor) {
        this.anchor = at;
        this.spent = 0;
      }
    }
    this.settle(untilTs, stepCap);
  }

  /** Brings the simulation up to the wall clock time `ts`.
   *
   *  The core: **how much simulation time passes between two events depends only on the two
   *  timestamps**, `min(MAX_GAP_MS, ts - anchor)`, and not on how many pieces one got there
   *  in. The obvious way (clamping every piece on its own) would be exactly the mistake: a
   *  silence of 155 s would give 20 s in one go but the full 155 s in 100 ms steps, and `seek`
   *  and `advance` would show the same moment differently.
   *
   *  A side effect, deliberately accepted: after `MAX_GAP_MS` without an event the room stands
   *  still, live as well. That is invisible: bubbles are gone after `BUBBLE_MS`, paths end
   *  after a few seconds, and after that everybody is sitting anyway. The coffee clock
   *  (`IDLE_COFFEE_MS`) on the other hand only runs as long as *somebody* in the room produces
   *  events, so exactly when an idle figure stands out beside a busy one. */
  private settle(ts: number, stepCap: number): void {
    const want = clampGap(ts - this.anchor);
    if (want > this.spent) {
      this.integrate(want - this.spent, stepCap);
      this.spent = want;
    }
    if (ts > this.p) this.p = ts;
  }

  /** Splits a time span into steps of at most `stepCap`.
   *
   *  The step is **variable**: `run` only ever calls up to the next command, so the span here
   *  is already `min(stepCap, time until the next command)`. Together with the idle skip in
   *  `Engine.tick` that is the antidote to the one place where this design does not scale by
   *  itself: three hours of log at 50 ms would be 216 000 ticks. Checkpoints deliberately do
   *  **not** exist; they would only come if the measurement demanded them, and then as a cache
   *  recomputed from the log, never as transferred or stored state. */
  private integrate(span: number, stepCap: number): void {
    let left = span;
    while (left > 0) {
      const step = left < stepCap ? left : stepCap;
      this.eng.tick(step);
      left -= step;
    }
  }

  /** `from`/`to` are the minimum and maximum of the timestamps, not the first and last entry:
   *  the order is `seq`, and that is not chronological. */
  private measure(): void {
    if (this.log.length === 0) {
      this._from = 0;
      this._to = 0;
      return;
    }
    let lo = this.log[0].ts;
    let hi = lo;
    for (const e of this.log) {
      if (e.ts < lo) lo = e.ts;
      if (e.ts > hi) hi = e.ts;
    }
    this._from = lo;
    this._to = hi;
  }
}

/** Clamping on both sides. Upwards, because otherwise minutes of silence on a single `run` row
 *  would leave the viewer in front of a dead picture. Downwards, because `ts` can run
 *  backwards relative to `seq` and a negative `dt` would turn the engine back. */
function clampGap(raw: number): number {
  if (!(raw > 0)) return 0;
  return raw > MAX_GAP_MS ? MAX_GAP_MS : raw;
}

/** The headless golden test entry: pure, without React, without canvas, without a clock. The
 *  same log and the same moment give the same `Frame`, on every machine and in every time
 *  zone. This purity is the only reason the renderer can be golden tested at all later on;
 *  it is to be kept strictly. */
export function frameAt(log: readonly LogEntry[], ts: number): Frame {
  const r = new Replay(log);
  r.seek(ts);
  return r.frame();
}
