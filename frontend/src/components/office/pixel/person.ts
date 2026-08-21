// Layer 1, the people.
//
// Scale (rule 1 of the pixel contract): a character occupies **16×24 art units**, which is
// 32×48 buffer pixels, and it is drawn natively in the fine grid. `POS_SCALE` appears here
// exactly once, to bring in the scene coordinate of an actor. For no dimension at all.
//
// The character is assembled from 20 parts instead of drawn as finished poses:
//
//   head   3 × 20×18   front · side · back
//   hair   5 × 24×24   overlay, the largest single area of the figure
//   torso  3 × 18×12   short and narrower than the head on purpose, see there
//   arms   4 ×  4×10   overlay: resting · typing A · typing B · reaching or carrying
//   legs   4 × 14× 7   sitting · standing · stride A · stride B
//
// 20 parts instead of finished sprites: eight poses times twelve people would be 96 images and
// four times the entire art budget. Assembled, the same variety costs 20 parts, and every new
// pose afterwards costs one part, not twelve images.
//
// The build from the ground up (the order is the occlusion):
//
//   yBase-13 … yBase-1    legs    (20 wide, centred)
//   yBase-25 … yBase-12   torso   (14 wide, two rows over the hips)
//   yBase-37 … yBase-24   arms    (5 each, left and right of the trunk)
//   yBase-43 … yBase-24   head    (22 wide, no neck: the jaw sits on the shoulders)
//   yBase-43 … yBase-18   hair    (26 wide, over head **and** shoulders)
//
// That makes 43 of the 48 rows available. The head takes 20 of them, deliberately far too many
// for an adult: at this size it is the only part with enough area to carry a face, and a face
// is what the eye looks for first.

import type { ActorState, Ctx, Gait, Look, Pose } from "../types.ts";
import { GATE_PULSE_MS, POS_SCALE } from "../const.ts";
import { mix } from "../ids.ts";
import type { Art } from "./art.ts";
import { defineArt, drawArt, fill, fillA } from "./art.ts";
import type { Pal } from "./palette.ts";
import { gaitOf, lookOf, rolesSeed } from "./palette.ts";

// ═══ Dimensions ══════════════════════════════════════════════════════════════

/** Nominal width and height of a character in buffer pixels. The art stays just below that
 *  (14 instead of 16 wide); the 16 are the grid the scene computes in: hit testing,
 *  Blasenbreite, Mindestabstand zweier Figuren. */
export const FIG_W = 16;
export const FIG_H = 24;

/** The characters are the first **finely drawn** family (stage 2): they compute in HD units,
 *  where one unit is one buffer pixel at full view, half the size of an art unit. `scene.ts`
 *  gives them `viewHiOf` and the doubled coordinates for it.
 *
 *  `FIG_W`/`FIG_H` stay in ART units: hit testing, bubble width and the minimum distance
 *  between two characters hang on them, all matters of the scene, not of the sprite. Whoever
 *  doubles those two shifts every speech bubble and every click. */
const HD = 2;

/** How far the upper body sinks when sitting, in **art units** (the stance computes in those,
 *  `drawBody` converts). Three are few and enough: together with the sitting legs (thighs
 *  forward) the character reads as seated at once, and more would make the head disappear
 *  behind the desktop. */
const SIT_DROP = 3;

// ═══ The art ═════════════════════════════════════════════════════════════════

// ── The build ────────────────────────────────────────────────────────────────
//
// The figure is **measured off the reference sprite**, not designed. A WOKA is 32×32 with the
// character 23 wide and 31 high in it, and the row widths of its silhouette are the table
// below. Guessing them is what went wrong three times in a row: every attempt looked like a
// pixel character and none looked like *that* pixel character.
//
// What the measurement says, and what none of the guesses had:
//
//   · **Head and body are one continuous shape.** No neck, no gap, no separate oval sitting on
//     a box. The silhouette narrows at the jaw (17 of 23) and widens again at the shoulders.
//   · **The head is 17 of 31 rows**, so well over half.
//   · **The hair is a helmet with a face window.** It covers the whole skull and hangs down the
//     sides to the shoulders; what is left of the face is a hole 12 wide.
//   · **The eyes are 4×4 each** and fill most of that hole. Two dots in a wide face, which is
//     what the earlier attempts drew, is a different character entirely.
//   · **The arms are stubs** that show only over the lower half of the body, and the legs are
//     two nubs three rows high.
//
// Everything is scaled by `SCALE`: the reference is drawn for a 32 pixel tile seen from close
// up, ours is seen from further away and needs the extra size to stay readable.

/** Row widths of the reference silhouette, from top. Odd numbers throughout, so every row is
 *  centred on the same column. */
