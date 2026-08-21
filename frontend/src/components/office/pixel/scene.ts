// Layer 1, the only entry point of the drawing layer.
//
// `renderFrame(ctx, frame, cam, grade)` paints a complete image buffer. It gets a `Frame` and
// nothing else: no engine, no recorder, no clock. That is the seam rewinding hangs on: if the
// drawing layer saw the engine it could carry state forward while painting, and the image would
// depend on how often it was drawn.
//
// ── Why the room geometry stands here a second time ─────────────────────────
//
// `room.ts` is layer 0, and layer 1 may only see `types`, `ids` and `const` from layer 0 (rule
// 4, and the checker enforces it). The furniture positions below are therefore computed from
// the **same fractions** of `SCENE` as there, not copied. So the two cannot drift apart without
// anybody noticing, this file exports `SEATS_PX`: a check can hold `SEATS_PX[i]` against
// `round(ROOM.seats[i].sit × POS_SCALE)`, which turns the duplication from a silent danger into
// a checked promise.
//
// ── The order of layers ─────────────────────────────────────────────────────
//
//   1 shell          wall · windows · board · clock · door · floor · cabinet
//   2 Licht          Fensterlicht (Tag) bzw. Lampenkegel (Abend)
//   3 Teppich
//   4 floor layer    furniture, workplaces **and characters together**, sorted by `yBase`
//   5 Luft           Dampf, Staub, Funken, Zettel
//   6 Spawn-Linien
//   7 overlays       plates, bubbles, emotes
//   8 grading        happens in the palette, not as a filter
//
// Layer 4 is the one that must not be rearranged: furniture and characters lie in **one** list
// and are sorted together (painter's algorithm). Two separate passes would be easier to write
// and wrong at once: a character in front of the desk of the front row would disappear behind
// it.

import type {
  ActorState, Ctx, Frame, Fx, Grade, MoodKind, Pt, ScreenKind, ToolAct,
} from "../types.ts";
import { ART, ART_SCALE, LINK_MS, PIX, POS_SCALE, SCENE } from "../const.ts";
import { fillA } from "./art.ts";
import type { Pal } from "./palette.ts";
import { GRADES, lookOf, palFor, rolesSeed } from "./palette.ts";
import {
  COOLER, LOUNGE, SHELF, SIZE, WALL_H, WINDOW_STEP, drawBoard, drawCabinet, drawChair,
  drawClock, drawCoffee, drawCooler, drawDesk, drawDoor, drawFloor, drawLowTable, drawMonitor,
  drawPicture, drawPlant, drawMeetingTable, drawRack, drawRoomEdges, drawRug, drawShelf,
  drawPartitions, drawSofa, drawWindowFront, drawWalls, drawZone, drawLight, drawTableChair, drawWall, drawWindow, drawWindowLight,
} from "./furniture.ts";
import { FIG_H, FIG_W, actorBox, drawActor, drawGhost } from "./person.ts";
import {
  PLATE_H, dust, emotePop, footPuff, linkLine, nameplate, revealOf, spark, speechBubble, steam,
  thoughtBubble,
} from "./props.ts";

// ═══ Kamera ══════════════════════════════════════════════════════════════════

/** `x`/`y` are the **buffer point** standing in the centre of the image; `zoom` is rounded to a
 *  whole number. Non integer scaling turns pixel art into mush: an edge one pixel wide would be
 *  mapped onto 1.5 pixels and would run over two columns at half opacity. */
export type Cam = { x: number; y: number; zoom: number };

/** The camera that shows the whole room. */
export const CAM_FULL: Cam = { x: ART.w / 2, y: ART.h / 2, zoom: ART_SCALE };

interface CamFit { z: number; hz: number; ox: number; oy: number }

/**
 * Scale and offset of the camera.
 *
 * `z` is a **multiple of `ART_SCALE`**, not merely an integer. That is the condition for finely
 * drawn families to be possible at all: they compute in HD units (half art units), and
 * `hz = z / ART_SCALE` has to stay an integer. At an odd zoom an HD edge would run over half a
 * buffer column, exactly the shimmer that
 * Regel 2.3 verbietet.
 */
function camFit(cam: Cam): CamFit {
  const hz = Math.max(1, Math.round(cam.zoom / ART_SCALE));
  const z = hz * ART_SCALE;
  return {
    z, hz,
    ox: Math.round(PIX.w / 2 - cam.x * z),
    oy: Math.round(PIX.h / 2 - cam.y * z),
  };
}

