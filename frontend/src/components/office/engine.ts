// Layer 0, the room as a state machine.
//
// The engine is the only place where commands become an image. It has exactly two entrances:
// `apply(cmd)` (an event happened, instantly) and `tick(dt)` (time has passed). It never sees a
// clock, neither live nor while rewinding. That is why the same log yields the same room, bit
// for bit.
//
// The two design decisions everything hangs on:
//
//  1. **Every movement is a closed function of `t`.** A trip knows its start, arrival, hold and
//     return times; the position is an interpolation between them. The obvious way, adding a
//     piece of distance per tick, violates the dt split invariance (PIXEL-CONTRACT.md 3.4) in
//     two ways at once: the floating point sum `8 × (v·25)` is not bit identical to `v·200`, and
//     at a large `dt` the target is overshot in one jump instead of reached in eight steps. With
//     timestamps the question does not even arise.

//
//  2. **Every state transition carries its exact moment with it**, and the next transition
//     computes from *that* moment, not from `this.t`. If a trip ends at t=1400 and `tick` stands
//     at t=1600, the following trip still begins at 1400. Exactly here such engines usually
//     fail: `tick(200)` would push a transition back by up to 200 ms, `tick(25)×8` only by 25.
//     fail: `tick(200)` would push a transition back by up to 200 ms, `tick(25)×8` only by 25.
//
// Everything else follows from that: no tick counters, no "every n frames" thresholds, no roll
// per frame. Variation comes from `rnd01(mix(seed, SALT))`.

import type {
  ActorState, Cmd, Frame, Fx, Pt, Rack, Room, RunStatus, Seat, Verdict,
} from "./types.ts";
import { hash32, mix, rnd01 } from "./ids.ts";
import { POD_SEATS, ROOM, SEAT_DX, seatOf } from "./room.ts";
import {
  ARRIVE_STAGGER_MS, BUBBLE_MS, COFFEE_HOLD_MS, DONE_LINGER_MS, DONE_LINGER_SPREAD_MS,
  GATE_PULSE_MS, HEARD_MS, HUDDLE_HOLD_MS, HUDDLE_MIN, HUDDLE_WINDOW_MS, IDLE_COFFEE_MS,
  LINK_MS, MAX_ACTORS, MAX_QUEUED_TRIPS, PACE_SPREAD, SETTLE_MS,
  SPEAK_HOLD_MS, SPEED_PX_PER_S, TOOL_BUSY_MS,
} from "./const.ts";

// ── Salze (PIXEL-CONTRACT.md 3.2) ────────────────────────────────────────────
//
// Every place of use gets its own named salt. Two places with the same salt would be perfectly
// correlated, and then all the slow walkers would go through the door at the same time as well.
// These numbers are not knobs but names, which is why they stand here and not in `const.ts`
// (that is where what one adjusts lives).

const SALT_PACE = 0x9e37_79b1;
const SALT_LINGER = 0x85eb_ca6b;

/** How long the "waiting for a person" sign stands above the head: three pulses. Derived
 *  instead of independent, so that there is exactly one knob for it. */
const GATE_EMOTE_MS = 3 * GATE_PULSE_MS;

// ── Geometrie ────────────────────────────────────────────────────────────────
//
// It comes entirely from `room.ts`; the engine fixes not a single point itself.
// `constructor(room?)` takes a room anyway: that lets a checker put up a miniature geometry
// without a second floor plan having to be maintained here.

/** The distance at which one stands next to somebody to hand something over: one seat width.
 *  Deliberately the same number as the seat distance: whoever hands over stands where a chair
 *  would stand in case of doubt, and does not walk into the character's back. */
const DELIVER_GAP = SEAT_DX;

// ── Wege ─────────────────────────────────────────────────────────────────────
//
// A `Trip` is a completely precomputed path. It knows its four moments and derives every
// position from them; it counts nothing up. Exactly that makes it dt split invariant.

type TripKind = "arrive" | "deliver" | "coffee" | "huddle" | "exit";

interface Trip {
  kind: TripKind;
  /** Real work pushes in front of walks (coffee, huddle). */
  work: boolean;
  /** `this.t` at the time of queuing: a trip never begins before its order. */
  queuedAt: number;
  from: Pt;
  to: Pt;
  /** Target of the way back; unused on `exit`. */
  home: Pt;
  t0: number;
  tArrive: number;
  holdUntil: number;
  /** End of the way back; `=== holdUntil` when `back === false`. */
  tBack: number;
  back: boolean;
  targetId?: string;
  text?: string;
  /** The effect of the arrival (speaking, disappearing) is triggered exactly once. */
  fired: boolean;
}