const REF_HEAD: readonly number[] = [5, 11, 15, 17, 19, 21, 21, 23, 23, 23, 23, 23, 23, 23, 21, 21, 19];
/** The torso **without** the arms; the arms are drawn separately so they can move. */
const REF_TORSO: readonly number[] = [15, 15, 15, 15, 15, 15, 15, 13, 13, 13, 11];
const REF_LEGS: readonly number[] = [11, 11, 9];

/** How much bigger than the reference. **One**, so exactly the size of a WOKA: 23 by 31 buffer
 *  pixels, and a carpet tile is 32. A figure is therefore one tile, which is the proportion the
 *  reference is built on and the reason it reads as a person rather than as a mascot.
 *
 *  It stood at 4/3 for a while, on the argument that this room is seen from further away and
 *  the figure needs the extra size. That argument is wrong: bigger does not make it more
 *  readable, it makes it a different character. Whoever wants it larger zooms (the stage has
 *  1x / 2x / 4x), and then everything grows together instead of the people alone. */
const SCALE = 1;

/** Total width of a person art. All parts share it, so they stack without any offset
 *  arithmetic at the call site. */
const FIG_ART_W = 23;

function up(n: number): number {
  return Math.max(1, Math.round(n * SCALE) | 1);
}

/** Stretches a list of row widths by `SCALE`, in rows as well as in width.
 *
 *  Interpolated, not repeated: taking the nearest reference row makes the dome of the head a
 *  staircase, because the widths jump by six between the first rows and the step is a third
 *  bigger after scaling. At 1x the reference reads as a curve; blown up it has to be one. */
function stretch(ws: readonly number[]): number[] {
  const rows = Math.round(ws.length * SCALE);
  const out: number[] = [];
  for (let i = 0; i < rows; i++) {
    const f = (i / (rows - 1)) * (ws.length - 1);
    const a = Math.floor(f), c = Math.min(ws.length - 1, a + 1);
    out.push(up(ws[a] + (ws[c] - ws[a]) * (f - a)));
  }
  return out;
}

/** One centred run inside a row `FIG_ART_W` wide. */
function band(n: number, ch: string): string {
  const pad = (FIG_ART_W - n) >> 1;
  return ".".repeat(pad) + ch.repeat(n) + ".".repeat(FIG_ART_W - n - pad);
}

/** A silhouette out of row widths, with the outermost pixel of every row in the shadow tone.
 *  That one row is what replaces a contour: it holds the edge without cutting a black line
 *  into a face 12 pixels wide (rule 2.5). */
function body(ws: readonly number[], fill: string, edge: string): string[] {
  return ws.map((n) => {
    const row = band(n, fill).split("");
    const pad = (FIG_ART_W - n) >> 1;
    row[pad] = edge;
    row[pad + n - 1] = edge;
    return row.join("");
  });
}

/** Overwrites a run in a row. The arts are computed, so patching them is a string operation
 *  and not a hand counted redraw. */
function put(rows: string[], y: number, x: number, s: string): void {
  if (y < 0 || y >= rows.length) return;
  rows[y] = rows[y].slice(0, x) + s + rows[y].slice(x + s.length);
}

const SKIN_MAP = { S: "S", s: "s", i: "ink", w: "paper" } as const;

const HEAD_W = stretch(REF_HEAD);
const HEAD_H = HEAD_W.length;
/** Centre column of every part. */
const MID = FIG_ART_W >> 1;

/** The eyes, measured: 4×4 each with a gap of 5 between them, in the lower third of the head.
 *  Scaled they are 5×5 with a gap of 7. */
// The eyes keep their measured size exactly; they are placed at an explicit x, so unlike the
// row widths they need no odd number to stay centred.
const EYE_W = Math.max(2, Math.round(4 * SCALE));
const EYE_H = Math.max(2, Math.round(4 * SCALE));
const EYE_GAP = Math.max(2, Math.round(5 * SCALE));
const EYE_Y = Math.round(13 * SCALE);

function headArt(dir: number): Art {
  const rows = body(HEAD_W, "S", "s");
  if (dir !== 2) {
    const halfGap = EYE_GAP >> 1;
    const leftX = MID - halfGap - EYE_W;
    const rightX = MID + halfGap + 1;
    // In profile both eyes move towards the front edge and the far one falls away.
    // In profile there is **one** eye, and it sits where a profile has one: a little in front
    // of the middle, not at the temple. Two eyes shifted sideways read as a squint.
    const lx = leftX;
    const rx = dir === 1 ? MID + 1 : rightX;
    for (let k = 0; k < EYE_H; k++) {
      // A row of white at the top, pupil below: the bright row is what turns a dark block into
      // a look. Without it the face has two holes in it.
      const ch = k === 0 ? "w" : "i";
      if (dir !== 1) put(rows, EYE_Y + k, lx, ch.repeat(EYE_W));
      put(rows, EYE_Y + k, rx, ch.repeat(EYE_W));
    }
  }
  return defineArt(rows, SKIN_MAP);
}

