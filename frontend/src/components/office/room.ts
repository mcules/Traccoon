// Layer 0: the geometry of the office. Built once, only read afterwards.
//
// **Everything here is in SCENE coordinates (1600x900).** The x0.3 scaling onto the 480x270
// buffer happens exclusively while rendering and exclusively for positions; sprites are never
// scaled along (PIXEL-CONTRACT.md rule 1). Whoever stored a size in buffer pixels here would
// miscalculate half the drawing layer by a factor of 3.3.
//
// The floor plan is deliberately plain and legible: two desk banks left and right, a free
// aisle between them for the round table, the chief's seat top right as an eye catcher, the
// door at the top, coffee on the far left. The reasoning for every chosen value stands below.

import { MAX_SEATS, PARTITIONS, POS_SCALE, SCENE, WALLS } from "./const.ts";
import { hash32 } from "./ids.ts";
import type { Pt, Room, Seat } from "./types.ts";

// ── Dimensions ───────────────────────────────────────────────────────────────

/** Pod seats: three clusters of four. The thirteenth seat (`MAX_SEATS`) is the chief's seat and
 *  is never handed out; the root run is assigned to it (`deskIndex === -1`). */
export const POD_SEATS = MAX_SEATS - 1;

/**
 * The floor plan: **three clusters of four**, not two benches of six.
 *
 * The old plan was a grid: two banks along the side walls, three rows each, and one table in
 * the middle of an otherwise empty hall. It read as a furniture showroom, and no amount of
 * redrawing changed that, because what looked wrong was not the art but the arrangement. A
 * real open plan office is **clusters**: four desks that belong together, an aisle around
 * them, and the rest of the floor given over to something that is not a desk.
 *
 * Everything here is measured in scene coordinates and lands on whole art units after
 * `POS_SCALE`; the values are chosen so that they do (`x * 0.3` is an integer).
 */
export const CLUSTERS: readonly Pt[] = [
  { x: 300, y: 467 },   // open plan, west
  { x: 833, y: 467 },   // open plan, east
  { x: 567, y: 740 },   // open plan, in front
];

/** Offsets of the four seats around the centre of a cluster: two columns 38 art units apart (a
 *  desk is 34 wide plus an aisle) and two rows 44 apart. */
export const SEAT_DX = 63;
export const SEAT_DY_ROW = 73;

/** How far the desk stands **above** its seat. Not a beauty value: the scene sorts by `y`
 *  (painter's algorithm), and a person sits in front of their own desk. */
export const SEAT_DY = -53;

/** The desk in art units, the size `pixel/furniture.ts` draws it at. It stands here because
 *  the obstacle list is built from it, and a second number would be the first place where the
 *  drawing and the route finding drift apart. */
export const DESK_W_ART = 34;
export const DESK_H_ART = 14;

/** Half the width of the round table respectively half its depth; the waypoints of the huddle
 *  lie on it. East and west stand further out than north and south, because a figure needs
 *  more room sideways than in front of or behind the table. */
// The waypoints of the huddle have to clear three things at once: the table box, the partition
// wall to the west and the wall to the archive in the south. The window that leaves is narrow,
// and the check `routes avoid the furniture` is what found its edges.
const TABLE_RX = 125;
const TABLE_RY = 100;

// ── Sitze ────────────────────────────────────────────────────────────────────

function seat(sitX: number, sitY: number, right: boolean): Seat {
  return {
    desk: { x: sitX, y: sitY + SEAT_DY },
    sit: { x: sitX, y: sitY },
    flip: right,
  };
}

/** `seats[0..11]` = pods, `seats[12]` = the chief's seat.
 *
 *  Numbering: `cluster * 4 + row * 2 + column`. Four consecutive numbers are therefore one
 *  cluster, so two runs with neighbouring numbers sit **together** instead of being scattered
 *  across the room. That is the point of a cluster. */
function buildSeats(): Seat[] {
  const out: Seat[] = [];
  for (const c of CLUSTERS) {
    for (const dy of [-SEAT_DY_ROW, SEAT_DY_ROW]) {
      out.push(seat(c.x - SEAT_DX, c.y + dy, false));
      out.push(seat(c.x + SEAT_DX, c.y + dy, true));
    }
  }
  // The chief's desk: alone in the aisle between the clusters and the meeting area, looking
  // away from us. A corner office would need walls, and walls are something the engine walks
  // straight through (there is no path finding, see `startTrip`).
  out.push(seat(1067, 433, true));
  return out;
}

// ── The room ─────────────────────────────────────────────────────────────────

const SEATS = buildSeats();