/** What the engine knows about a character that is none of the drawing layer's business.
 *  Deliberately next to `ActorState` and not inside it: `Frame` is the contract with layer 1,
 *  and queues of trips do not belong in a drawing order. */
interface Priv {
  /** Floating point position; `ActorState.x/.sub.x` are only its whole and fractional parts. */
  fx: number;
  fy: number;
  /** Pace factor from the seed, `1 ± PACE_SPREAD`. */
  pace: number;
  trip: Trip | null;
  trips: Trip[];
  /** The exact moment the last trip ended: the start time of the next one. */
  freeAt: number;
  /** `t` of the last real stirring. The coffee clock runs from here. */
  lastAct: number;
  /** `t` of the last utterance: the window for detecting a huddle. */
  spokeAt: number;
  /** Planned departure through the door (`0` means none). */
  exitAt: number;
}

// ── Hilfsfunktionen ──────────────────────────────────────────────────────────

/** Colours the bubble edge and nothing else. `running` and everything unknown stays colourless:
 *  a run that is still running has no verdict. */
function verdictOf(s: RunStatus): Verdict {
  if (s === "success" || s === "planned") return "ok";
  if (s === "failed" || s === "loop_exhausted") return "err";
  if (s === "blocked") return "blocked";
  return null;
}

function dist(a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return Math.sqrt(dx * dx + dy * dy);
}

// ── The engine ───────────────────────────────────────────────────────────────

export class Engine {
  private room: Room;
  /** Insertion order is iteration order, hence a `Map` and never an object
   *  (PIXEL-CONTRACT.md 3.5: `"12"` would otherwise sort before `"run:8871"`). */
  private actors = new Map<string, ActorState>();
  private priv = new Map<string, Priv>();
  private fxs: Fx[] = [];
  private _t = 0;

  /** The server rack. The only state of the engine that hangs on no character, which is why it
   *  stands here and not in `ActorState`: a deployment belongs to the room, not to the run that
   *  triggered it (which walks through the door long before the build is done). */
  private rack: Rack = { state: "idle", since: 0, label: "" };

  /** Occupied pod seats and the boss seat. Released again on departure, otherwise a long
   *  session would be a room full of ghosts at the window after thirteen runs. */
  private podTaken = new Set<number>();
  private bossTaken = false;

  /** Moment of the arrival planned last: staggers a backlog (`ARRIVE_STAGGER_MS`). */
  private lastArriveAt = -ARRIVE_STAGGER_MS;
  /** Is a huddle running right now? Prevents every further utterance from starting a new one. */
  private huddleUntil = 0;

  constructor(room?: Room) {
    this.room = room ?? ROOM;
  }

  /** Simulation time in ms. Every animation phase derives from it, never from a tick counter,
   *  otherwise the image would depend on how often it was ticked. */
  get t(): number {
    return this._t;
  }

  // ── Eingang 1: Kommandos ───────────────────────────────────────────────────
  //
  // `apply` takes effect instantly at the current `this.t`. It reads no clock and never calls
  // `tick`, otherwise the effect of an event would depend on when it arrived, and the replay
  // would no longer be the same room.