const HEADS: readonly Art[] = [headArt(0), headArt(1), headArt(2)];

/** Head direction. Numbers instead of strings, because they index into `HEADS` directly. */
const DIR_FRONT = 0;
const DIR_SIDE = 1;
const DIR_BACK = 2;

// ── Hair ─────────────────────────────────────────────────────────────────────
//
// A helmet with a face window, exactly as measured: full cover down to about half the head,
// then a hole opens and only the side strands are left, and those run past the jaw onto the
// shoulders. The window is 12 reference units wide; everything outside it is hair.

const HAIR_MAP = { H: "H", h: "h" } as const;

/** How far down the hair reaches at all: past the head onto the shoulders. */
const HAIR_H = Math.round(20 * SCALE);
/** From which row the face window is open. */
const FACE_TOP = Math.round(9 * SCALE);
/** Width of the face window. */
const FACE_W = up(12);

/** Builds a hairstyle. `sideTo` is the row down to which the side strands hang, `bump` adds a
 *  ponytail on the right, `frayed` breaks the top edge for curls. */
function hairArt(sideTo: number, bump: boolean, frayed: boolean): Art {
  const rows: string[] = [];
  for (let y = 0; y < HAIR_H; y++) {
    const w = y < HEAD_H ? HEAD_W[y] : HEAD_W[HEAD_H - 1] - (y - HEAD_H + 1) * 2;
    if (w <= 0 || y > sideTo) { rows.push(".".repeat(FIG_ART_W)); continue; }
    const r = band(Math.max(1, w), "H").split("");
    const pad = (FIG_ART_W - Math.max(1, w)) >> 1;
    if (y >= FACE_TOP) {
      // The window: everything between the strands becomes free again.
      const from = MID - (FACE_W >> 1);
      for (let x = from; x < from + FACE_W; x++) if (x >= 0 && x < FIG_ART_W) r[x] = ".";
    }
    if (y === FACE_TOP - 1 || y === sideTo) {
      // One row of shadow at the fringe and at the tip: that single row turns an area into a
      // strand of hair.
      for (let x = 0; x < FIG_ART_W; x++) if (r[x] === "H") r[x] = "h";
    }
    if (frayed && y > 0 && y < FACE_TOP - 1 && (y & 1) === 0) {
      r[pad] = "."; r[pad + Math.max(1, w) - 1] = ".";
    }
    rows.push(r.join(""));
  }
  if (bump) {
    for (let y = FACE_TOP; y < Math.min(HAIR_H, sideTo + 4); y++) {
      put(rows, y, FIG_ART_W - 3, "HH");
    }
  }
  return defineArt(rows, HAIR_MAP);
}

const HAIRS: readonly Art[] = [
  hairArt(Math.round(15 * SCALE), false, false),   // short
  hairArt(Math.round(17 * SCALE), false, false),   // longer at the sides
  hairArt(HAIR_H - 1, false, false),               // long, down to the shoulders
  hairArt(Math.round(16 * SCALE), true, false),    // ponytail
  hairArt(Math.round(16 * SCALE), false, true),    // curls
];

// ── Upper body ───────────────────────────────────────────────────────────────
// The torso without arms. It begins where the head ends, and the two shapes are built from the
// same table, so the silhouette runs through without a step.

const TORSO_MAP = { T: "T", t: "t", P: "P", S: "S", s: "s" } as const;

const TORSO_W = stretch(REF_TORSO);

function torsoArt(kind: number): Art {
  const rows = body(TORSO_W, "T", "t");
  const last = rows.length - 1;
  // The lowest two rows are trousers: without them a gap gapes between the hem and the leg.
  for (let k = 0; k < 2; k++) {
    rows[last - k] = rows[last - k].replace(/[Tt]/g, "P");
  }
  if (kind === 1) {
    // Shirt: a collar of skin at the neckline.
    put(rows, 0, MID - 2, "SSSS");
    put(rows, 1, MID - 1, "ss");
  } else if (kind === 2) {
    // Hoodie: a hood edge at the neck and a pocket.
    put(rows, 1, MID - 5, "tttttttttt");
    const py = rows.length - 6;
    put(rows, py, MID - 4, "tttttttt");
    put(rows, py + 2, MID - 4, "tttttttt");
  }
  return defineArt(rows, TORSO_MAP);
}

