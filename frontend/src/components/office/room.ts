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
// die Begründung.

import { MAX_SEATS, POS_SCALE, SCENE } from "./const.ts";
import { hash32 } from "./ids.ts";
import type { Pt, Room, Seat } from "./types.ts";

// ── Dimensions ───────────────────────────────────────────────────────────────

/** Pod seats: two banks of six. The thirteenth seat (`MAX_SEATS`) is the chief's seat and is
 *  never handed out; the root run is assigned to it (`deskIndex === -1`). */
export const POD_SEATS = MAX_SEATS - 1;

/** Horizontal centre of the two banks. Left 23 %, right 79 %, not symmetric, because the
 *  chief's seat sits top right and the right bank moves a little closer to the wall. */
export const BANK_X: readonly number[] = [Math.round(SCENE.w * 0.23), Math.round(SCENE.w * 0.79)];

/** Three rows per bank, at 37 / 58 / 78 % of the height. The distances increase slightly
 *  towards the front (21 to 20 points), which pulls the room into depth without any
 *  perspective arithmetic anywhere. */
export const ROW_Y: readonly number[] = [
  Math.round(SCENE.h * 0.37), Math.round(SCENE.h * 0.58), Math.round(SCENE.h * 0.78),
];

/** Distance of a seat from the centre of the desk. A desk is a bench for two: one figure sits
 *  left and one right, and both look at the same top. */
export const SEAT_DX = 92;

/** The foot point sits a little **below** the centre of the desk. That is not a beauty value:
 *  the scene sorts by `y` (painter's algorithm), and a figure with the same `y` as its desk
 *  landed in front of it or behind it depending on the insertion order. */
export const SEAT_DY = 14;

/** Half the width of the round table respectively half its depth; the waypoints of the huddle
 *  lie on it. East and west stand further out than north and south, because a figure needs
 *  more room sideways than in front of or behind the table. */
const TABLE_RX = 172;
const TABLE_RY = 112;

// ── Sitze ────────────────────────────────────────────────────────────────────

function seat(deskX: number, deskY: number, side: number): Seat {
  // side 0 = left of the desk (looking right), side 1 = right (looking left).
  const left = side === 0;
  return {
    desk: { x: deskX, y: deskY },
    sit: { x: deskX + (left ? -SEAT_DX : SEAT_DX), y: deskY + SEAT_DY },
    flip: !left,
  };
}

/** `seats[0..11]` = Pod, `seats[12]` = Chefplatz.
 *
 *  Numbering: `bank * 6 + row * 2 + side`. That puts seats 0 to 5 completely in the left bank
 *  and 6 to 11 in the right one, so two runs with neighbouring numbers sit together instead
 *  of being scattered across the room. */
function buildSeats(): Seat[] {
  const out: Seat[] = [];
  for (const x of BANK_X) {
    for (const y of ROW_Y) {
      out.push(seat(x, y, 0));
      out.push(seat(x, y, 1));
    }
  }
  // Chief's seat: 61 % / 32 %, looking left into the room.
  const cx = Math.round(SCENE.w * 0.61);
  const cy = Math.round(SCENE.h * 0.32);
  out.push({ desk: { x: cx, y: cy }, sit: { x: cx, y: cy + SEAT_DY }, flip: true });
  return out;
}

// ── The room ─────────────────────────────────────────────────────────────────

const SEATS = buildSeats();

/** Door threshold: 73 % / 13.5 %, so in the upper wall right of the centre. */
const DOOR: Pt = { x: Math.round(SCENE.w * 0.73), y: Math.round(SCENE.h * 0.135) };

/** Centre of the round table: 51 % / 55 %. Exactly in the aisle between the banks (23 % and
 *  79 %) and below the chief's seat, the only spot on which four figures can stand without
 *  covering a desk. */
const TABLE: Pt = { x: Math.round(SCENE.w * 0.51), y: Math.round(SCENE.h * 0.55) };

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
  coffee: { x: Math.round(SCENE.w * 0.065), y: Math.round(SCENE.h * 0.25) },
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
 *  Determinismus geht hier vor Realismus.
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