/**
 * The camera as a `Ctx` wrapper.
 *
 * Panning and zooming would really be `translate`/`scale`, and both are forbidden (rule 2.1),
 * rightly so: a transformation matrix rasterises edges onto subpixels. Instead this wrapper
 * converts every rectangle itself. Because all inputs are integers and `z` is a whole number,
 * all outputs are integers as well, so pixels stay pixels.
 *
 * At identity the context is passed through **unchanged**: the most common case should not cost
 * a detour through a call, and the golden ops hashes of the checker stay those of the bare
 * Kontexts.
 */
function viewOf(base: Ctx, fit: CamFit): Ctx {
  return scaled(base, fit.z, fit.ox, fit.oy);
}

/**
 * The same camera, but in **HD units**: one unit is one buffer pixel at full view instead of
 * the two of an art unit. Finely drawn families (the characters since stage 2) get this context
 * and compute in a grid twice as fine; their coordinates are doubled accordingly.
 * are doubled accordingly.
 */
function viewHiOf(base: Ctx, fit: CamFit): Ctx {
  return scaled(base, fit.hz, fit.ox, fit.oy);
}

function scaled(base: Ctx, z: number, ox: number, oy: number): Ctx {
  if (z === 1 && ox === 0 && oy === 0) return base;
  return {
    get fillStyle(): string | object { return base.fillStyle; },
    set fillStyle(v: string | object) { base.fillStyle = v; },
    get globalAlpha(): number { return base.globalAlpha; },
    set globalAlpha(v: number) { base.globalAlpha = v; },
    fillRect(x: number, y: number, w: number, h: number): void {
      base.fillRect(x * z + ox, y * z + oy, w * z, h * z);
    },
  };
}

/** Opacity of a character that does not belong to the selected session tab. Strong enough to
 *  still recognise it as a character: the filter dims, it does not remove. */
const DIM_ALPHA = 0.35;

/**
 * A pale wrapper: every opacity set is multiplied by `k`.
 *
 * Why a wrapper and not a `globalAlpha` around the block: `fillA` and `drawArt` set
 * `globalAlpha` back to 1 **themselves** at the end (rule 2.1: there is no `restore`, resetting
 * is the duty of the drawer). An alpha set once would therefore only hold until the first call.
 * The wrapper intercepts exactly those assignments.
 *
 * The caller sets `wrapper.globalAlpha = 1` before the block (which puts the base at `k`) and
 * `basis.globalAlpha = 1` danach.
 */
function dimOf(base: Ctx, k: number): Ctx {
  return {
    get fillStyle(): string | object { return base.fillStyle; },
    set fillStyle(v: string | object) { base.fillStyle = v; },
    get globalAlpha(): number { return base.globalAlpha; },
    set globalAlpha(v: number) { base.globalAlpha = v * k; },
    fillRect(x: number, y: number, w: number, h: number): void { base.fillRect(x, y, w, h); },
  };
}

// ═══ The floor plan ══════════════════════════════════════════════════════════
//
// Scene dimensions as in `room.ts`, then rounded **once** into buffer pixels with `POS_SCALE`.
// From here on every number is a buffer number; `POS_SCALE` appears in this file otherwise only
// for actor and effect positions (rule 1).

/** Szenen- → Pufferkoordinate. */
function px(v: number): number {
  return Math.round(v * POS_SCALE);
}

/** One workplace in buffer pixels: where the person sits, where their desk stands, where the
 *  screen stands on it, and which way they face. */
interface SeatPx {
  sit: Pt;
  desk: Pt;
  mon: Pt;
  flip: boolean;
}

// The floor plan a second time, in buffer pixels. Layer 1 must not see `room.ts` (rule 4), so
// it inevitably holds the geometry twice; check 10 holds the two against each other.
const CLUSTERS: readonly Pt[] = [
  { x: 300, y: 467 }, { x: 833, y: 467 }, { x: 567, y: 740 },
];
const SEAT_DX = 63;
const SEAT_DY_ROW = 73;

/** How far the desk stands above its seat, in **buffer pixels**. */
const DESK_UP = 16;

/**
 * The thirteen seats in buffer pixels: three clusters of four plus the chief's desk.
 *
 * A desk stands **over** its seat and centred on it. In a room seen from above a person sits
 * in front of their own desk; the old plan (half a desk pushed sideways towards the middle of
 * a bench for two) came from the front view and put every colleague at the corner of a table
 * as soon as the projection was right.
 */