  apply(cmd: Cmd): void {
    switch (cmd.k) {
      case "ensureActor": {
        const a = this.ensureActor(cmd.id, cmd.parent);
        a.role = cmd.role;
        a.issue = cmd.issue;
        a.phase = cmd.phase;
        a.model = cmd.model;
        if (cmd.parent !== undefined) a.parent = cmd.parent;
        break;
      }
      case "think": {
        const a = this.wake(cmd.id);
        a.think = cmd.text;
        a.thinkAt = this._t;
        this.touch(a);
        break;
      }
      case "say": {
        const a = this.wake(cmd.id);
        this.speak(a, cmd.text);
        this.maybeHuddle();
        break;
      }
      case "tool": {
        const a = this.wake(cmd.id);
        a.act = cmd.act;
        a.tool = cmd.tool;
        a.target = cmd.target;
        // A tool row from old data knows only *one* moment, not an interval (`tool_start` and
        // `tool_result` are synthesised from the same row, `duration_ms` is `null`). A constant
        // busy duration is honest about what we know: it claims no duration, it shows
        // "something is happening here". If the backend later delivers a real interval,
        // `toolEnd` simply clears `busy`, exactly one line of change, and the display becomes
        // real.
        a.busy = this._t + TOOL_BUSY_MS;
        this.emit("spark", a, this._t, TOOL_BUSY_MS);
        this.touch(a);
        break;
      }
      case "toolEnd": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.lastOk = cmd.ok;
        if (cmd.ok === false) a.fails++;
        else if (cmd.ok === true) a.resolved++;
        // `busy` deliberately stays: it runs out in `tick` (see above).
        this.touch(a);
        break;
      }
      case "edit": {
        const a = this.wake(cmd.id);
        a.edit = cmd.path;
        a.edits++;
        this.emit("drop", a, this._t, LINK_MS);
        this.touch(a);
        break;
      }
      case "spawn": {
        const parent = this.actors.get(cmd.parent);
        const child = this.actors.get(cmd.id);
        if (parent) {
          parent.link = { to: cmd.id, until: this._t + LINK_MS };
          const to = child ? { x: child.x, y: child.y } : this.room.door;
          this.fxs.push({
            kind: "link", x: parent.x, y: parent.y, to: { x: to.x, y: to.y },
            t0: this._t, until: this._t + LINK_MS, seed: parent.seed,
          });
          this.touch(parent);
        }
        if (child && child.parent === undefined) child.parent = cmd.parent;
        break;
      }
      case "deliver": {
        const a = this.wake(cmd.id);
        const to = this.actors.get(cmd.to);
        if (!to || to.retired || to.id === a.id) {
          // No target in the room, so it becomes an announcement into the room instead.
          this.speak(a, cmd.text ?? "");
          this.maybeHuddle();
          break;
        }
        const side = this.pos(a).x <= to.x ? -DELIVER_GAP : DELIVER_GAP;
        this.enqueue(a, {
          kind: "deliver", work: true, queuedAt: this._t,
          from: { x: 0, y: 0 }, to: { x: to.x + side, y: to.y }, home: { x: 0, y: 0 },
          t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true,
          targetId: to.id, text: cmd.text, fired: false,
        });
        this.touch(a);
        break;
      }
      case "gate": {
        const a = this.wake(cmd.id);
        a.waiting = true;
        a.gate = cmd.kind;
        // Whoever waits for a person does not set off any more. A trip already running may
        // finish: stopping in the middle of the room would look like a crash.
        a.say = cmd.text;
        a.sayAt = this._t;
        a.pose = a.pose === "walk" ? "walk" : "stand";
        this.emitAt("emote", a, this._t, GATE_EMOTE_MS, "!");
        this.touch(a);
        break;
      }
      case "resume": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.waiting = false;
        a.gate = undefined;
        this.touch(a);
        break;
      }
      case "status": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.status = cmd.status;
        a.verdict = verdictOf(cmd.status);
        break;
      }
      case "done": {
        const a = this.wake(cmd.id);
        a.done = this._t;
        a.doneOk = cmd.ok;
        a.verdict = cmd.ok ? "ok" : "err";
        if (cmd.text !== undefined && cmd.text !== "") this.speak(a, cmd.text);
        this.emitAt("emote", a, this._t, LINK_MS, cmd.ok ? "✓" : "✗");
        const p = this.privOf(a);
        // Staggered, so that twelve people do not walk to the door at the same time.
        const spread = rnd01(mix(a.seed, SALT_LINGER)) * DONE_LINGER_SPREAD_MS;
        p.exitAt = this._t + DONE_LINGER_MS + spread;
        this.touch(a);
        break;
      }
      case "deploy": {
        // **No expiry.** The rack state is set by the `start` and replaced by `ok`/`fail`/`back`:
        // no `until`, no substitute duration. That is the explicit opposite of `TOOL_BUSY_MS`,
        // which exists only because a tool row from old data knows no interval. Here we have
        // **both ends as real events**, so nothing has to be guessed.

        //
        // If the end never comes (deployer dead, container gone, watcher died) the rack keeps
        // glowing. **That is the truth, not a bug**: a deployment is running that nobody knows
        // the outcome of. An expiry would turn that state into a "finished" that stands nowhere
        // in the log.
        this.rack = { state: cmd.state, since: this._t, label: cmd.label };

        if (cmd.by !== undefined) {
          // The gesture: there and back. A `deliver` with a **target point instead of a target
          // actor**: no sixth `TripKind`, no actor without a run. `onArrive` reads `targetId`
          // only in the `deliver` branch and finds none here, so nothing happens on arrival
          // except standing still (`SPEAK_HOLD_MS`). Door detection, foot dust, spread of pace
          // and dt split invariance come for free.
          //
          // The walk happens on **every** change of state, not only on the `start`. That is not
          // sloppiness: 130 of the 186 deployments come from the time before the watcher and
          // tell only their **end** (`deployment_events` synthesises exactly one event). A rule
          // of "only on start" would leave the majority of all deployments without a gesture.
          const a = this.wake(cmd.by);
          const rack = this.room.rack;
          this.enqueue(a, {
            kind: "deliver", work: true, queuedAt: this._t,
            from: { x: 0, y: 0 }, to: { x: rack.x, y: rack.y }, home: { x: 0, y: 0 },
            t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true, fired: false,
          });
          this.touch(a);
        }

        // The verdict: the same `emote` kind `done` already uses, no fifth `FxKind`. `back`
        // gets "✗", because the deployment did fail; that it was also healed is told by the LED
        // rows (`ok` below), not by the sign above the rack.
        if (cmd.state !== "start") {
          const rack = this.room.rack;
          this.emitAt("emote", { x: rack.x, y: rack.y, seed: hash32(cmd.label) },
            this._t, LINK_MS, cmd.state === "ok" ? "✓" : "✗");
        }
        break;
      }
    }
  }

  // ── Eingang 2: Zeit ────────────────────────────────────────────────────────

  /** The one door time comes in through.
   *
   *  `dtMs` is **not** clamped: `MAX_FRAME_MS` is the caller's business (the rAF loop in layer
   *  2), because clamping here would break exactly the invariant the engine exists for:
   *  `tick(200)` would suddenly be `tick(100)`. */
  tick(dtMs: number): void {
    if (!(dtMs > 0)) return;
    this._t += dtMs;
    const t = this._t;
    for (const a of this.actors.values()) this.stepActor(a, this.privOf(a));
    // Effects die at their moment, not after n frames: even with one large tick exactly the
    // same set is left afterwards as with eight small ones.
    if (this.fxs.length > 0) this.fxs = this.fxs.filter((f) => f.until > t);
  }

  // ── Ausgang ────────────────────────────────────────────────────────────────

  /** The only thing layer 1 gets to see. The actors are sorted by `y` (painter's algorithm,
   *  back first) and lie in a list **of their own**: the engine never hands out its collection.
   *  Departed characters stay in it: the dock keeps showing them, the stage skips `retired`.
   *
   *  Departed characters stay in it: the dock keeps showing them, the stage skips `retired`.
   *  Two lists would be two truths. */
  frame(): Frame {
    const actors = [...this.actors.values()];
    actors.sort((x, y) => x.y - y.y); // stabil (ES2019) → Gleichstand behält Einfügereihenfolge
    // `rack` as a copy, for the same reason as `actors`: the engine never hands out its own
    // state. A caller overwriting `frame().rack.state` would otherwise change the room, and on
    // the next replay it would stand differently.
    return { t: this._t, actors, fx: this.fxs.slice(), rack: { ...this.rack } };
  }

  // ── Aktoren ────────────────────────────────────────────────────────────────

  private privOf(a: ActorState): Priv {
    const p = this.priv.get(a.id);
    if (p) return p;
    const fresh: Priv = {
      fx: a.x, fy: a.y, pace: 1, trip: null, trips: [],
      freeAt: this._t, lastAct: this._t, spokeAt: -HUDDLE_WINDOW_MS, exitAt: 0,
    };
    this.priv.set(a.id, fresh);
    return fresh;
  }

  /** Creates the character or returns the existing one. Idempotent. */
  private ensureActor(id: string, parent?: string): ActorState {
    const found = this.actors.get(id);
    if (found) return found;
    if (this.actors.size >= MAX_ACTORS) this.evict();

    const seed = hash32(id);
    const a: ActorState = {
      id, role: "", issue: null, phase: null, model: null,
      x: 0, y: 0, sub: { x: 0, y: 0 },
      // Begins standing, not walking: until the staggered entry is its turn the character waits
      // in front of the door, and a walking character that does not move would look broken.
      pose: "stand", flip: false, away: false,
      verdict: null, status: "running", deskIndex: -2,
      busy: 0, waiting: false, fails: 0, resolved: 0, edits: 0,
      seed,
    };
    if (parent !== undefined) a.parent = parent;
    this.actors.set(id, a);

    const p = this.privOf(a);
    p.pace = 1 + (rnd01(mix(seed, SALT_PACE)) - 0.5) * 2 * PACE_SPREAD;
    this.seat(a);
    this.enter(a, p);
    return a;
  }

  /** Wakes a character that has to exist because something is about to happen with it. A
   *  `retired` agent that speaks again comes back through the same door it left by, which does
   *  happen with continuation runs after a context compaction. */
  private wake(id: string): ActorState {
    const a = this.ensureActor(id);
    if (a.retired) {
      a.retired = undefined;
      a.away = false;
      a.done = undefined;
      const p = this.privOf(a);
      p.trip = null;
      p.trips.length = 0;
      p.exitAt = 0;
      this.seat(a);
      this.enter(a, p);
    }
    return a;
  }

  /** Seat assignment: the root run (no `parent`) gets the boss seat, everybody else goes
   *  through `seatOf`, `hash32(id) % 12` with linear probing and no queue. If everything is
   *  taken, the character becomes a ghost at the window (`deskIndex === -2`). A queue would
   *  carry state across a reset and the same agent would sit elsewhere on a second viewing. */
  private seat(a: ActorState): void {
    if (a.parent === undefined && !this.bossTaken) {
      this.bossTaken = true;
      a.deskIndex = -1;
      a.away = false;
      return;
    }
    const slot = seatOf(a.id, this.podTaken);
    if (slot >= 0) this.podTaken.add(slot);
    a.deskIndex = slot;
    a.away = slot === -2;
  }

  private unseat(a: ActorState): void {
    if (a.deskIndex === -1) this.bossTaken = false;
    else if (a.deskIndex >= 0) this.podTaken.delete(a.deskIndex);
    a.deskIndex = -2;
  }

  /** The seat of this character, from **this** room and not from `podSeat()`: the constructor
   *  may have been given a different geometry, and two sources for the same point would be one
   *  zu viel. */
  private seatOfActor(a: ActorState): Seat | undefined {
    if (a.deskIndex === -2) return undefined;
    return this.room.seats[a.deskIndex === -1 ? POD_SEATS : a.deskIndex];
  }

  /** The foot point this character is at home at. */
  private home(a: ActorState): Pt {
    return this.seatOfActor(a)?.sit ?? this.room.away;
  }

  private homeFlip(a: ActorState): boolean {
    return this.seatOfActor(a)?.flip ?? false;
  }

  /** Arrival through the door. When several runs come in the same instant (the backfill of an
   *  event window is the normal case) they are staggered by `ARRIVE_STAGGER_MS`, otherwise
   *  twelve people jump in at once and one sees nothing. */
  private enter(a: ActorState, p: Priv): void {
    const at = Math.max(this._t, this.lastArriveAt + ARRIVE_STAGGER_MS);
    this.lastArriveAt = at;
    const door = this.room.door;
    p.fx = door.x;
    p.fy = door.y;
    this.sync(a, p);
    p.freeAt = at;
    p.lastAct = at;
    p.trip = null;
    p.trips.length = 0;
    this.enqueue(a, {
      kind: "arrive", work: true, queuedAt: at,
      from: { x: door.x, y: door.y }, to: this.home(a), home: this.home(a),
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: false, fired: false,
    });
  }

  /** Make room when the room is full: first those already outside, then the finished ones, and
   *  last the oldest character of all. The insertion order of the `Map` is
   *  Altersreihenfolge. */
  private evict(): void {
    let victim: ActorState | undefined;
    for (const a of this.actors.values()) if (a.retired) { victim = a; break; }
    if (!victim) for (const a of this.actors.values()) if (a.done !== undefined) { victim = a; break; }
    if (!victim) for (const a of this.actors.values()) { victim = a; break; }
    if (!victim) return;
    this.unseat(victim);
    this.actors.delete(victim.id);
    this.priv.delete(victim.id);
  }

  // ── Regungen ───────────────────────────────────────────────────────────────

  /** "Something happened": resets the coffee clock. */
  private touch(a: ActorState): void {
    this.privOf(a).lastAct = this._t;
  }

  private speak(a: ActorState, text: string, at?: number): void {
    const t = at ?? this._t;
    a.say = text;
    a.sayAt = t;
    a.think = undefined;
    a.thinkAt = undefined;
    const p = this.privOf(a);
    p.spokeAt = t;
    p.lastAct = t;
  }

  private pos(a: ActorState): Pt {
    const p = this.privOf(a);
    return { x: p.fx, y: p.fy };
  }

  /** Writes the floating point position back into the actor: whole scene pixels into `x`/`y`,
   *  the rest into the subpixel accumulator. Rounding happens only while drawing
   *  (PIXEL-CONTRACT.md 2.3); whoever rounded here would lose every movement at small `dt`. */
  private sync(a: ActorState, p: Priv): void {
    const ix = Math.floor(p.fx);
    const iy = Math.floor(p.fy);
    a.x = ix;
    a.sub.x = p.fx - ix;
    a.y = iy;
    a.sub.y = p.fy - iy;
  }

  private emit(kind: "spark" | "drop", a: ActorState, t0: number, ms: number): void {
    this.fxs.push({ kind, x: a.x, y: a.y, t0, until: t0 + ms, seed: a.seed });
  }

  /** An emote above a **place**, not necessarily above a character. `ActorState` satisfies the
   *  shape by itself, so one parameter is enough for both cases and the server rack needs no
   *  emit function of its own. */
  private emitAt(kind: "emote", at: { x: number; y: number; seed: number },
                 t0: number, ms: number, text: string): void {
    this.fxs.push({ kind, x: at.x, y: at.y, t0, until: t0 + ms, text, seed: at.seed });
  }

  // ── Huddle ─────────────────────────────────────────────────────────────────

  /** When `HUDDLE_MIN` characters speak within `HUDDLE_WINDOW_MS` they meet at the round table.
   *  The check sits in `apply`, not in `tick`: that ties it to the timestamp of the event and
   *  not to where a tick boundary happened to fall. */
  private maybeHuddle(): void {
    if (this._t < this.huddleUntil) return;
    const since = this._t - HUDDLE_WINDOW_MS;
    const crowd: ActorState[] = [];
    for (const a of this.actors.values()) {
      if (a.retired || a.waiting || a.done !== undefined) continue;
      if (this.privOf(a).spokeAt >= since) crowd.push(a);
    }
    if (crowd.length < HUDDLE_MIN) return;
    const spots = this.room.huddle;
    let k = 0;
    for (const a of crowd) {
      const p = this.privOf(a);
      p.spokeAt = -HUDDLE_WINDOW_MS; // does not count twice
      const spot = spots[k % spots.length];
      k++;
      if (!spot) continue;
      this.enqueue(a, {
        kind: "huddle", work: false, queuedAt: this._t,
        from: { x: 0, y: 0 }, to: { x: spot.x, y: spot.y }, home: { x: 0, y: 0 },
        t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true, fired: false,
      });
    }
    this.huddleUntil = this._t + HUDDLE_HOLD_MS;
  }

  // ── Wegewarteschlange ──────────────────────────────────────────────────────

  /** Queues a trip. Real work (`deliver`, `arrive`, `exit`) pushes in **front of** walks: a
   *  coffee run must not delay a handover. More than `MAX_QUEUED_TRIPS` bookings are dropped,
   *  otherwise after a flood of events a character would spend minutes working off orders that
   *  are long overtaken. */
  private enqueue(a: ActorState, tr: Trip): void {
    const p = this.privOf(a);
    if (tr.work) {
      let i = p.trips.length;
      while (i > 0 && !p.trips[i - 1].work) i--;
      p.trips.splice(i, 0, tr);
    } else {
      p.trips.push(tr);
    }
    if (p.trips.length > MAX_QUEUED_TRIPS) p.trips.length = MAX_QUEUED_TRIPS;
  }

  /** Computes a trip completely. From here on it is a function of time. */
  private startTrip(a: ActorState, p: Priv, tr: Trip): void {
    const t0 = Math.max(p.freeAt, tr.queuedAt);
    const speed = SPEED_PX_PER_S * p.pace;
    tr.from = { x: p.fx, y: p.fy };
    tr.home = tr.kind === "exit" ? { x: p.fx, y: p.fy } : this.home(a);
    tr.t0 = t0;
    tr.tArrive = t0 + (dist(tr.from, tr.to) / speed) * 1000;
    const hold =
      tr.kind === "deliver" ? SPEAK_HOLD_MS :
      tr.kind === "coffee" ? COFFEE_HOLD_MS :
      tr.kind === "huddle" ? HUDDLE_HOLD_MS : 0;
    tr.holdUntil = tr.tArrive + hold;
    tr.tBack = tr.back ? tr.holdUntil + (dist(tr.to, tr.home) / speed) * 1000 : tr.holdUntil;
    tr.fired = false;
    p.trip = tr;
  }

  private makeCoffee(at: number): Trip {
    const c = this.room.coffee;
    return {
      kind: "coffee", work: false, queuedAt: at,
      from: { x: 0, y: 0 }, to: { x: c.x, y: c.y }, home: { x: 0, y: 0 },
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true, fired: false,
    };
  }

  private makeExit(at: number): Trip {
    const d = this.room.door;
    return {
      kind: "exit", work: true, queuedAt: at,
      from: { x: 0, y: 0 }, to: { x: d.x, y: d.y }, home: { x: 0, y: 0 },
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: false, fired: false,
    };
  }

  // ── One actor, one tick ────────────────────────────────────────────────────

  private stepActor(a: ActorState, p: Priv): void {
    const t = this._t;

    // Idle skip. Not cosmetics: `Replay.seek` over three hours of log is tens of thousands of
    // ticks, and in an office most people sit still most of the time. Whoever has nothing
    // planned costs one condition here instead of half a dozen computations.
    if (a.retired) return;
    if (
      !p.trip && p.trips.length === 0 && a.busy === 0 && !a.waiting &&
      a.sayAt === undefined && a.thinkAt === undefined &&
      a.link === undefined && a.heard === undefined &&
      p.exitAt === 0 && t < p.lastAct + IDLE_COFFEE_MS
    ) return;

    // Expiring states. All thresholds are moments, not counters (PIXEL-CONTRACT.md 3.4).
    if (a.busy !== 0 && t >= a.busy) {
      a.busy = 0;
      a.act = undefined;
      a.tool = undefined;
      a.target = undefined;
    }
    if (a.sayAt !== undefined && t >= a.sayAt + BUBBLE_MS) {
      a.say = undefined;
      a.sayAt = undefined;
    }
    if (a.thinkAt !== undefined && t >= a.thinkAt + BUBBLE_MS) {
      a.think = undefined;
      a.thinkAt = undefined;
    }
    if (a.heard !== undefined && t >= a.heard) a.heard = undefined;
    if (a.link !== undefined && t >= a.link.until) a.link = undefined;

    // Departure: due at the stored moment, not at the moment of the tick.
    if (p.exitAt !== 0 && t >= p.exitAt) {
      const at = p.exitAt;
      p.exitAt = 0;
      p.trips.length = 0;
      p.freeAt = Math.max(p.freeAt, at);
      this.enqueue(a, this.makeExit(at));
    }

    // Coffee: the only sign of life in a long chain of tools.
    if (
      p.exitAt === 0 && !a.waiting && !p.trip && p.trips.length === 0 &&
      a.deskIndex !== -2 && !a.retired && t >= p.lastAct + IDLE_COFFEE_MS
    ) {
      const at = p.lastAct + IDLE_COFFEE_MS;
      p.lastAct = at; // the next coffee at the earliest after another idle period
      this.enqueue(a, this.makeCoffee(at));
    }

    this.stepTrips(a, p);
  }

  /** Works off the trips. The loop is necessary because one large tick can skip several trips,
   *  and each of them begins exactly when the previous one ended, not only at `this.t`. Exactly
   *  that makes `tick(200)` and `tick(25)×8` equal. */
  private stepTrips(a: ActorState, p: Priv): void {
    const t = this._t;
    for (let guard = 0; guard < 16; guard++) {
      let tr = p.trip;
      if (!tr) {
        const next = p.trips.shift();
        if (!next) {
          // At home and idle.
          if (!a.retired) {
            const h = this.home(a);
            p.fx = h.x;
            p.fy = h.y;
            this.sync(a, p);
            a.pose = a.deskIndex === -2 ? "stand" : "sit";
            a.flip = this.homeFlip(a);
          }
          return;
        }
        this.startTrip(a, p, next);
        tr = next;
      }

      // The way there. Before `t0` the character still stands at the starting point: that is the
      // staggered arrival waiting in front of the door until it is its turn.
      if (t < tr.tArrive) {
        this.place(a, p, tr.from, tr.to, tr.t0, tr.tArrive);
        a.pose = t < tr.t0 ? "stand" : "walk";
        return;
      }
      if (!tr.fired) {
        // First set exactly onto the target, then trigger the effect. Otherwise `onArrive` would
        // read the position from the *previous* tick, and at `tick(200)` that lies elsewhere
        // than at `tick(25)×8`. The facing direction of the listener and the starting point of
        // the handover line hang on exactly that.
        p.fx = tr.to.x;
        p.fy = tr.to.y;
        this.sync(a, p);
        tr.fired = true;
        this.onArrive(a, p, tr);
        if (tr.kind === "exit") { p.trip = null; p.freeAt = tr.tArrive; return; }
      }
      // Aufenthalt am Ziel.
      if (t < tr.holdUntil) {
        p.fx = tr.to.x;
        p.fy = tr.to.y;
        this.sync(a, p);
        // `SETTLE_MS`: after the arrival there is a moment of shuffling before the pose changes,
        // otherwise the character freezes mid stride.
        a.pose = t < tr.tArrive + SETTLE_MS ? "walk" : "stand";
        return;
      }
      // The way back.
      if (tr.back && t < tr.tBack) {
        this.place(a, p, tr.to, tr.home, tr.holdUntil, tr.tBack);
        a.pose = "walk";
        return;
      }
      // Done: the next trip begins exactly here.
      p.freeAt = tr.back ? tr.tBack : tr.holdUntil;
      p.trip = null;
      if (!a.retired) {
        p.fx = tr.back ? tr.home.x : tr.to.x;
        p.fy = tr.back ? tr.home.y : tr.to.y;
        this.sync(a, p);
        a.pose = a.deskIndex === -2 ? "stand" : "sit";
        a.flip = this.homeFlip(a);
      }
    }
  }

  private place(a: ActorState, p: Priv, from: Pt, to: Pt, t0: number, t1: number): void {
    const u = t1 <= t0 ? 1 : Math.min(1, Math.max(0, (this._t - t0) / (t1 - t0)));
    p.fx = from.x + (to.x - from.x) * u;
    p.fy = from.y + (to.y - from.y) * u;
    this.sync(a, p);
    if (to.x !== from.x) a.flip = to.x < from.x;
  }

  /** The effect of the arrival, triggered at the **planned** arrival moment `tr.tArrive` and not
   *  at `this.t`. Otherwise the bubble's start time would depend on how coarsely it was ticked. */
  private onArrive(a: ActorState, p: Priv, tr: Trip): void {
    switch (tr.kind) {
      case "arrive":
        a.pose = "sit";
        a.flip = this.homeFlip(a);
        break;
      case "deliver": {
        const to = tr.targetId !== undefined ? this.actors.get(tr.targetId) : undefined;
        if (tr.text !== undefined && tr.text !== "") this.speak(a, tr.text, tr.tArrive);
        if (to) {
          to.heard = tr.tArrive + HEARD_MS;
          // **Never read the live position of another actor.** `tick` runs the actors in
          // insertion order; whoever comes after us still stands at the state of the previous
          // tick. At `tick(200)` that is 200 ms old, at `tick(25)×8` only 25 ms, and the dt
          // split invariance is gone. The seat on the other hand is a pure function of the
          // place. (The line that travels along draws `ActorState.link` anyway, which carries
          // only an id and resolves the position while drawing.)
          const anchor = this.home(to);
          this.fxs.push({
            kind: "link", x: a.x, y: a.y, to: { x: anchor.x, y: anchor.y },
            t0: tr.tArrive, until: tr.tArrive + LINK_MS, seed: a.seed,
          });
          a.link = { to: to.id, until: tr.tArrive + LINK_MS };
        }
        break;
      }
      case "exit":
        // Through the door. The seat is freed: the next run should have it.
        this.unseat(a);
        a.retired = true;
        a.away = true;
        a.say = undefined;
        a.sayAt = undefined;
        a.think = undefined;
        a.thinkAt = undefined;
        p.trips.length = 0;
        break;
      case "coffee":
      case "huddle":
        break;
    }
  }
}