/** Door threshold: 73 % / 13.5 %, so in the upper wall right of the centre. */
const DOOR: Pt = { x: Math.round(SCENE.w * 0.73), y: Math.round(SCENE.h * 0.135) };

/** Centre of the round table. It moves **below** the cluster band with the new plan: the
 *  clusters take the upper half of the floor, and everything that is not a desk gets the lower
 *  half. Four figures can stand around it there without covering a workplace. */
const TABLE: Pt = { x: 1390, y: 467 };

/** Standing place in front of the server rack, **thought of in buffer pixels and converted back**.
 *
 *  All other points of this file are fractions of `SCENE`; the rack is not: it is a piece of
 *  furniture and stands in `pixel/scene.ts` at a buffer coordinate (`RACK_X`/`RACK_Y`),
 *  because the back wall is scenery and not a place where a percentage would mean anything.
 *  An invented percentage here would hit the rack only by chance.
 *
 *  The 18 pixels of offset to the right are the actual measure: a figure is 16 buffer pixels
 *  wide and would stand centred **in front of** the rack it is meant to show; with the offset
 *  it stands beside it. Check 11 (`RACK_PX ≡ round(ROOM.rack × POS_SCALE)`) holds both numbers together. */
const RACK_BUF = { x: 224 + 18, y: 60 + 8 };
const RACK: Pt = {
  x: Math.round(RACK_BUF.x / POS_SCALE),
  y: Math.round(RACK_BUF.y / POS_SCALE),
};

export const ROOM: Room = {
  seats: SEATS,
  door: DOOR,
  table: TABLE,
  // Fixed order north · west · east · south. Fixed, because the engine puts the nth arrival on
  // the nth place: a different order would give a different picture when rewinding.
  huddle: [
    { x: TABLE.x, y: TABLE.y - TABLE_RY },
    { x: TABLE.x - TABLE_RX, y: TABLE.y },
    { x: TABLE.x + TABLE_RX, y: TABLE.y },
    { x: TABLE.x + 0, y: TABLE.y + TABLE_RY },
  ],
  // Coffee corner: 6.5 % / 25 %, on the far left in front of the bank; the way there passes no
  // desk, because otherwise the coffee break would run right across the picture.
  coffee: { x: 83, y: 400 },
  // Assembly point outside the picture (`deskIndex === -2`). It lies **behind the door**, not
  // somewhere at the edge: figures enter and leave the room through the door, and a second
  // exit would let them walk through the wall. The negative `y` also sorts it behind
  // everything else, should the drawing layer ever touch it.
  away: { x: DOOR.x, y: -100 },
  rack: RACK,
};

// ── Sitzvergabe ──────────────────────────────────────────────────────────────

/** Which pod seat an agent gets: `hash32(id) % 12` with linear probing.
 *
 *  Deterministic **without** a queue, and that is intentional: a seat that depends on the
 *  arrival order does not replay identically. When rewinding, the same runs come in the same
 *  `seq` order, but a FIFO buffer would carry state across the reset, and then the same agent
 *  would sit somewhere else on the second viewing. Determinism goes before realism here.
 *
 *  If everything is taken there is no chair: `-2` means "away", and the figure stays outside
 *  the room. No special place, no stacked figures on the same chair.
 *
 *  `taken` contains exclusively pod numbers (0..11). The chief's seat (`-1`) is not handed
 *  out but set. */
export function seatOf(id: string, taken: ReadonlySet<number>): number {
  const start = hash32(id) % POD_SEATS;
  for (let i = 0; i < POD_SEATS; i++) {
    const slot = (start + i) % POD_SEATS;
    if (!taken.has(slot)) return slot;
  }
  return -2;
}

/** Foot point of a seat. `-1` = chief's seat, `-2` (and anything outside 0..12) = away.
 *
 *  Returns a **copy**. A caller who accidentally computes `pt.x += 3` would otherwise move
 *  the chair for the rest of the session, and on the next replay it would be somewhere else
 *  again. */
export function podSeat(slot: number): Pt {
  const src = slot === -1 ? SEATS[POD_SEATS].sit
    : (slot >= 0 && slot < POD_SEATS ? SEATS[slot].sit : ROOM.away);
  return { x: src.x, y: src.y };
}

// ═══ Obstacles and route finding ═════════════════════════════════════════════
//
// Until now a figure walked a **straight line** from where it stood to where it wanted to go,
// and it walked through desks, through the meeting table and through the sofa. In a room seen
// from above that is not a small blemish: the whole point of a top-down room is that the
// furniture stands somewhere, and a person who walks through it says it does not.
//
// What this is **not**: a physics engine. There is no collision at every tick, no pushing
// apart, no reaction to other figures. Two people may walk through each other, and that is
// deliberate: figures are not obstacles, they move, and a route computed against a moving
// obstacle would have to be recomputed, which would make it depend on the tick size and would
// take the determinism with it (rule 3.4).
//
// What it is: the route is computed **once**, when the trip starts, out of a fixed list of
// rectangles. From then on the trip is a function of time, exactly as before, only along a
// polyline instead of a straight line.