function buildSeats(): SeatPx[] {
  const out: SeatPx[] = [];
  for (const c of CLUSTERS) {
    for (const dy of [-SEAT_DY_ROW, SEAT_DY_ROW]) {
      for (const dx of [-SEAT_DX, SEAT_DX]) {
        const sit = { x: px(c.x + dx), y: px(c.y + dy) };
        out.push({
          sit,
          desk: { x: sit.x, y: sit.y - DESK_UP },
          mon: { x: sit.x, y: sit.y - DESK_UP },
          flip: dx > 0,
        });
      }
    }
  }
  const boss = { x: px(1067), y: px(433) };
  out.push({
    sit: boss,
    desk: { x: boss.x, y: boss.y - DESK_UP },
    mon: { x: boss.x, y: boss.y - DESK_UP },
    flip: true,
  });
  return out;
}

/** Public so that a check can hold the duplication against `room.ts`. */
export const SEATS_PX: readonly SeatPx[] = buildSeats();

const DOOR = { x: px(Math.round(SCENE.w * 0.73)), y: px(Math.round(SCENE.h * 0.135)) };
const TABLE = { x: px(1390), y: px(467) };
const COFFEE = { x: px(83), y: px(400) };

/** Bands of windows left and right of the centre of the wall. The numbers are buffer pixels and
 *  derived from no scene dimension: the wall is scenery, not a place where something happens. */
const WIN_Y = 32;
const WIN_LEFT_X = 20;
const WIN_LEFT_N = 5;
const WIN_RIGHT_X = 396;
const WIN_RIGHT_N = 3;

/** The centres of all windows, for the fields of light on the floor. Built from the same
 *  constants as the windows themselves; a second list would be the first place where windows
 *  and light drift apart. */
const WIN_XS: readonly number[] = [42, 96, 150, 396, 442];
const BOARD_X = 214;
const CLOCK_X = 320;

/** Filing cabinet with a potted plant, under the whiteboard. Pure furnishing. */
const CABINET_X = 196;
const CABINET_Y = 60;

/**
 * The server rack: 20×34, standing at the back wall **between whiteboard and clock**.
 *
 * The place is computed, not chosen. The board occupies `BOARD_X ± 17` (so up to 207), the
 * clock `CLOCK_X ± 4` (from 242), the left band of windows ends at 147 and the door begins at
 * 340. The rack 20 pixels wide fits with `RACK_X = 224` exactly into the gap (214..233), and
 * only there, without covering another piece of wall.
 *
 * `RACK_Y` stays the old floor line: the rack grew upwards, not forwards. Its top edge is
 * therefore at 26, so **above** the skirting board (`WALL_H - 4`) in the wall surface, exactly
 * how a rack two metres tall looks in front of a wall. It covers nothing there: between the
 * board (up to 207) and the clock (from 242) nothing hangs on the wall.
 */
const RACK_X = 224;
const RACK_Y = 60;

/**
 * The lounge, the shelves and the water cooler: scenery in the front strip of the room.
 *
 * All of it stands **below row 200**, so behind the last row of desks (their foot point is
 * 196) and in front of nothing. That is the one condition: a piece of furniture in the aisle
 * would stand in the way of every figure walking to its seat, and the engine knows nothing
 * about it, so the figure would walk straight through it.
 */
const LOUNGE_X = 322;
const LOUNGE_Y = 255;
const SHELF_X = 400;
const SHELF_Y = 243;
const COOLER_X = 300;
const COOLER_Y = 74;

/** Two pictures on the wall, left and right of the whiteboard. */
const PICTURE_LEFT_X = 194;
const PICTURE_RIGHT_X = 300;
const PICTURE_Y = 26;

/**
 * The standing place in front of the server rack, in buffer pixels.
 *
 * The same duplication as with `SEATS_PX` and for the same reason: layer 1 must not see
 * `room.ts` (rule 4) and therefore inevitably holds the geometry a second time. Check 11 holds
 * `RACK_PX ≡ round(ROOM.rack × POS_SCALE)`; if it drifts, the character walks to a place where
 * no rack stands, and the verdict (`emote`) floats beside it in the air.
 *
 * The 18 pixels of offset to the right are not taste: a character is 16 buffer pixels wide and
 * would stand centred **in front of** the rack instead of next to it. With the offset it starts
 * at 234, one pixel right of the rack edge (233): it stands at the rack, not in front of it.
 *
 * Right, not left: on the left are the filing cabinet and the potted plant (188..203). And
 * because the speech bubble is centred on the middle of the character, the LED field sits on
 * the **other** front side (`LED_X = 3`, here 217..220), otherwise the bubble would cover
 * exactly the display the character walked over for.
 */