const TORSOS: readonly Art[] = [torsoArt(0), torsoArt(1), torsoArt(2)];

// ── Arms ─────────────────────────────────────────────────────────────────────
// Stubs. In the reference an arm shows only over the lower half of the body and ends in a
// hand; there is no upper arm to see, because the sleeve and the trunk are the same colour and
// the same silhouette.

const ARM_MAP = { T: "T", t: "t", S: "S", s: "s" } as const;

const ARM_W = up(5);
const ARM_ROWS = Math.round(8 * SCALE);

/** `reach` shifts the hand forward by that many rows: 0 hanging, 2 on the keyboard, 4 held out. */
function armArt(reach: number): Art {
  const rows: string[] = [];
  for (let y = 0; y < ARM_ROWS; y++) {
    const skin = y >= ARM_ROWS - 3 - reach && y < ARM_ROWS - reach;
    const gone = y >= ARM_ROWS - reach;
    if (gone) { rows.push(".".repeat(ARM_W)); continue; }
    const ch = skin ? "S" : "T";
    const edge = skin ? "s" : "t";
    rows.push(edge + ch.repeat(ARM_W - 2) + edge);
  }
  return defineArt(rows, ARM_MAP);
}

const ARMS: readonly Art[] = [armArt(0), armArt(2), armArt(3), armArt(4)];

const ARM_REMAINDER_I = 0;
const ARM_TYPE_A_I = 1;
const ARM_TYPE_B_I = 2;
const ARM_REACH_I = 3;

/** The short sleeve: from this row the arm is drawn a second time in skin (`tint`). */
const ARM_CUFF: readonly number[] = ARMS.map(() => Math.round(4 * SCALE));

const ARM_FORE: readonly Art[] = ARMS.map((a, i) => ({
  rows: a.rows.slice(ARM_CUFF[i]), map: a.map,
}));

// ── Legs ─────────────────────────────────────────────────────────────────────
// Two nubs. In the reference the legs are three rows of the thirty-one and carry no
// information at all; they are what the eye reads last.

const LEG_MAP = { P: "P", i: "ink" } as const;

const LEG_ROWS = Math.round(REF_LEGS.length * SCALE);
const LEG_SPAN = up(11);

/** `spread` pushes the two feet apart: the stride. */
function legsArt(spread: number, sit: boolean): Art {
  const rows: string[] = [];
  const foot = up(5);
  for (let y = 0; y < LEG_ROWS; y++) {
    const r = ".".repeat(FIG_ART_W).split("");
    if (sit) {
      // Sitting: the thighs go forward, and under a desk almost nothing of them is visible.
      for (let x = MID - 2; x <= MID + (LEG_SPAN >> 1); x++) r[x] = y < LEG_ROWS - 2 ? "P" : "i";
    } else {
      const gap = 1 + spread;
      const left = MID - (gap >> 1) - foot;
      const right = MID + (gap >> 1) + (gap & 1);
      for (let x = left; x < left + foot; x++) if (x >= 0) r[x] = y >= LEG_ROWS - 2 ? "i" : "P";
      for (let x = right; x < right + foot; x++) if (x < FIG_ART_W) r[x] = y >= LEG_ROWS - 2 ? "i" : "P";
    }
    rows.push(r.join(""));
  }
  return defineArt(rows, LEG_MAP);
}

const LEGS: readonly Art[] = [
  legsArt(0, true), legsArt(2, false), legsArt(8, false), legsArt(5, false),
];

const LEGS_SIT_I = 0;
const LEGS_STATE_I = 1;
const LEGS_WALK_A_I = 2;
const LEGS_WALK_B_I = 3;

// ═══ Zeit → Bild ═════════════════════════════════════════════════════════════
//
// **All** phases come from `t` (`(t / MS | 0) % n`), not one of them from a counter. That is
// not taste but the dt split invariance (rule 3.4): live the ticks come at the rAF beat, while
// rewinding in 250 ms steps. A counter would count differently and the timeline would show a
// different room than the stage.
//
// The numbers stand here and not in `const.ts`: they describe how a sprite looks, not how the
// room behaves. Whoever changes the stride length changes art, not simulation.

/** Frame duration of the walk cycle at normal speed. 4 × 120 ms is just under half a second per
 *  double step, which fits `SPEED_PX_PER_S = 150` (about 45 buffer pixels per second). */
const WALK_FRAME_MS = 120;
/** Typing: two frames. Faster looks frantic, slower like hunt and peck. */
const TYPE_FRAME_MS = 160;
/** Gesturing while speaking. */
const TALK_FRAME_MS = 240;
/** Breathing, the only micro idle v1 kept. Without it a room full of waiting agents looks like
 *  a still image, and one starts looking for the bug in the engine. */
