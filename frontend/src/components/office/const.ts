// Layer 0: all the numbers of the "office" in one place.
//
// Whoever finds a number in the code that is missing here has found a bug: scattered constants
// are the fastest way to let stage and timeline drift apart.
//
// All `*_MS` are milliseconds in **simulation time** (`engine.t`), not wall clock.
// No `enum` (PIXEL-CONTRACT.md rule 5), only flat `const` and `as const` objects.

// ── Resolution ───────────────────────────────────────────────────────────────

/** The **art level**: the coordinate system that is drawn in. Sprites, bubbles, fonts,
 *  furniture and the camera compute in these units. Until 2026-08-07 it was identical to the
 *  frame buffer, which is why everything here used to be called "buffer pixels". */
export const ART = { w: 480, h: 270 } as const;

/** How many buffer pixels one art unit is wide.
 *
 *  The reason for the separation: at 480x270 a drawn pixel fills a 4x4 block on a 1080p
 *  screen, and a figure is 16x24, so from three metres away it is a thumbnail. With
 *  ART_SCALE = 2 the buffer is 960x540; whoever keeps drawing in art units gets exactly the
 *  same picture (every unit becomes a 2x2 block), and whoever wants to draw more finely now
 *  has twice the resolution for it. That is exactly how the office moves from coarse to fine
 *  in stages without anything being broken in between. */
export const ART_SCALE = 2;

/** The frame buffer in real pixels. Results from art level times scale, never set by hand,
 *  otherwise camera, hit test and blit drift apart. */
export const PIX = { w: ART.w * ART_SCALE, h: ART.h * ART_SCALE } as const;

/** The room the simulation computes in. Positions live here. */
export const SCENE = { w: 1600, h: 900 } as const;

/** `SCENE → PIX`, **for positions only**. Sprites are not scaled along: a figure is 16x24
 *  buffer pixels, not 16x24 scene pixels. Rule 1 of the pixel contract, and the rule whose
 *  violation miscalculates the whole art budget by a factor of 3. */
export const POS_SCALE = 0.3;

// ── Bewegung ─────────────────────────────────────────────────────────────────

/** Grundtempo in **Szenen**pixeln je Sekunde (≈45 Pufferpixel/s). */
export const SPEED_PX_PER_S = 150;
/** Spread of the pace per figure: `1 ± 0.17`, from the seed. */
export const PACE_SPREAD = 0.17;
/** Arrival at the target: this long the figure keeps stepping before the pose changes. */
export const SETTLE_MS = 600;
/** More than two queued paths are discarded; otherwise a figure would run off assignments for
 *  minutes after a burst of events, long after they have been superseded. */
export const MAX_QUEUED_TRIPS = 2;
/** If several runs enter the room at the same time, they come through the door staggered. */
export const ARRIVE_STAGGER_MS = 2600;

// ── Blasen & Aufmerksamkeit ──────────────────────────────────────────────────

/** Standzeit einer Sprechblase. */
export const BUBBLE_MS = 5000;
/** Schreibmaschineneffekt: Zeichen je Sekunde. */
export const TYPE_CPS = 40;
/** How long a figure stays in the speaking pose after speaking. */
export const SPEAK_HOLD_MS = 2500;
/** Visibility of a spawn or handover line. */
export const LINK_MS = 2200;
/** How long a listener keeps their head turned. */
export const HEARD_MS = 4000;

// ── Huddle ───────────────────────────────────────────────────────────────────

/** From three simultaneously active runs on it becomes a huddle at the round table. */
export const HUDDLE_MIN = 3;
/** Window in which those three have to come together. */
export const HUDDLE_WINDOW_MS = 7500;
/** Minimum duration before the huddle breaks up again. */
export const HUDDLE_HOLD_MS = 4000;

// ── Leerlauf & Abgang ────────────────────────────────────────────────────────

/** After a minute and a half without an event the figure fetches coffee, the only sign of
 *  life in a long tool chain. */
export const IDLE_COFFEE_MS = 90_000;
export const COFFEE_HOLD_MS = 4000;
/** After `done` the figure stands around a while before going through the door … */
export const DONE_LINGER_MS = 1800;
/** … spread by up to this much per figure (from the seed), so that not everybody leaves at once. */
export const DONE_LINGER_SPREAD_MS = 4200;

// ── Werkzeuge & Gates ────────────────────────────────────────────────────────

/** Ersatzdauer eines Werkzeugschritts.
 *
 *  On the legacy path (`kind=''` rows from the time before the worker instrumentation) a tool
 *  call knows only *one* moment: `tool_start` and `tool_result` are synthesised from the same
 *  row, and `duration_ms` is `null`. Without a substitute duration the tool pose would flash
 *  for 0 ms and the room would look as if nobody were doing anything. If a real `duration_ms`
 *  is present, that one wins. */
export const TOOL_BUSY_MS = 1400;

/** Pulse duration of the "waiting for a human" signal above the figure.
 *
 *  A gate (`ask_human`, permission request, plan approval) is the most common reason for a
 *  silent room, and a silent room without a visible reason reads like a crash. */
export const GATE_PULSE_MS = 1200;

// ── Zeit, Replay, Kappung ────────────────────────────────────────────────────