export const RACK_PX: Pt = { x: RACK_X + 18, y: RACK_Y + 8 };

// ═══ Monitor image and mood ══════════════════════════════════════════════════
//
// `toolAct.ts` is layer 0 and not visible to layer 1 (rule 4). The two tables below are
// therefore copies of `screenFor`/`moodFor`, deliberately **word for word**, so that comparing
// the two places is one line in a diff and not a translation exercise.

const SCREEN_BY_ACT: Record<ToolAct, ScreenKind> = {
  read: "code", write: "code", run: "log", browse: "page", delegate: "link", other: "blank",
};

const SCREEN_BY_TOOL: Record<string, ScreenKind> = {
  codegraph: "search", memory_search: "search", fs_list: "search",
  screenshot: "page", read_attachment: "page",
};

/** `waiting` wins over everything else: a character waiting for a person is exactly what the
 *  viewer should see, even when a tool is still open in the background. */
function screenOf(a: ActorState): ScreenKind {
  if (a.waiting) return "wait";
  if (a.tool !== undefined) {
    const hit = SCREEN_BY_TOOL[a.tool];
    if (hit !== undefined) return hit;
  }
  if (a.act === undefined) return "blank";
  return SCREEN_BY_ACT[a.act];
}

/** Done beats everything, then waiting, then errors, otherwise work. */
function moodOf(a: ActorState): MoodKind {
  if (a.done !== undefined) return "done";
  if (a.waiting) return "wait";
  if (a.fails > 0) return "error";
  return "work";
}

// ═══ The floor layer ═════════════════════════════════════════════════════════

interface Piece {
  /** Sorting key = foot point. */
  y: number;
  draw(): void;
}

/** Top edge of the desktop, what a monitor is placed on. */
function deskTop(yBase: number): number {
  // Five rows into the desktop, not on its far edge. A screen whose foot sits exactly on the
  // edge floats above the desk; a few rows in, it **stands** on it, and that is the whole
  // difference between a picture of a workplace and a collage of one.
  return yBase - SIZE.desk.h + 5;
}

// ═══ Building the image ══════════════════════════════════════════════════════

export interface RenderOpts {
  /** The selected agent: gets a bright plate and a ring on the floor. */
  selected?: string;
  /** Agent under the pointer. */
  hover?: string;
  /** Agents the session tab does **not** mean right now. They are drawn pale and **not**
   *  removed: a removed agent would free its seat, the handover lines would point into nothing
   *  and the same room would look different depending on the tab. */
  dimmed?: ReadonlySet<string>;
}

/**
 * Paints a complete image into the 480×270 buffer.
 *
 * The caller must **not** clear the buffer: wall and floor cover it completely. A `clearRect`
 * would be a fourth context call in the contract and a superfluous full frame write per frame.
 * would be a fourth context call in the contract and a superfluous full frame write per frame.
 */