const BREATH_MS = 900;

const SALT_BREATH = 0x41544d4e; // "ATMN"

/** The breathing curve as a table instead of a sine: at ±1 pixel there are only two values
 *  anyway, and a table is bit identical across all browsers. */
const BREATH: readonly number[] = [0, -1, -1, 0];

/** Integer phase index from the simulation time. */
function phase(t: number, ms: number, n: number, offset: number): number {
  const raw = ((t / ms) | 0) + offset;
  // `%` yields negative values for a negative `t`. The engine starts at 0, but a caller with an
  // offset backwards would otherwise index out of the array.
  return ((raw % n) + n) % n;
}

// ═══ Aktion ══════════════════════════════════════════════════════════════════

export type CharAct = "idle" | "type" | "read" | "walk" | "wait" | "talk" | "handoff" | "gaze";

/**
 * What the character is doing right now, as a **table** instead of a cascade of special cases.
 *
 * The order is the statement: walking beats everything (whoever walks does not type), then
 * sitting separates from standing, and inside both the more specific state wins. Whoever moves
 * a condition up here does not change the look but what the room claims.
 *
 * Deviation from the design: there the waiting case read `waiting > 0 && waiting === busy`.
 * `ActorState.waiting` is a `boolean` in the finished contract (the engine sets it on `gate`
 * and clears it on `resume`), so there is neither a counter nor a moment to compare. The
 * condition is therefore simply `a.waiting`.
 */
export function actOf(a: ActorState): CharAct {
  if (a.pose === "walk") return "walk";

  if (a.pose === "sit") {
    if (a.done !== undefined) return "idle";
    if (a.waiting) return "wait";
    if (a.act === "read" || a.act === "browse") return "read";
    if (a.act === "write" || a.act === "run" || a.act === "other") return "type";
    if (a.busy > 0) return "type";
    return "idle";
  }

  if (a.say !== undefined) return "talk";
  if (a.act === "delegate") return "handoff";
  if (a.act === "browse") return "gaze";
  return "idle";
}

// ═══ Haltung ═════════════════════════════════════════════════════════════════

/** A finished, computed pose. Pure numbers: `drawBody` only paints them. */
interface Stance {
  /** Index in `HEADS`. */
  dir: number;
  legs: number;
  /** Arm on the viewer's side and on the far side. Two values, because while typing the hands
   *  strike offset from one another: with one value both hands type in sync, and that looks
   *  like playing the piano with tied wrists. */
  armNear: number;
  armFar: number;
  /** Offset of the whole body downwards (sitting). */
  drop: number;
  /** Offset of the whole body upwards (breathing, the bob of walking). */
  lift: number;
  /** Horizontal offset of the upper body in the facing direction (lean while walking). */
  leanX: number;
  /** Horizontal offset of the arms (swing while walking). */
  armX: number;
  /** Extra shoe length of the leading foot (stride from the seed). */
  shoe: number;
  /** Paper in front of the chest (reading). */
  paper: boolean;
}

/**
 * Builds the pose from action, time and gait.
 *
 * **All seven** fields of `gaitOf` are used here. That is not completeness for its own sake:
 * with `speed/bob/phase` alone twelve people walk in the same rhythm and differ only in tempo,
 * which from two metres away is one single animation. They become distinguishable only through
 * stride, lean and arm swing, so through the silhouette in motion.
 */