/** An axis parallel rectangle in scene coordinates, given by its centre and its size, because
 *  that is how every piece of furniture in this room is placed. */
export interface Box { x: number; y: number; w: number; h: number }

/** How far a route keeps away from an obstacle. Half a figure plus a little air: a person
 *  brushing the edge of a desk with their shoulder looks like a collision that was not caught,
 *  which is worse than one obviously walked around. */
const CLEARANCE = 22;

/** Scene units per art unit. The furniture sizes are art (that is where they are drawn), the
 *  routing is scene (that is where the engine computes). */
const PER_ART = 1 / 0.3;

function box(cx: number, cy: number, wArt: number, hArt: number): Box {
  return { x: cx, y: cy, w: wArt * PER_ART, h: hArt * PER_ART };
}

/**
 * Everything a person cannot walk through.
 *
 * Built from the same numbers the drawing uses, not typed a second time: a desk stands
 * `-SEAT_DY` above its seat and is 34 by 14 art units, so that is what stands here. Whoever
 * moves a desk in `pixel/furniture.ts` and forgets this list gets a figure walking through it
 * again, which is why the sizes are named and not repeated.
 */
function buildBlocked(seats: readonly Seat[]): Box[] {
  const out: Box[] = [];
  for (const s of seats) {
    // The desk. Its foot point is `s.desk.y`, so its centre lies half a height above it.
    out.push(box(s.desk.x, s.desk.y - (DESK_H_ART * PER_ART) / 2, DESK_W_ART, DESK_H_ART));
  }
  // The round table with its chairs, the lounge and the shelves. All of them are drawn in
  // `pixel/scene.ts` at fixed buffer coordinates; the conversion is the same one `px()` does
  // there, only the other way round.
  // The table box is measured against what is **drawn**: the top is 52 by 13 art units with
  // its foot at `TABLE.y`, and a chair stands above and below it. Taken too small, a figure
  // clips the chair; taken too high, it swallows the seat of the nearest cluster and the route
  // finding then treats the table as "the target is inside" and walks straight through it.
  out.push(box(TABLE.x, TABLE.y - 3 * PER_ART, 56, 36));
  out.push(box(322 / 0.3, (255 - 20) / 0.3, 64, 44));     // break corner: sofa, table, plant
  out.push(box(417 / 0.3, (243 - 11) / 0.3, 68, 24));     // archive: the two shelves
  out.push(box(224 / 0.3, (60 - 17) / 0.3, 22, 36));      // server rack
  out.push(box(196 / 0.3, (60 - 8) / 0.3, 18, 17));       // filing cabinet
  // The walls. Given by their top left corner in art units, so the centre is half a size
  // further on; the doorways are the gaps between the segments and need no entry.
  for (const w of [...WALLS, ...PARTITIONS]) {
    out.push(box((w.x + w.w / 2) / 0.3, (w.y + w.h / 2) / 0.3, w.w, w.h));
  }

  // **The outer walls of the room.** Without them the route finding puts its corner nodes
  // outside the picture and a figure walks around the outside of the building to reach the
  // door. That was a real bug and it looked exactly as absurd as it sounds: the visibility
  // graph does not know what "the room" is, it only knows rectangles, so the boundary has to be
  // one too.
  //
  // The north wall has a **gap at the door**: that is the way in and out, and `away` (the
  // assembly point of a figure without a desk) lies behind it. Without the gap there would be
  // no route out at all and the fallback would send everybody through the wall again.
  const ART_W = 480, ART_H = 270, EDGE = 8, NORTH = 38;
  const doorGapL = 336, doorGapR = 366;
  out.push(box((doorGapL / 2) / 0.3, (NORTH / 2) / 0.3, doorGapL, NORTH));
  out.push(box(((doorGapR + ART_W) / 2) / 0.3, (NORTH / 2) / 0.3, ART_W - doorGapR, NORTH));
  out.push(box((EDGE / 2) / 0.3, ((NORTH + ART_H) / 2) / 0.3, EDGE, ART_H - NORTH));
  out.push(box(((ART_W - EDGE / 2)) / 0.3, ((NORTH + ART_H) / 2) / 0.3, EDGE, ART_H - NORTH));
  out.push(box((ART_W / 2) / 0.3, (ART_H - EDGE / 2) / 0.3, ART_W, EDGE));
  return out;
}