export function renderFrame(
  ctx: Ctx, frame: Frame, cam: Cam, grade: Grade, opts?: RenderOpts,
): void {
  const fit = camFit(cam);
  const v = viewOf(ctx, fit);
  // The characters are finely drawn (stage 2) and get the HD view; everything else still draws
  // in art units. Having both side by side is the purpose of the separation.
  const vh = viewHiOf(ctx, fit);
  const env = GRADES[grade];
  const day = grade === "day";
  const t = frame.t;

  // ── 1 shell ───────────────────────────────────────────────────────────────
  drawWall(vh, env);
  // Two continuous bands instead of eight single windows: one from the corner to the solid
  // piece of wall that carries the whiteboard and the rack, one from the door to the far
  // corner. The gaps are where something hangs, not where the glass happened to stop.
  drawWindowFront(vh, env, 14, 178, 8, 20);
  drawWindowFront(vh, env, 372, 466, 8, 20);
  drawBoard(v, BOARD_X, 34, env);
  drawPicture(v, PICTURE_LEFT_X, PICTURE_Y, env, "rug");
  drawPicture(v, PICTURE_RIGHT_X, PICTURE_Y, env, "clay");
  drawClock(v, CLOCK_X, 26, env);
  drawDoor(v, DOOR.x, WALL_H, env, { open: anyoneTravelling(frame) });
  drawFloor(vh, env);
  // Light comes after the floor and before the furniture: it lies on the planks but under
  // everything standing on them, otherwise desks would glow from the inside.
  drawLight(vh, env, WIN_XS, day);
  // The frame of the room, drawn after the floor and before everything standing on it: it is
  // the edge of the floor, not a piece of furniture.
  drawRoomEdges(vh, env);
  drawWalls(vh, env);
  drawPartitions(vh, env);
  drawCabinet(v, CABINET_X, CABINET_Y, env);
  drawPlant(v, CABINET_X, CABINET_Y - SIZE.cabinet.h, env, { small: true });
  // The rack is the only piece of scenery that tells something: on `idle` it draws unchanged,
  // otherwise one LED field per rack unit lights up.
  drawRack(v, RACK_X, RACK_Y, env, { state: frame.rack.state, since: frame.rack.since, t });

  // ── 2 Licht ───────────────────────────────────────────────────────────────
  if (day) {
    // Two carpets of light under the bands of windows. They are the only reason the day room
    // looks like day and not like "a brighter night".
    drawWindowLight(v, 96, WALL_H + 58, 150, 58, env);
    drawWindowLight(v, 419, WALL_H + 58, 88, 58, env);
  } else {
    // In the evening the light comes from the screens. A soft patch under every **occupied**
    // seat; the empty ones stay dark, and exactly that shows how full the room is.
    for (const a of frame.actors) {
      if (a.retired === true || a.pose !== "sit") continue;
      const s = seatFor(a);
      if (!s) continue;
      fillA(v, env, "lamp", 0.05, s.mon.x - 18, s.mon.y - 10, 36, 22);
      fillA(v, env, "lamp", 0.04, s.mon.x - 12, s.mon.y - 4, 24, 14);
    }
  }

  // ── 3 Teppich ─────────────────────────────────────────────────────────────
  //
  // Carpets are drawn **before** the sorted layer and not in it. They lie flat: their front
  // edge is not a foot point, and sorted along with the furniture they would land behind the
  // sofa standing on them and cover it. That was a real bug, and it looked as if the sofa had
  // never been drawn at all.
  drawZone(vh, TABLE.x, TABLE.y + 34, 104, 76, env);
  drawZone(vh, LOUNGE_X, LOUNGE_Y + 4, 64, 44, env);

  // ── 4 Bodenschicht ────────────────────────────────────────────────────────
  const world: Piece[] = [];
  buildRoom(world, v, vh, env, frame);
  buildActors(world, v, vh, env, frame, grade, opts);
  // Stable (ES2019): at the same foot point the insertion order wins, and that is "first the
  // chair, then the character on it".
  world.sort((a, b) => a.y - b.y);
  for (const p of world) p.draw();

  // ── 5 Luft ────────────────────────────────────────────────────────────────
  // Steam rises from the **top edge** of the machine. Computed from the foot point it would
  // rise inside the device and be invisible, the classic mistake with the foot point rule.
  steam(v, COFFEE.x, COFFEE.y - 6 - SIZE.coffee.h + 1, env, t, 0x4b4146);
  dust(v, env, t, 0x53544142);
  for (const fx of frame.fx) drawAirFx(v, env, fx, t);
  for (const a of frame.actors) {
    if (a.retired === true || a.pose !== "walk") continue;
    // Puffs of dust at the beat of the walk cycle: the age comes from `t`, not from a counter.
    footPuff(v, px(a.x), px(a.y), env, ((t % 480) / 480));
  }

  // ── 6 Spawn-Linien ────────────────────────────────────────────────────────
  drawLinks(v, env, frame, t);

  // ── 7 overlays ────────────────────────────────────────────────────────────
  drawOverlays(v, env, frame, grade, opts);

  // ── 8 Abstufung ───────────────────────────────────────────────────────────
  // Nothing to do. Day and evening are two palette tables, not a filter over the finished
  // image: a darkened day image would darken the monitors as well, although in the evening they
  // are exactly the light source that makes everything else visible.
}

/** Is somebody standing in the door right now? Then it is open. A detail that lets the room
 *  tell a story without an event having to be invented for it. */
function anyoneTravelling(frame: Frame): boolean {
  for (const a of frame.actors) {
    if (a.retired === true) continue;
    if (a.pose === "walk" && Math.abs(px(a.y) - DOOR.y) < 26) return true;
  }
  return false;
}

/** The seat of an actor: `deskIndex` 0..11 is a pod, `-1` the boss seat, `-2` away. */
function seatFor(a: ActorState): SeatPx | undefined {
  if (a.deskIndex >= 0 && a.deskIndex < SEATS_PX.length - 1) return SEATS_PX[a.deskIndex];
  if (a.deskIndex === -1) return SEATS_PX[SEATS_PX.length - 1];
  return undefined;
}