function stanceOf(
  act: CharAct, pose: Pose, t: number, look: Look, gait: Gait, seed: number,
): Stance {
  const sitting = pose === "sit";
  const breath = BREATH[phase(t, BREATH_MS / BREATH.length, BREATH.length,
    mix(seed, SALT_BREATH) % BREATH.length)];

  const s: Stance = {
    dir: sitting ? DIR_SIDE : DIR_FRONT,
    legs: sitting ? LEGS_SIT_I : LEGS_STATE_I,
    armNear: ARM_REMAINDER_I,
    armFar: ARM_REMAINDER_I,
    drop: sitting ? SIT_DROP : 0,
    lift: breath,
    leanX: 0,
    armX: 0,
    shoe: 0,
    paper: false,
  };

  if (act === "walk") {
    // Tempo from the seed **lengthens the frame duration** instead of skipping frames: a slow
    // walker should not judder but walk slowly.
    const ms = Math.max(60, Math.round(WALK_FRAME_MS / gait.speed));
    const f = phase(t, ms, 4, Math.round(gait.phase * 4));
    // Leading foot from the look, otherwise all twelve start with the same leg.
    const lead = (look.legs & 1) === 0;
    const strideFrames: readonly number[] = lead
      ? [LEGS_STATE_I, LEGS_WALK_A_I, LEGS_STATE_I, LEGS_WALK_B_I]
      : [LEGS_STATE_I, LEGS_WALK_B_I, LEGS_STATE_I, LEGS_WALK_A_I];
    s.dir = DIR_SIDE;
    s.legs = strideFrames[f];
    // Bob: high in the passing position, low in the full stride. `bob` is 0.35..1.
    s.lift = f % 2 === 0 ? -Math.max(1, Math.round(gait.bob * 2)) : 0;
    s.leanX = Math.round(gait.lean * 2);
    s.shoe = f % 2 === 1 ? gait.stride - 2 : 0;
    const af = phase(t, ms, 4, Math.round(gait.armPhase * 4));
    s.armX = af === 1 ? gait.swing : af === 3 ? -gait.swing : 0;
    s.armNear = ARM_REMAINDER_I;
    s.armFar = ARM_REMAINDER_I;
    return s;
  }

  if (act === "type") {
    const f = phase(t, TYPE_FRAME_MS, 2, 0);
    s.armNear = f === 0 ? ARM_TYPE_A_I : ARM_TYPE_B_I;
    s.armFar = f === 0 ? ARM_TYPE_B_I : ARM_TYPE_A_I;
    return s;
  }

  if (act === "read") {
    // In profile, because a sheet in front of the face looks like a bib in the front view.
    s.dir = DIR_SIDE;
    s.armNear = ARM_REACH_I;
    s.armFar = ARM_REACH_I;
    s.paper = true;
    return s;
  }

  if (act === "wait") {
    // Weight shift at the beat of the gate pulse. The same rhythm as the waiting signal above
    // the head: two signals coming from the same constant read as one state.
    s.leanX = phase(t, GATE_PULSE_MS, 2, 0) === 0 ? 0 : 1;
    return s;
  }

  if (act === "talk") {
    const f = phase(t, TALK_FRAME_MS, 2, 0);
    s.armNear = f === 0 ? ARM_REACH_I : ARM_REMAINDER_I;
    return s;
  }

  if (act === "handoff") {
    s.dir = DIR_SIDE;
    s.armNear = ARM_REACH_I;
    s.armFar = ARM_REACH_I;
    s.leanX = 1;
    return s;
  }

  if (act === "gaze") {
    s.dir = DIR_SIDE;
    // One pixel of nodding every 800 ms: the difference between "looking" and "frozen".
    s.lift = breath + (phase(t, 800, 2, 0) === 0 ? 0 : 1);
    return s;
  }

  // idle: standing, the weight shifts onto one leg depending on the seed.
  if (!sitting && (look.legs & 2) !== 0) s.leanX = 1;
  return s;
}

// ═══ Zeichnen ════════════════════════════════════════════════════════════════

/**
 * The contact shadow. Without it every character floats a hair above the planks. The effect is
 * tiny but noticed at once, because the eye checks the grip on the ground at exactly that edge.
 *
 * Three flat rectangles instead of a soft patch: `shadowBlur` is forbidden (rule 2.1) and would
 * be mush at 480×270 anyway.
 */
export function drawShadow(ctx: Ctx, cx: number, yBase: number, pal: Pal, w: number): void {
  // Five steps instead of three: in the fine grid a step is half as high, and three of them
  // would be a line. The widths taper from the inside out, a stepped patch, the closest thing
  // to a soft shadow that `fillRect` alone can give (rule 2.1).
  const half = w >> 1;
  fillA(ctx, pal, "shadow", 0.26, cx - half + 3, yBase - 2, w - 6, 1);
  fillA(ctx, pal, "shadow", 0.20, cx - half + 1, yBase - 1, w - 2, 1);
  fillA(ctx, pal, "shadow", 0.14, cx - half, yBase, w, 1);
  fillA(ctx, pal, "shadow", 0.09, cx - half + 1, yBase + 1, w - 2, 1);
  fillA(ctx, pal, "shadow", 0.05, cx - half + 3, yBase + 2, w - 6, 1);
}

/** Facial detail from `look.head`.
 *
 *  Why not three head arts per direction: the three heads are **facing directions**, not
 *  people, and `look.head` would otherwise have no effect at all and be a dead field in the
 *  seed. Mouth and beard cost two `fillRect` and give every second character its own face. */