/** Upper bound for the pause between two events.
 *
 *  Deliberately tight. A `check` build regularly produces minutes of silence on a single
 *  `run` tool row; chosen more generously one would sit in front of a dead picture for minutes. A shortened time jump is the lesser evil.
 *
 *  Applied on **both sides**: `dt = min(MAX_GAP_MS, max(0, ts - prev))`. The `max(0, …)`
 *  is not theoretical: under `WORKER_CONCURRENCY > 1` `ts` can run backwards relative to
 *  `seq`, and a negative `dt` would turn the engine backwards. */
export const MAX_GAP_MS = 20_000;

/** `seek` replays no more log rows than this; above it, truncation happens from the **oldest** end. */
export const REPLAY_CAP = 20_000;
/** Step size when rewinding: `min(REPLAY_STEP_MS, timeToNextCmd)`. */
export const REPLAY_STEP_MS = 250;
/** Schrittweite im Livebetrieb. */
export const LIVE_STEP_MS = 100;
/** Upper bound for a single `tick(dt)`, protecting against a tab that lay in the background. */
export const MAX_FRAME_MS = 100;

// ── Kiosk (Wandschirm) ───────────────────────────────────────────────────────

/** This long a chosen camera target stays unchanged.
 *
 *  Without this lock the camera would jitter between two sparks twelve times a second, and
 *  from three metres away that is no longer a picture but a twitch. 6 s are about four
 *  `TOOL_BUSY_MS`, so long enough to see a tool step through to the end. */
export const KIOSK_HOLD_MS = 6000;

/** This long without a single new `Fx`, then the camera pulls back to the whole room.
 *
 *  Deliberately more generous than `MAX_GAP_MS`: only after that is nothing really going on,
 *  and then the whole silent room is the honest picture, not a table zoomed in on by chance. */
export const KIOSK_IDLE_MS = 20_000;

// ── Capacities ───────────────────────────────────────────────────────────────

/** The room shows no more figures at once; the oldest finished ones leave first. */
export const MAX_ACTORS = 24;
/** 12 pod seats plus the chief's seat. The seat choice is `hash32(runId) % 12`, deterministic, without a queue. */
export const MAX_SEATS = 13;

// ── Zeitleiste ───────────────────────────────────────────────────────────────

/** One bar per second. */
export const TIMELINE_BUCKET_MS = 1000;
/** No more bars than this are kept. */
export const TIMELINE_CAP = 1200;
/** This many columns are what the display summarises to. */
export const TIMELINE_COLUMNS = 220;

// ═══ The floor plan ══════════════════════════════════════════════════════════
//
// The office is not one hall any more, it is **rooms**. A corridor along the entrance, an open
// plan area for the desks, a meeting room and an archive behind a partition wall.
//
// That this is possible at all is new: until the route finding existed (`room.route`), every
// figure walked a straight line from where it stood to where it wanted to go, and any interior
// wall would have been walked straight through. Walls and route finding are the same feature;
// one without the other is either a hall or a bug.
//
// The segments stand **here** and not in `room.ts`, because both layers need them: layer 0
// makes obstacles out of them, layer 1 draws them, and layer 1 must not see `room.ts` (rule 4).
// `const.ts` is the one file both may import, which is exactly what it is for. Every other
// piece of geometry in this room is held twice and kept in step by a check; this one is not
// held twice at all, which is better.

/** A piece of wall, in **art units**, given by its top left corner. Doorways are not marked:
 *  a doorway is the gap between two segments, and a gap needs no data. */
export interface Wall { x: number; y: number; w: number; h: number }

/** Thickness of an interior wall. Six art units is what reads as a wall rather than as a line
 *  at this scale, and it leaves the rooms their floor. */
export const WALL_T = 6;

export const WALLS: readonly Wall[] = [
  // The corridor along the entrance, closed off towards the open plan area. The doorway lies
  // between x 150 and 190, and that is **measured, not chosen**: it has to open onto the aisle
  // between the desk clusters. The first attempt put it at 240 to 280, straight onto the desks
  // of the middle cluster, and the route finding then found no way at all from the corridor
  // into the open plan area and fell back to the straight line, so everybody walked through the
  // wall. The check `routes avoid the furniture` is what showed it.
  { x: 8, y: 80, w: 142, h: WALL_T },
  { x: 190, y: 80, w: 282, h: WALL_T },
  // The partition wall between the open plan area and the two rooms on the east side. Its
  // doorway is the gap between y 150 and 190.
  { x: 356, y: 86, w: WALL_T, h: 64 },
  { x: 356, y: 190, w: WALL_T, h: 72 },
  // Meeting room above, archive below; one gets into the archive through the meeting room.
  // That is not elegant and it is what a floor plan grown over time looks like.
  { x: 362, y: 180, w: 78, h: WALL_T },
];

/** Cubicle screens. Low partitions **between the two columns** of a cluster, which is what
 *  turns four desks into four workplaces.
 *
 *  Only vertical ones, and that is a constraint of the room, not a taste: a horizontal screen
 *  would sit between the seats of the back row and the desks of the front row, so exactly in
 *  the way somebody walking to the back row has to come from. The route finding would then have
 *  to go round the whole cluster, and with the aisles this narrow it would not get through at
 *  all. The check `routes avoid the furniture` says so within a second.
 *
 *  They are obstacles like the walls: whoever draws a screen and does not block it gets a
 *  colleague walking through it, which is the complaint this whole feature started with. */
export const PARTITIONS: readonly Wall[] = [
  { x: 88, y: 88, w: 5, h: 74 },
  { x: 248, y: 88, w: 5, h: 74 },
  { x: 168, y: 170, w: 5, h: 74 },
];