/** Put furniture and workplaces into the sorting list. */
function buildRoom(world: Piece[], v: Ctx, vh: Ctx, env: Pal, frame: Frame): void {
  // Who sits where, for the chair (occupied or free) and the monitor image.
  const byDesk = new Map<number, ActorState>();
  for (const a of frame.actors) {
    if (a.retired === true || a.pose !== "sit") continue;
    byDesk.set(a.deskIndex, a);
  }

  for (let i = 0; i < SEATS_PX.length; i++) {
    const s = SEATS_PX[i];
    const idx = i === SEATS_PX.length - 1 ? -1 : i;
    const who = byDesk.get(idx);
    const screen: ScreenKind = who ? screenOf(who) : "blank";
    const mood: MoodKind = who ? moodOf(who) : "work";

    world.push({
      y: s.desk.y,
      draw(): void {
        drawDesk(vh, s.desk.x, s.desk.y, env, { toward: s.flip ? 1 : -1 });
        drawMonitor(v, s.mon.x, deskTop(s.desk.y), env, { screen, mood, flip: s.flip });
      },
    });
    // The chair goes into the list **before** the character. Both have the same foot point and
    // the sort is stable, so the character sits on the chair and not behind it.
    world.push({
      y: s.sit.y,
      draw(): void {
        drawChair(vh, s.sit.x, s.sit.y, env, { occupied: who !== undefined, flip: s.flip });
      },
    });
  }

  // A round table with four chairs. The two at the back stand before the table in the list, the
  // two at the front after it; both follow from their foot point, not from the order.
  world.push({ y: TABLE.y - 6, draw: () => drawTableChair(v, TABLE.x - 26, TABLE.y - 6, env) });
  world.push({ y: TABLE.y - 6, draw: () => drawTableChair(v, TABLE.x + 26, TABLE.y - 6, env) });
  world.push({ y: TABLE.y, draw: () => drawMeetingTable(v, TABLE.x, TABLE.y, env) });
  world.push({
    y: TABLE.y + 14,
    draw: () => drawTableChair(v, TABLE.x - 30, TABLE.y + 14, env, { flip: true }),
  });
  world.push({ y: TABLE.y + 14, draw: () => drawTableChair(v, TABLE.x + 30, TABLE.y + 14, env) });

  world.push({ y: COFFEE.y - 6, draw: () => drawCoffee(v, COFFEE.x, COFFEE.y - 6, env) });

  // ── The lounge, front left ────────────────────────────────────────────────
  //
  // The front strip of the room (from row 215 down) was empty over its whole width: no seat,
  // no path, nothing. That is a third of the picture, and an empty third reads as a level that
  // was never finished. It gets what an office has and this one did not: somewhere to sit that
  // is not a desk.
  //
  // The places are fixed numbers and not fractions of `SCENE`, because none of them is a place
  // where anything **happens**: nobody walks there, no seat is resolved there. Scenery is
  // measured in buffer pixels, the same argument as for the windows and the rack.
  world.push({ y: LOUNGE_Y - 13, draw: () => drawSofa(v, LOUNGE_X, LOUNGE_Y - 13, env) });
  world.push({ y: LOUNGE_Y, draw: () => drawLowTable(v, LOUNGE_X + 4, LOUNGE_Y, env) });
  world.push({ y: LOUNGE_Y - 6, draw: () => drawPlant(v, LOUNGE_X + 24, LOUNGE_Y - 6, env) });

  // ── Shelves, front right ──────────────────────────────────────────────────
  world.push({ y: SHELF_Y, draw: () => drawShelf(v, SHELF_X, SHELF_Y, env) });
  world.push({ y: SHELF_Y, draw: () => drawShelf(v, SHELF_X + SHELF.w + 3, SHELF_Y, env) });
  world.push({ y: SHELF_Y + 14, draw: () => drawPlant(v, SHELF_X + 40, SHELF_Y + 14, env) });

  // The water cooler stands beside the door, where one stands anyway.
  world.push({ y: COOLER_Y, draw: () => drawCooler(v, COOLER_X, COOLER_Y, env) });

  world.push({ y: 250, draw: () => drawPlant(v, 24, 250, env) });
}