function face(ctx: Ctx, pal: Pal, cx: number, headTop: number, variant: number, dir: number): void {
  if (dir === DIR_BACK) return;
  // The head is 22 wide, so its left edge lies 11 to the left of the centre. Everything below
  // is counted from there in buffer pixels, the same grid the head art is drawn in.
  const hx = cx - MID;
  if (variant === 1) {
    // Glasses: one bar over each eye, one row above it. The face is 13 pixels wide between the
    // strands of hair; anything drawn twice across it is a blindfold, and a frame around an eye
    // 4 pixels wide is a frame around nothing.
    fill(ctx, pal, "lineSoft", hx + MID - 7, headTop + EYE_Y - 1, EYE_W + 2, 1);
    fill(ctx, pal, "lineSoft", hx + MID + 1, headTop + EYE_Y - 1, EYE_W + 2, 1);
  } else if (variant === 2) {
    // A hair band. There is no room for a beard: the face window is four rows tall and the
    // eyes fill it, so anything below them lands on the shoulders. What is left is the hair,
    // and a band across it is visible from three metres and belongs to the person.
    fill(ctx, pal, "h", hx + 2, headTop + 6, FIG_ART_W - 4, 2);
  }
}

/** One arm including sleeve length. `sleeve` 0/1 is long, 2 short, 3 rolled up. */
function arm(
  ctx: Ctx, pal: Pal, idx: number, cx: number, yBase: number,
  flip: boolean, sleeve: number, alpha: number,
): void {
  drawArt(ctx, ARMS[idx], cx, yBase, pal, { flip, alpha });
  if (sleeve >= 2) {
    const fore = ARM_FORE[idx];
    // Rolled up leaves one more row of fabric standing, so the forearm art is shortened by one
    // more row.
    const rows = sleeve === 3 ? fore.rows.slice(1) : fore.rows;
    if (rows.length > 0) {
      drawArt(ctx, { rows, map: fore.map }, cx, yBase, pal, { flip, alpha, tint: "S" });
    }
  }
}

/** Assembles the character from its parts. The order is the occlusion: legs, torso, the far
 *  arm, head, face, the near arm, hair. The far arm lies **behind** the torso, the near one in
 *  front of it, and that is the only hint of depth a character 14 pixels wide can give. */
function drawBody(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, look: Look, s: Stance,
  flip: boolean, alpha: number,
): void {
  // `cx`/`yBase` come in in HD units; `Stance` keeps computing in art units (it describes a
  // pose, not pixels). The conversion therefore happens exactly here, at the seam between the
  // two, and not scattered through `stanceOf`.
  //
  // The stack from the ground up, all of it in buffer pixels:
  //
  //   legs    7 rows, foot at `yBase`
  //   torso  12 rows, foot at `yBase-5`   (two rows of it are the hips)
  //   head   18 rows, foot at `torsoY-11` (the jaw sits on the shoulders, there is no neck)
  //   hair   24 rows, foot 6 below the head, so it lies over head **and** shoulders
  //
  // Together that is 34 of the 48 rows a figure is allowed (`FIG_H = 24` art units). Squat on
  // purpose: the reference figure is about one tile tall, and a tile is 32 buffer pixels.
  const dirSign = flip ? -1 : 1;
  const bodyY = yBase + (s.drop + s.lift) * HD;
  const legsY = yBase;

  const torsoY = bodyY - LEG_ROWS + 1;
  const headY = torsoY - TORSO_W.length + 1;
  const armY = torsoY - 2;
  const hairY = headY + (HAIR_H - HEAD_H);

  const bodyX = cx + s.leanX * HD * dirSign;
  // The torso is 18 wide, an arm 4: at ±9 the arm sits on the edge of the trunk, where a
  // shoulder is.
  // The arm sits on the edge of the trunk: half the torso plus half an arm, less one pixel of
  // overlap so that the shoulder is a joint and not a butt seam.
  const armOff = ((TORSO_W[0] + ARM_W) >> 1) - 2;
  const armXNear = bodyX + (armOff + s.armX * HD) * dirSign;
  const armXFar = bodyX - (armOff - s.armX * HD) * dirSign;

  // Legs: the leading shoe is lengthened by `shoe` pixels, the stride from the seed, without
  // needing a second leg art for it.
  drawArt(ctx, LEGS[s.legs], cx, legsY, pal, { flip, alpha });
  if (s.shoe > 0) {
    // Walking left the shoe grows to the left: a width multiplied by `dirSign` would be
    // negative, and `fill` silently discards negative widths, so the stride of the half of the
    // room walking left would simply be shorter.
    const sx = flip ? cx - (5 + s.shoe) * HD : cx + 5 * HD;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "ink", sx, legsY - 3, s.shoe * HD, 2);
    if (alpha < 1) ctx.globalAlpha = 1;
  }

  drawArt(ctx, TORSOS[look.torso], bodyX, torsoY, pal, { flip, alpha });
  arm(ctx, pal, s.armFar, armXFar, armY, !flip, look.arms, alpha);
  drawArt(ctx, HEADS[s.dir], bodyX, headY, pal, { flip, alpha });
  if (alpha >= 1) face(ctx, pal, bodyX, headY - HEAD_H, look.head, s.dir);

  if (s.paper) {
    // The sheet in the hand. A single bright rectangle in front of the chest is enough:
    // otherwise "reads" cannot be told from "types", because both arms point forward. In front
    // of the body, not on it: on the chest it would read as a name badge.
    const px = bodyX + dirSign * 9 - 4;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "lineSoft", px - 1, torsoY - 11, 10, 10);
    fill(ctx, pal, "paper", px, torsoY - 10, 8, 8);
    fill(ctx, pal, "ink", px + 2, torsoY - 8, 4, 1);
    fill(ctx, pal, "ink", px + 2, torsoY - 6, 3, 1);
    if (alpha < 1) ctx.globalAlpha = 1;
  }

  arm(ctx, pal, s.armNear, armXNear, armY, flip, look.arms, alpha);
  drawArt(ctx, HAIRS[look.hair], bodyX, hairY, pal, { flip, alpha });
}