/** Does the segment `a`→`b` cut the rectangle `r`? Slab test, and the only geometry in this
 *  file: everything is axis parallel, so a segment against a rectangle is four comparisons and
 *  no square root. */
function hits(a: Pt, b: Pt, r: Box, pad: number): boolean {
  const hw = r.w / 2 + pad, hh = r.h / 2 + pad;
  const x0 = r.x - hw, x1 = r.x + hw, y0 = r.y - hh, y1 = r.y + hh;
  // Trivially outside on one side: no cut, and this is the case that ends almost all tests.
  if ((a.x < x0 && b.x < x0) || (a.x > x1 && b.x > x1)) return false;
  if ((a.y < y0 && b.y < y0) || (a.y > y1 && b.y > y1)) return false;
  const dx = b.x - a.x, dy = b.y - a.y;
  let lo = 0, hi = 1;
  for (const [p, q] of [[-dx, a.x - x0], [dx, x1 - a.x], [-dy, a.y - y0], [dy, y1 - a.y]]) {
    if (p === 0) { if (q < 0) return false; continue; }
    const t = q / p;
    if (p < 0) { if (t > hi) return false; if (t > lo) lo = t; }
    else { if (t < lo) return false; if (t < hi) hi = t; }
  }
  return lo <= hi;
}

function inside(p: Pt, r: Box, pad: number): boolean {
  return Math.abs(p.x - r.x) <= r.w / 2 + pad && Math.abs(p.y - r.y) <= r.h / 2 + pad;
}

/**
 * The route from `from` to `to` as a polyline **without** the starting point.
 *
 * A visibility graph over the corners of the obstacles, and nothing cleverer: with twenty
 * rectangles that is eighty nodes, and a trip happens a few times a minute, not a few times a
 * frame. A grid search would give the same answer in blocky steps; the corners give the line a
 * person actually walks, straight across the aisle and around the edge of the desk.
 *
 * Rectangles that contain the start or the target are **left out**. Otherwise a seat in front
 * of its own desk would be unreachable as soon as the clearance reaches over it, and the
 * figure would stand still forever, which is a far worse bug than walking through a table.
 */
export function route(from: Pt, to: Pt, blocked: readonly Box[]): Pt[] {
  const rects = blocked.filter((r) => !inside(from, r, CLEARANCE) && !inside(to, r, CLEARANCE));
  const free = (a: Pt, b: Pt): boolean => !rects.some((r) => hits(a, b, r, CLEARANCE));
  if (free(from, to)) return [to];

  // Nodes: start, target, and the four corners of every rectangle, pushed outwards by the
  // clearance so that a corner is a place one can actually stand.
  const nodes: Pt[] = [from, to];
  for (const r of rects) {
    const hw = r.w / 2 + CLEARANCE + 2, hh = r.h / 2 + CLEARANCE + 2;
    for (const sx of [-1, 1]) {
      for (const sy of [-1, 1]) {
        const p = { x: r.x + sx * hw, y: r.y + sy * hh };
        if (!rects.some((o) => inside(p, o, CLEARANCE))) nodes.push(p);
      }
    }
  }

  // Dijkstra. No heap: with under a hundred nodes the linear scan for the smallest is faster
  // than maintaining one, and it keeps the order of visits deterministic, which a heap with
  // equal keys would not.
  const n = nodes.length;
  const distTo = new Array<number>(n).fill(Infinity);
  const prev = new Array<number>(n).fill(-1);
  const done = new Array<boolean>(n).fill(false);
  distTo[0] = 0;
  for (;;) {
    let u = -1;
    for (let i = 0; i < n; i++) if (!done[i] && distTo[i] < (u < 0 ? Infinity : distTo[u])) u = i;
    if (u < 0 || u === 1) break;
    done[u] = true;
    for (let v = 0; v < n; v++) {
      if (done[v] || v === u) continue;
      if (!free(nodes[u], nodes[v])) continue;
      const d = distTo[u] + dist(nodes[u], nodes[v]);
      if (d < distTo[v]) { distTo[v] = d; prev[v] = u; }
    }
  }

  if (prev[1] < 0) return [to];   // nothing found: better a straight line than a figure frozen
  const out: Pt[] = [];
  for (let i = 1; i !== 0 && i >= 0; i = prev[i]) out.unshift(nodes[i]);
  return out;
}

/** Distance of two points. Local, because `room.ts` may not import the engine. */
function dist(a: Pt, b: Pt): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  return Math.sqrt(dx * dx + dy * dy);
}

/** Everything a person cannot walk through. Built once from the seats, so it cannot drift
 *  away from where the desks actually stand. */
export const BLOCKED: readonly Box[] = buildBlocked(SEATS);