/** Put characters into the same sorting list: that is the core of layer 4. */
function buildActors(
  world: Piece[], v: Ctx, vh: Ctx, env: Pal, frame: Frame, grade: Grade, opts?: RenderOpts,
): void {
  const sel = opts?.selected;
  const hov = opts?.hover;
  const paleSet = opts?.dimmed;
  const paleV = paleSet !== undefined ? dimOf(v, DIM_ALPHA) : v;
  // The same paleness once more for the fine view: the character is drawn over it,
  // ihr Markierungsring darunter in Kunsteinheiten.
  const paleVh = paleSet !== undefined ? dimOf(vh, DIM_ALPHA) : vh;
  for (const a of frame.actors) {
    if (a.retired === true) continue;
    const cx = px(a.x);
    const yBase = px(a.y);
    // Nothing is drawn outside the buffer. The gathering point for "away" lies at `y = -100` in
    // `room.ts`, so far above the ceiling. Without this check the drawing loop would run
    // completely once for every character pushed away.
    if (cx < -FIG_W || cx > ART.w + FIG_W || yBase < -FIG_H || yBase > ART.h + FIG_H) continue;

    // Shirt, hair and shoulders come from the **role**, everything else from the run seed:
    // `a.role` is already in the `Frame`, so no new field is needed in layer 0.
    const look = lookOf(a.seed, rolesSeed(a.role, a.seed));
    const pal = palFor(grade, look);
    const ring = a.id === sel ? "acc" : a.id === hov ? "wallHi" : undefined;
    const ghost = a.deskIndex === -2;
    const pale = paleSet !== undefined && paleSet.has(a.id);
    world.push({
      y: yBase,
      draw(): void {
        if (ring) {
          // A marking ring on the floor instead of an outline around the character: an outline
          // would have to be laid pixel by pixel around an assembled sprite, a ring is four
          // rectangles, and it does not cover the character.
          fillA(v, env, ring, 0.55, cx - 7, yBase, 14, 1);
          fillA(v, env, ring, 0.35, cx - 8, yBase - 1, 1, 2);
          fillA(v, env, ring, 0.35, cx + 7, yBase - 1, 1, 2);
          fillA(v, env, ring, 0.25, cx - 6, yBase - 2, 12, 1);
        }
        // The pale wrapper is armed **after** the ring: `fillA` sets the opacity back to 1 at
        // the end and would otherwise erase the wrapper right away. The ring stays bright: that
        // something is selected holds even when the filter dims it.
        const c = pale ? paleVh : vh;
        if (pale) c.globalAlpha = 1;
        if (ghost) drawGhost(c, cx * 2, yBase * 2, pal, frame.t, a.seed, look);
        else drawActor(c, a, frame.t, pal);
        if (pale) c.globalAlpha = 1;
      },
    });
  }
}

/** Short lived effects that lie in the air (spark, falling note). */
function drawAirFx(v: Ctx, env: Pal, fx: Fx, t: number): void {
  const span = Math.max(1, fx.until - fx.t0);
  const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
  const cx = px(fx.x);
  const y = px(fx.y);
  if (fx.kind === "spark") {
    spark(v, cx, y - FIG_H + 6, env, age, fx.seed);
  } else if (fx.kind === "drop") {
    // A sheet falls onto the desk: it sinks and lies down flat. Four rectangles.
    const drop = Math.round(age * 8);
    const yy = y - FIG_H + 2 + drop;
    fillA(v, env, "paper", 1 - age * 0.3, cx + 6, yy, 5, 4);
    fillA(v, env, "ink", 0.6 - age * 0.3, cx + 7, yy + 1, 3, 1);
  }
}

/** Spawn and handover lines. */
function drawLinks(v: Ctx, env: Pal, frame: Frame, t: number): void {
  const at = new Map<string, Pt>();
  for (const a of frame.actors) at.set(a.id, { x: px(a.x), y: px(a.y) - FIG_H + 6 });

  for (const fx of frame.fx) {
    if (fx.kind !== "link" || !fx.to) continue;
    const span = Math.max(1, fx.until - fx.t0);
    const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
    linkLine(v, { x: px(fx.x), y: px(fx.y) - FIG_H + 6 },
      { x: px(fx.to.x), y: px(fx.to.y) - FIG_H + 6 }, env, age);
  }

  // The line that travels along: while `link` stands it hangs on the **current** positions of
  // both characters. The `Fx` above is the flash at the trigger, this one is the band.
  for (const a of frame.actors) {
    if (a.link === undefined || a.retired === true) continue;
    const from = at.get(a.id);
    const to = at.get(a.link.to);
    if (!from || !to) continue;
    linkLine(v, from, to, env, Math.max(0, Math.min(1, 1 - (a.link.until - t) / LINK_MS)));
  }
}

// ═══ Overlays ════════════════════════════════════════════════════════════════