/**
 * Draws an actor at its scene position.
 *
 * The only place in this file where `POS_SCALE` appears, and it scales a **position**, not a
 * size (rule 1). Rounding happens here, not in the engine: that one keeps computing with a
 * subpixel accumulator so that even tiny `dt` make progress.
 *
 * `pal` is the **already resolved palette of this character**
 * (`palFor(grade, lookOf(a.seed, rollenSeed(a.role, a.seed)))`). Resolving it here would be an
 * object spread over 36 keys per character and frame.
 *
 * The look is resolved **while drawing**, not when the actor is created. That is why the case
 * of a role arriving late needs no special treatment: `engine.ts` sets `a.role` only after
 * `ensureActor`, and `wake(id)` calls `ensureActor` without a role at all. If the look had been
 * written into a seed once, somebody would have to overwrite it afterwards; this way the
 * character simply gets its role look in the first frame in which the role is known.
 */
export function drawActor(ctx: Ctx, a: ActorState, t: number, pal: Pal): void {
  // Rounded in HD: the character can stand on half art units, and walking runs twice as
  // finely, the same movement with steps half as coarse.
  const cx = Math.round(a.x * POS_SCALE * HD);
  const yBase = Math.round(a.y * POS_SCALE * HD);
  const look = lookOf(a.seed, rolesSeed(a.role, a.seed));
  // The gait stays **entirely** with the walk seed: it is what keeps twelve colleagues of one
  // role apart in the picture when shirt and hair are grouping them together (rule 3.2).
  const gait = gaitOf(a.seed);
  const act = actOf(a);
  const s = stanceOf(act, a.pose, t, look, gait, a.seed);

  // The boss seat is the only seat whose character sits **in front of** its desk (`room.ts`:
  // `sit = desk + SEAT_DY`, same x centre). So it faces away from us, and that is not a
  // stylistic decision but the geometry of the room.
  if (a.pose === "sit" && a.deskIndex === -1) s.dir = DIR_BACK;

  drawShadow(ctx, cx, yBase, pal, (a.pose === "sit" ? 9 : 10) * HD);
  drawBody(ctx, cx, yBase, pal, look, s, a.flip, 1);
}

/**
 * An agent without a desk (`deskIndex === -2`).
 *
 * Semi transparent and without a contact shadow: it belongs to the run but has no place in the
 * room. Drawing it like everybody else would be a lie (it has no chair), leaving it out a
 * second one (it is working). The ghost is the only honest representation, and one sees at
 * once that the room is full.
 *
 * `look` comes in **ready made** instead of being pulled from `seed`: it is the same one the
 * caller already built `pal` from. Pulling it again here would mean resolving the role a second
 * time, and at the slightest drift the ghost would wear a different shirt than its palette.
 * `seed` is still needed: gait and breathing phase are individual.
 */
export function drawGhost(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, t: number, seed: number, look: Look,
): void {
  const gait = gaitOf(seed);
  const s = stanceOf("idle", "stand", t, look, gait, seed);
  // Extra floating so the ghost reads as a ghost in a still image as well.
  s.lift += phase(t, 700, 2, mix(seed, SALT_BREATH) % 2) === 0 ? 0 : -1;
  drawBody(ctx, cx, yBase, pal, look, s, false, 0.45);
}

/** Only for the hit test of the stage: the area a character occupies. No drawing, so that
 *  `scene.ts` does not have to copy the dimensions out of the header comment. */
export function actorBox(cx: number, yBase: number): {
  x: number; y: number; w: number; h: number;
} {
  return { x: cx - (FIG_W >> 1), y: yBase - FIG_H, w: FIG_W, h: FIG_H + 2 };
}