/** The role name, shortened to what fits on a plate. `exec_agent` becomes `EXEC`.
 *  The part before the first underscore is the meaningful one in all roles here.
 *  (`plan_agent`, `exec_agent`, `review_agent`, `assistant`). */
function shortRole(role: string): string {
  const cut = role.indexOf("_");
  const head = cut > 0 ? role.slice(0, cut) : role;
  return head.length > 9 ? head.slice(0, 9) : head;
}

function drawOverlays(
  v: Ctx, env: Pal, frame: Frame, grade: Grade, opts?: RenderOpts,
): void {
  const t = frame.t;
  const sel = opts?.selected;
  const hov = opts?.hover;
  const paleSet = opts?.dimmed;
  const paleV = paleSet !== undefined ? dimOf(v, DIM_ALPHA) : v;

  for (const a of frame.actors) {
    if (a.retired === true) continue;
    const cx = px(a.x);
    const yBase = px(a.y);
    if (cx < -FIG_W || cx > ART.w + FIG_W || yBase < 0 || yBase > ART.h + FIG_H) continue;

    const chosen = a.id === sel;
    const hovered = a.id === hov;
    // Plate and bubble share the fate of the character: if it does not belong to the session
    // tab, its label goes pale as well. A bright plate over a pale character would read as
    // Zeichenfehler.
    const pale = paleSet !== undefined && paleSet.has(a.id);
    const c = pale ? paleV : v;
    if (pale) c.globalAlpha = 1;

    // Plates for everybody, but pale: a room with twelve equally bright labels reads as a
    // table, not as a room. Only what was asked for becomes bright.
    //
    // **Above the head, not under the feet.** Under the feet the label sat on the floor in
    // front of the character and covered the very row that says where they stand; and with
    // twelve of them the floor read as a list of captions. Above the head it belongs to its
    // figure and follows it, which is what every game that shows names over avatars does.
    const plateH = nameplate(c, cx, yBase - FIG_H - 2, env, shortRole(a.role), {
      sub: chosen ? (a.issue ?? a.model ?? undefined) : undefined,
      selected: chosen,
      dim: !chosen && !hovered,
    });

    // The bubble moves up by exactly the height of the plate: both belong to the same figure,
    // and one covering the other would make the room look broken.
    const top = yBase - FIG_H - 3 - plateH;
    if (a.say !== undefined) {
      speechBubble(c, cx, top, env, a.say, {
        reveal: revealOf(a.say, a.sayAt !== undefined ? t - a.sayAt : 0),
        verdict: a.verdict,
      });
    } else if (a.think !== undefined) {
      thoughtBubble(c, cx, top, env, a.think, t);
    }
    if (pale) v.globalAlpha = 1;
  }

  // Emotes last: they are the short signal and must not be covered by anything.
  for (const fx of frame.fx) {
    if (fx.kind !== "emote" || fx.text === undefined) continue;
    const g = fx.text;
    if (g !== "✓" && g !== "✗" && g !== "!") continue;
    const span = Math.max(1, fx.until - fx.t0);
    const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
    // Above the name plate, not over it. The plate moved over the head, and an emote at the
    // old height landed exactly in the middle of the word.
    emotePop(v, px(fx.x) + 9, px(fx.y) - FIG_H - 4 - PLATE_H, env, g, age);
  }
}

// ═══ Hit testing ═════════════════════════════════════════════════════════════

/**
 * Which agent stands under this point? `px`/`py` are **buffer coordinates** (the stage converts
 * the pointer position into them, because only it knows the blit factor).
 *
 * Testing goes from **front to back**, so backwards through the list sorted by `y`. Front means
 * further down means drawn later, and what lies on top has to be clickable. A test in list
 * order would hit the character behind it, and the bug would look like a rounding problem.

 */
export function hitTest(
  frame: Frame, cam: Cam, pxCoord: number, pyCoord: number,
): string | undefined {
  const fit = camFit(cam);
  const bx = (pxCoord - fit.ox) / fit.z;
  const by = (pyCoord - fit.oy) / fit.z;

  for (let i = frame.actors.length - 1; i >= 0; i--) {
    const a = frame.actors[i];
    if (a.retired === true) continue;
    const box = actorBox(px(a.x), px(a.y));
    if (bx >= box.x && bx < box.x + box.w && by >= box.y && by < box.y + box.h) return a.id;
  }
  return undefined;
}

/** Where a character stands in the buffer: for the stage and the inspector. */
export function actorAt(frame: Frame, id: string): Pt | undefined {
  for (const a of frame.actors) {
    if (a.id === id) return { x: px(a.x), y: px(a.y) };
  }
  return undefined;
}
