// Layer 1, the furniture and the set.
//
// Scale (rule 1 of the pixel contract, the rule people fail at): the buffer is 480×270, and
// `POS_SCALE = 0.3` applies **to positions only**. A character is 16×24 **buffer pixels**.
// Everything here is measured against that character, not against scene coordinates:
//
//   character      16 × 24     — the scale everything else hangs on
//   desk           26 × 12     — fits next to the character without hiding it
//   office chair   10 × 15
//   monitor        16 × 13     — stands on the desktop, not on the floor
//   runder Tisch   52 × 13
//   door           20 × 34     — reaches from the floor line almost to the ceiling
//   Fenster        30 × 22     — kachelbar, siehe `WINDOW_STEP`
//   filing cabinet 16 × 15     — hip high, a shelf for the small plant
//   server rack    20 × 34     — one and a half character heights; tall **instead of** wide, see there
//
// The wall takes the top `WALL_H = 38` rows, the floor the remaining 232.
//
// Wall, floor, carpet and window light are **procedural**: they are surfaces, not motifs. As
// art they would together be larger than the entire remaining art budget and could not be
// stretched to arbitrary room widths.

import type { Ctx, RackState } from "../types.ts";
import { ART } from "../const.ts";
import { mix } from "../ids.ts";
import type { Pal, PalKey } from "./palette.ts";
import { artH, artLeft, artW, defineArt, drawArt, fill, fillA, doubled } from "./art.ts";

// ═══ Dimensions of the set ═══════════════════════════════════════════════════

/** Height of the back wall in buffer pixels. Everything below is floor, so `WALL_H` is also the
 *  floor line that the door, the cabinets and the back row of desks stand on. */
export const WALL_H = 38;

/** The set in buffer pixels: wall, floor and light are finely drawn since stage 3 and get the
 *  HD view. All other furniture still draws in art units, and having both side by side is the
 *  purpose of the separation (PIXEL-CONTRACT rule 1). */
const HD = 2;

/** Step when lining up windows: two windows share the post, otherwise a frame seam 4 pixels
 *  wide would stand between them. */
export const WINDOW_STEP = 28;

// ═══ The art ═════════════════════════════════════════════════════════════════
//
// The legend is separate per piece of art: `M` means something different in the monitor than
// in the cabinet. What must appear in **no** furniture art: `S H T P h s t`. Those seven
// characters are reserved for the character and would take the skin colour of the agent here.

// ── Schreibtisch ─────────────────────────────────────────────────────────────
// Top, front edge, modesty panel, two metal sides. 26×12: narrow enough that a character 16
// pixels wide stands in front of it without the desk framing them.

const DESK = defineArt([
  "DDDDDDDDDDDDDDDDDDDDDDDDDD",
  "DDDDDDDDDDDDDDDDDDDDDDDDDD",
  "dddddddddddddddddddddddddd",
  ".MMddddddddddddddddddddMM.",
  ".MMddddddddddddddddddddMM.",
  ".MMddddddddddddddddddddMM.",
  ".MMddddddddddddddddddddMM.",
  ".MMddddddddddddddddddddMM.",
  ".MM....................MM.",
  ".MM....................MM.",
  ".MM....................MM.",
  ".MM....................MM.",
], { D: "desk", d: "deskLo", M: "metal" });

// ── Office chair ─────────────────────────────────────────────────────────────
// Two versions. The occupied one leaves the seat free: the character sits there, and a chair
// shining through beneath them would look like a drawing error. The foot stays visible in both
// versions: it sticks out sideways under the character and is the detail that makes "sitting at
// the desk" recognisable at all.

const CHAIR_LEGS = [
  "....MM....",
  "....MM....",
  "...MMMM...",
  "..MMMMMM..",
  ".M.M..M.M.",
];

const CHAIR_FREE = defineArt([
  "..CCCCCC..",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".cccccccc.",
  "....MM....",
  "..CCCCCC..",
  ".CCCCCCCC.",
  ".cccccccc.",
  ...CHAIR_LEGS,
], { C: "chair", c: "chairLo", M: "metal" });

const CHAIR_TAKEN = defineArt([
  "..CCCCCC..",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".CCCCCCCC.",
  ".cccccccc.",
  "....MM....",
  "..........",
  "..........",
  ".cccccccc.",
  ...CHAIR_LEGS,
], { C: "chair", c: "chairLo", M: "metal" });

// ── Monitor ──────────────────────────────────────────────────────────────────
// Only the case and an empty area. The content is drawn procedurally (`drawMonitor`), because
// seven kinds of image times four moods would eat half the budget as 28 pieces of art, and
// because the same strokes at a different length immediately look like a different tool.

const MONITOR = defineArt([
  "NNNNNNNNNNNNNNNN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NggggggggggggggN",
  "NNNNNNNNNNNNNNNN",
  "......MMMM......",
  "......MMMM......",
  "....MMMMMMMM....",
  "...MMMMMMMMMM...",
], { N: "screen", g: "screenLit", M: "metal" });

/** Inner area of the monitor, relative to the top left corner of the art. */
const MON_IN = { x: 1, y: 1, w: 14, h: 7 } as const;

// ── Runder Tisch ─────────────────────────────────────────────────────────────
// The ellipse is a table of half widths, not an `arc` (rule 2.1). The four upper rows are the
// top, the three lower ones the front apron, and that one change of colour turns a disc into a
// tabletop with thickness.

/** Waagerecht zentrierter Lauf in einer `w` breiten Zeile. */
function band(w: number, n: number, ch: string): string {
  const pad = (w - n) >> 1;
  return ".".repeat(pad) + ch.repeat(n) + ".".repeat(w - n - pad);
}

const TABLE_W = 52;
const OFFICE = defineArt([
  band(TABLE_W, 32, "D"),
  band(TABLE_W, 44, "D"),
  band(TABLE_W, 50, "D"),
  band(TABLE_W, 52, "D"),
  band(TABLE_W, 50, "d"),
  band(TABLE_W, 44, "d"),
  band(TABLE_W, 32, "d"),
  band(TABLE_W, 8, "M"),
  band(TABLE_W, 8, "M"),
  band(TABLE_W, 8, "M"),
  band(TABLE_W, 8, "M"),
  band(TABLE_W, 18, "M"),
  band(TABLE_W, 24, "M"),
], { D: "desk", d: "deskLo", M: "metal" });

// ── Stuhl am runden Tisch ────────────────────────────────────────────────────
// Narrower than the office chair (8 instead of 10) and without castors: the meeting chair
// stands, it does not roll. At 8 pixels the difference is small, but it separates "workplace"
// from "meeting", and that is exactly what the huddle should show.

const TABLE_CHAIR = defineArt([
  "..CCCC..",
  ".CCCCCC.",
  ".CCCCCC.",
  ".cccccc.",
  "........",
  "CCCCCCCC",
  "cccccccc",
  ".M....M.",
  ".M....M.",
  ".M....M.",
  ".M....M.",
  "M......M",
], { C: "chair", c: "chairLo", M: "metal" });

// ── Pflanzen ─────────────────────────────────────────────────────────────────
// Two sizes. The large one stands in corners and breaks the edge of the wall, the small one
// stands on cabinets and window sills. Both are deliberately asymmetric: a plant with an axis
// of symmetry reads as an ornament, not as a plant.

const PLANT_TALL = defineArt([
  "....G......",
  "..G.G.G....",
  ".GGGGGGG...",
  "GGGGGGGGGG.",
  ".gGGGGGGGGG",
  "..GGGGGGGg.",
  "GGGGGGGGGG.",
  ".gGGGGGGg..",
  "..GGGGGG...",
  "...gGGg....",
  "....GG.....",
  "....gG.....",
  "....GG.....",
  "....gG.....",
  "..OOOOOOO..",
  ".KKKKKKKKK.",
  ".KKKKKKKKK.",
  "..KKKKKKK..",
  "..KKKKKKK..",
  "..KKKKKKK..",
  "...KKKKK...",
  "...KKKKK...",
], { G: "plant", g: "plantLo", O: "soil", K: "clay" });

const PLANT_SMALL = defineArt([
  "..G.G.G..",
  ".GGGGGGG.",
  "GGGGGGGGG",
  ".GGGGGGG.",
  "..GgGgG..",
  "...GGG...",
  "...gG....",
  "..OOOOO..",
  ".KKKKKKK.",
  ".KKKKKKK.",
  "..KKKKK..",
  "..KKKKK..",
  "..KKKKK..",
], { G: "plant", g: "plantLo", O: "soil", K: "clay" });

// ── Aktenschrank ─────────────────────────────────────────────────────────────
// 16×15, hip high next to a character 24 pixels tall: three drawers with recessed handles, a
// light sheet metal body, small feet. It is pure furnishing and carries **no** meaning any
// more, which is exactly the point. As long as it was the deployment display, viewers looked
// for the server rack in a piece of furniture that looks like a drawer unit.
//
// It stays in the room anyway: the potted plant stands on it (which would otherwise stand on
// the floor and lie there like a forgotten flowerpot), and an office without a single piece of
// storage furniture reads as a furniture showroom, not as a workplace.

const FILE_CABINET = defineArt([
  "MMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMM",
  "cccccccccccccccc",
  "MMMMMMMMMMMMMMMM",
  "MMMMMIIIIIIMMMMM",
  "MMMMMMMMMMMMMMMM",
  "cccccccccccccccc",
  "MMMMMMMMMMMMMMMM",
  "MMMMMIIIIIIMMMMM",
  "MMMMMMMMMMMMMMMM",
  "cccccccccccccccc",
  "MMMMMMMMMMMMMMMM",
  "MMMMMIIIIIIMMMMM",
  "MMMMMMMMMMMMMMMM",
  ".c............c.",
], { M: "metal", c: "chairLo", I: "ink" });

// ── Serverschrank ────────────────────────────────────────────────────────────
// 20×34: **tall instead of wide**, a good one and a half character heights. The aspect ratio is
// the first and most important carrier of the message: a rack stands, a filing cabinet
// crouches. At 22×20 (the old version) the same piece of furniture inevitably read as a
// cupboard with drawers, and the three dark slots in it as handles.
//
// What makes it a rack, in the order the eye takes it in:
//
//   · **Two bright frame posts** (`metal`, 2 columns each on the outside) over the full height.
//     They frame a **dark** front (`chair`), the inverse of the old sheet metal box, and the
//     reason the silhouette stops looking like furniture from three metres away.
//   · **Rack units with their own faceplates**, separated by dark gaps (`chairLo`): a patch
//     panel on top (pairs of ports), three devices, a blanking plate at the bottom. Stacked
//     devices are what distinguishes a rack from a box.
//   · **Ventilation grilles** as an offset chequerboard of `ink` pixels (not as a solid bar): a
//     perforated surface cannot be mistaken for a handle.
//   · **A plinth and four feet** at the bottom, so it stands on the floor instead of floating.
//
// The `L` blocks (rows 7·8 / 13·14 / 19·20, columns 3..6) are the LED fields, one per device,
// 4×2 pixels. They are `ink` like the grille, so dark at rest, and are overwritten by
// `drawRack` as soon as a deployment runs.
//
// They sit on the **left** front edge, and that is measured, not decorated: the character that
// triggers the deployment stands to the right of the rack (`RACK_PX`), and its speech bubble is
// centred on its middle. On the right hand front the light field lay exactly under the bubble,
// so in the one moment somebody looks, the display was covered. On the left it is as far from
// the speaker as the rack is wide.

const RACK = defineArt([
  "MMMMMMMMMMMMMMMMMMMM",
  "MMNNNNNNNNNNNNNNNNMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCIICIICIICIICIICMM",
  "MMCIICIICIICIICIICMM",
  "MMccccccccccccccccMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCLLLLCICICICICICMM",
  "MMCLLLLCCICICICICCMM",
  "MMCCCCCCICICICICICMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMccccccccccccccccMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCLLLLCICICICICICMM",
  "MMCLLLLCCICICICICCMM",
  "MMCCCCCCICICICICICMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMccccccccccccccccMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCLLLLCICICICICICMM",
  "MMCLLLLCCICICICICCMM",
  "MMCCCCCCICICICICICMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMccccccccccccccccMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMCCCCCCCCCCCCCCCCMM",
  "MMccccccccccccccccMM",
  "MMMMMMMMMMMMMMMMMMMM",
  ".NNNNNNNNNNNNNNNNNN.",
  ".NNNNNNNNNNNNNNNNNN.",
  ".MM..............MM.",
  ".MM..............MM.",
], { M: "metal", C: "chair", c: "chairLo", N: "screen", I: "ink", L: "ink" });

// ── Kaffeeecke ───────────────────────────────────────────────────────────────
// A machine on a small counter, with a cup in the niche. That is the place a character goes to
// after `IDLE_COFFEE_MS`, the only sign of life in a long chain of tools, and therefore one
// has to see at a glance what it is.

const COFFEE = defineArt([
  "....MMMMMMMM....",
  "...MMMMMMMMMM...",
  "...MIIIIIIIIM...",
  "...MIIIIIIIIM...",
  "...MMMMMMMMMM...",
  "...MMMMMMMMMM...",
  "...MMMMMMMMMM...",
  "...MMM....MMM...",
  "...MMM.KK.MMM...",
  "...MMMMMMMMMM...",
  "..MMMMMMMMMMMM..",
  "DDDDDDDDDDDDDDDD",
  "dddddddddddddddd",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".dddddddddddddd.",
  ".d............d.",
], { M: "metal", I: "ink", K: "clay", D: "desk", d: "deskLo" });

// ── Door ─────────────────────────────────────────────────────────────────────
// Two versions. The open one shows a dark corridor and the swung in leaf: the runs come and go
// through it. Without the dark passage an open door reads as a hole in the wall.


const DOOR_SHUT = defineArt([
  "dddddddddddddddddddd",
  "dddddddddddddddddddd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDddddddddddddDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDddddddddddddDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDddddddddddddDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdMMDDDDDDDDdDDdd",
  "ddDDdMMDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDdDDDDDDDDDDdDDdd",
  "ddDDddddddddddddDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "ddDDDDDDDDDDDDDDDDdd",
  "dddddddddddddddddddd",
], { d: "deskLo", D: "desk", M: "metal" });

const DOOR_OPEN = defineArt([
  "dddddddddddddddddddd",
  "dddddddddddddddddddd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddMMDDIIIIIIIIIIIIdd",
  "ddMMDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "ddDDDDIIIIIIIIIIIIdd",
  "dddddddddddddddddddd",
], { d: "deskLo", D: "desk", M: "metal", I: "shadow" });

// ── Fenster ──────────────────────────────────────────────────────────────────
// Two panes per element, a transom in the middle. The diagonal gleam (`G`) sits the same in
// both panes: it is a reflection of the room light, not a cloud. At night it carries the
// ceiling light, by day the sky.

const WINDOW = defineArt([
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOGGOOMMOOOOOOOOGGOOMM",
  "MMOOOOOOOGGOOOMMOOOOOOOGGOOOMM",
  "MMOOOOOOGGOOOOMMOOOOOOGGOOOOMM",
  "MMOOOOOGGOOOOOMMOOOOOGGOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMOOOOOOOOOOOOMMOOOOOOOOOOOOMM",
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "wwwwwwwwwwwwwwwwwwwwwwwwwwwwww",
], { M: "metal", O: "out", G: "glass", w: "wallHi" });

// ── Whiteboard ───────────────────────────────────────────────────────────────
// Two boxes with an arrow between them and three lines of text. The content is deliberately
// fixed: a board whose sketch changes would be movement without an event, and therefore a
// detail a viewer takes for a statement about the run that it is not.

const BOARD = defineArt([
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWIIIIIIIIIIWWWWWWIIIIIIIIIIWWWM",
  "MWWWIWWWWWWWWIWWWWWWIWWWWWWWWIWWWM",
  "MWWWIWWWWWWWWIWWWWWWIWWWWWWWWIWWWM",
  "MWWWIWWWWWWWWIIIIIIIIWWWWWWWWIWWWM",
  "MWWWIWWWWWWWWIWWWWWWIWWWWWWWWIWWWM",
  "MWWWIIIIIIIIIIWWWWWWIIIIIIIIIIWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWIIIIIIIIIIIIIIIIIIWWWWWWWWWWWM",
  "MWWWIIIIIIIIIIIIIWWWWWWWWWWWWWWWWM",
  "MWWWIIIIIIIIIIIIIIIIIIIIIIWWWWWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWM",
  "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
  ".MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM.",
  "..MM..........................MM..",
], { M: "metal", W: "paper", I: "ink" });

// ── Wanduhr ──────────────────────────────────────────────────────────────────
// The hands stand **still**. Setting them from the clock would be forbidden in layer 1
// (rule 3.1), and worse: on rewind the same second of a run would show a different time every
// time. The clock is furnishing, not a display.

const CLOCK = defineArt([
  "...MMM...",
  ".MMWWWMM.",
  ".MWWWWWM.",
  "MWWIWIWWM",
  "MWWWIWWWM",
  "MWWWWWWWM",
  ".MWWWWWM.",
  ".MMWWWMM.",
  "...MMM...",
], { M: "metal", W: "paper", I: "ink" });

// ═══ Table of dimensions ═════════════════════════════════════════════════════

/** The dimensions of all furniture in buffer pixels, derived and not typed. Whoever builds the
 *  room computes distances with it instead of copying numbers from this header comment, which
 *  would go silently wrong at the next rebuild of a sprite. */
/** The same art in the fine grid. `SIZE` below stays on the coarse one: the scene computes its
 *  geometry with that, and that is in art units. Whoever doubles `SIZE` here
 *  verschiebt jeden Sitzplatz. */
const DESK_HD = doubled(DESK);
const CHAIR_FREE_HD = doubled(CHAIR_FREE);
const CHAIR_TAKEN_HD = doubled(CHAIR_TAKEN);

export const SIZE = {
  desk: { w: artW(DESK), h: artH(DESK) },
  chair: { w: artW(CHAIR_FREE), h: artH(CHAIR_FREE) },
  monitor: { w: artW(MONITOR), h: artH(MONITOR) },
  office: { w: artW(OFFICE), h: artH(OFFICE) },
  tableChair: { w: artW(TABLE_CHAIR), h: artH(TABLE_CHAIR) },
  plantTall: { w: artW(PLANT_TALL), h: artH(PLANT_TALL) },
  plantSmall: { w: artW(PLANT_SMALL), h: artH(PLANT_SMALL) },
  cabinet: { w: artW(FILE_CABINET), h: artH(FILE_CABINET) },
  rack: { w: artW(RACK), h: artH(RACK) },
  coffee: { w: artW(COFFEE), h: artH(COFFEE) },
  door: { w: artW(DOOR_SHUT), h: artH(DOOR_SHUT) },
  window: { w: artW(WINDOW), h: artH(WINDOW) },
  board: { w: artW(BOARD), h: artH(BOARD) },
  clock: { w: artW(CLOCK), h: artH(CLOCK) },
} as const;

// ═══ Zeichnen ════════════════════════════════════════════════════════════════
//
// Every world drawing function carries `(ctx, cx, yBase, pal, …)`: horizontal centre plus the
// **lowest** contact row. The scene sorts by `yBase` (painter's algorithm); an object that
// passes its top edge sorts wrongly and disappears behind whatever it stands in front of.
//
// The palette comes fourth instead of in the options, because every one of these functions
// needs it and a call without it could draw nothing at all.

/** A soft contact shadow under a piece of furniture. No `shadowBlur` (forbidden and mush at
 *  this resolution anyway): three flat rectangles, paler outwards. Without it every piece
 *  floats a hair above the floor, and the effect is tiny and noticed at once. */
function contactShadow(ctx: Ctx, pal: Pal, cx: number, yBase: number, w: number): void {
  const h = w >> 1;
  fillA(ctx, pal, "shadow", 0.20, cx - (w >> 1), yBase - 1, w, 1);
  fillA(ctx, pal, "shadow", 0.12, cx - (w >> 1) - 1, yBase, w + 2, 1);
  fillA(ctx, pal, "shadow", 0.06, cx - (h >> 1) - h, yBase + 1, h * 2, 1);
}

/** The same shadow in the fine grid: five steps instead of three, because a step is half as
 *  high. It narrows towards the back, because the light comes from the windows, so from above. */
function contactShadowHD(ctx: Ctx, pal: Pal, cx: number, yBase: number, w: number): void {
  const half = w >> 1;
  fillA(ctx, pal, "shadow", 0.24, cx - half + 4, yBase - 3, w - 8, 1);
  fillA(ctx, pal, "shadow", 0.20, cx - half + 1, yBase - 2, w - 2, 2);
  fillA(ctx, pal, "shadow", 0.13, cx - half - 1, yBase, w + 2, 2);
  fillA(ctx, pal, "shadow", 0.07, cx - half + 2, yBase + 2, w - 4, 1);
  fillA(ctx, pal, "shadow", 0.04, cx - half + 8, yBase + 3, w - 16, 1);
}

export function drawDesk(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  // Finely drawn (stage 4). `cx`/`yBase` stay art units: the scene still places furniture in
  // its own grid, only the drawing is twice as fine.
  const X = cx * HD, Y = yBase * HD;
  const w = SIZE.desk.w * HD, h = SIZE.desk.h * HD;
  const x0 = X - (w >> 1);
  contactShadowHD(ctx, pal, X, Y, w);
  drawArt(ctx, DESK_HD, X, Y, pal);
  // Front edge: a HAIRLINE of light with a line of shadow below it. Two buffer pixels thick
  // made the edge a bar; only a line with its own shadow reads as the rim of a top you could
  // touch.
  fillA(ctx, pal, "wallHi", 0.22, x0, Y - h, w, 1);
  fillA(ctx, pal, "shadow", 0.16, x0, Y - h + 1, w, 1);
  // The modesty panel lies in the shadow of the top. Without that grip it has almost exactly
  // the tone of the plank floor and vanishes into it, and the desk then looks like a board on
  // two wires.
  fillA(ctx, pal, "shadow", 0.30, x0 + 6, Y - h + 6, w - 12, 10);
  // And a hint of light on the upper side to the left, where the windows are.
  fillA(ctx, pal, "wallHi", 0.10, x0 + 2, Y - h + 2, (w >> 1) - 4, 3);
}

export interface ChairOpts {
  /** Somebody is sitting on it: the seat stays free, the character fills it. */
  occupied?: boolean;
  /** Facing left. */
  flip?: boolean;
}

export function drawChair(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: ChairOpts): void {
  const art = opts?.occupied === true ? CHAIR_TAKEN_HD : CHAIR_FREE_HD;
  const X = cx * HD, Y = yBase * HD;
  const w = SIZE.chair.w * HD, h = SIZE.chair.h * HD;
  contactShadowHD(ctx, pal, X, Y, w - 4);
  drawArt(ctx, art, X, Y, pal, { flip: opts?.flip });
  // A light edge on the top of the backrest: it separates the backrest from the seat behind it,
  // which has the same tone, otherwise the chair is a blob.
  fillA(ctx, pal, "wallHi", 0.20, X - (w >> 1) + 4, Y - h, w - 8, 1);
}

// ── Monitor: kind of image and mood ──────────────────────────────────────────

/** What is on the screen. Seven images for forty tools, derived from `ToolAct` and not from
 *  the tool name. */
export type ScreenKind = "code" | "log" | "page" | "search" | "link" | "wait" | "blank";

/** How the run is doing right now. Colours **the glow, the ink of one line and one pixel of the
 *  frame**, not the area. A panel flooded red reads as "broken monitor", not as "failed step",
 *  and at twelve seats it drowns out everything else in the picture. */
export type Mood = "work" | "wait" | "error" | "done";

const MOOD_COLOR: Record<Mood, PalKey> = {
  work: "acc", wait: "blocked", error: "err", done: "ok",
};

/** Line pattern per kind of image: `[row, indent, length]` inside the 14×7 inner area.
 *  There is no row 7: whoever enters it paints into the frame. */
const SCREEN_LINES: Record<ScreenKind, readonly (readonly [number, number, number])[]> = {
  // Editor: levels of indentation are what makes source code recognisable at 14 pixels at all.
  code: [[0, 0, 6], [1, 2, 7], [2, 2, 4], [3, 4, 8], [4, 2, 5], [5, 4, 6], [6, 0, 7]],
  // Log: left aligned, dense, of unequal length.
  log: [[0, 0, 12], [1, 0, 9], [2, 0, 13], [3, 0, 7], [4, 0, 11], [5, 0, 13], [6, 0, 8]],
  // Document or page: a heading and running text, with a margin.
  page: [[1, 2, 6], [3, 2, 10], [4, 2, 10], [5, 2, 7]],
  // Suche: Eingabezeile oben, darunter Treffer.
  search: [[0, 1, 12], [2, 1, 9], [3, 1, 11], [4, 1, 6], [5, 1, 10]],
  // Handover: two boxes, the connection is painted by `drawScreenBody` in the mood colour.
  link: [[1, 1, 3], [2, 1, 3], [3, 1, 3], [1, 10, 3], [2, 10, 3], [3, 10, 3]],
  // Waiting: only the bar at the bottom; the dots come in the mood colour.
  wait: [[6, 1, 12]],
  blank: [],
};

function drawScreenBody(
  ctx: Ctx, pal: Pal, x: number, y: number, kind: ScreenKind, mood: PalKey,
): void {
  if (kind === "blank") {
    // Dark panel: the seat is taken, but nobody is doing anything right now.
    fill(ctx, pal, "screen", x, y, MON_IN.w, MON_IN.h);
    return;
  }
  if (kind === "page") {
    // A document is paper, not a glowing editor, which is the only case where the area gets a
    // different base colour.
    fill(ctx, pal, "paper", x, y, MON_IN.w, MON_IN.h);
  }
  if (kind === "code") {
    // Zeilennummernspalte.
    fill(ctx, pal, "screen", x, y, 2, MON_IN.h);
  }

  // The editor additionally indents by the number column. Clamping happens **after** that
  // offset: otherwise the longest line of code runs one pixel past the inner area and paints
  // into the frame, and at twelve monitors that is exactly what gets noticed.
  const gutter = kind === "code" ? 3 : 0;
  for (const [row, indent, len] of SCREEN_LINES[kind]) {
    const sx = indent + gutter;
    fill(ctx, pal, "ink", x + sx, y + row, Math.min(len, MON_IN.w - sx), 1);
  }

  // Exactly one element carries the mood, never the area.
  if (kind === "code") fill(ctx, pal, mood, x + 3, y + 3, 8, 1);
  else if (kind === "log") fill(ctx, pal, mood, x, y + 2, 2, 1);
  else if (kind === "search") fill(ctx, pal, mood, x + 12, y, 1, 1);
  else if (kind === "link") fill(ctx, pal, mood, x + 4, y + 2, 6, 1);
  else if (kind === "page") fill(ctx, pal, mood, x + 2, y + 1, 6, 1);
  else if (kind === "wait") {
    fill(ctx, pal, mood, x + 4, y + 3, 2, 1);
    fill(ctx, pal, mood, x + 7, y + 3, 2, 1);
    fill(ctx, pal, mood, x + 10, y + 3, 2, 1);
  }
}

export interface MonitorOpts {
  screen?: ScreenKind;
  mood?: Mood;
  /** Facing direction of the seat: the glow then falls to the other side. */
  flip?: boolean;
}

/**
 * A monitor with content. `yBase` is the lower edge of the foot, so the desktop, **not** the
 * floor.
 */
export function drawMonitor(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: MonitorOpts): void {
  const kind = opts?.screen ?? "blank";
  const mood = MOOD_COLOR[opts?.mood ?? "work"];
  const x0 = artLeft(MONITOR, cx);
  const y0 = yBase - SIZE.monitor.h;

  // The glow lies **behind** the device and is painted over by the case: two rings, weaker
  // outwards. A gradient would be the obvious thing here and is forbidden (rule 2.1), and two
  // stepped rectangles look better at 480×270 than a soft circle anyway.
  if (kind !== "blank") {
    fillA(ctx, pal, mood, 0.06, x0 - 5, y0 - 4, SIZE.monitor.w + 10, 17);
    fillA(ctx, pal, mood, 0.10, x0 - 2, y0 - 2, SIZE.monitor.w + 4, 13);
  }

  drawArt(ctx, MONITOR, cx, yBase, pal, { flip: opts?.flip });
  drawScreenBody(ctx, pal, x0 + MON_IN.x, y0 + MON_IN.y, kind, mood);

  // The one frame pixel: the power light at the bottom right.
  fill(ctx, pal, mood, x0 + 13, y0 + 8, 1, 1);
}

export function drawMeetingTable(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.office.w - 8);
  drawArt(ctx, OFFICE, cx, yBase, pal);
  fillA(ctx, pal, "wallHi", 0.16, cx - 14, yBase - SIZE.office.h + 1, 28, 1);
}

export function drawTableChair(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: { flip?: boolean },
): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.tableChair.w);
  drawArt(ctx, TABLE_CHAIR, cx, yBase, pal, { flip: opts?.flip });
}

export function drawPlant(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: { small?: boolean; flip?: boolean },
): void {
  const small = opts?.small === true;
  const art = small ? PLANT_SMALL : PLANT_TALL;
  contactShadow(ctx, pal, cx, yBase, small ? 7 : 9);
  drawArt(ctx, art, cx, yBase, pal, { flip: opts?.flip });
}

/** The filing cabinet. Furnishing, nothing else: the deployment display is `drawRack`. */
export function drawCabinet(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.cabinet.w);
  drawArt(ctx, FILE_CABINET, cx, yBase, pal);
  fillA(ctx, pal, "wallHi", 0.20, artLeft(FILE_CABINET, cx), yBase - SIZE.cabinet.h,
    SIZE.cabinet.w, 1);
}

// ── The server rack as the deployment display ────────────────────────────────

/** The `L` fields of the `RACK` art in art coordinates: one 4×2 field per rack unit. Derived
 *  from the sprite above, so whoever inserts a row there has to move these numbers along, which
 *  is why they stand next to the drawing function and not in `const.ts`. Index 0 is the
 *  **topmost** device.
 *
 *  Four pixels wide and two high instead of the former 8×1 bar: a bar across half the front
 *  width was exactly what made the rack look like a drawer. A compact block next to the
 *  ventilation grille reads as the status light of the device in whose faceplate it sits, and
 *  eight lit pixels it remains in both versions. */
const LED_ROWS: readonly number[] = [7, 13, 19];
const LED_X = 3;
const LED_W = 4;
const LED_H = 2;

/** One step of the rising bar. Three steps make a cycle of 1.26 s, which reads as "something is
 *  working here" without flickering. */
const LED_STEP_MS = 420;

/** The server rack as a `Frame` describes it. `t - since` is the phase; there is no counter
 *  (PIXEL-CONTRACT.md 3.4). */
export interface RackOpts {
  state: RackState;
  since: number;
  t: number;
}

/** Which colour an LED field carries. No new palette colour needed: `lamp`, `ok`, `err` and
 *  `blocked` are the same four that bubble edges and dock tiles already use, so rack and
 *  timeline never contradict each other.
 *
 *  `back` is the reason there are four states and not three: `blocked` on top (failed), `ok`
 *  below (rolled back, the service runs again). Merged with `fail` exactly the good half of
 *  that message would be lost. */
function ledKey(state: RackState, row: number): PalKey {
  if (state === "start") return "lamp";
  if (state === "ok") return "ok";
  if (state === "fail") return "err";
  return row === 0 ? "blocked" : "ok";
}

/**
 * The server rack. Without `rack` (or on `idle`) it is quiet scenery: a rack in which nothing
 * gerade nichts leuchtet.
 *
 * **The construction rule the golden ops hashes hang on**: the LED block is entered only when
 * a deployment really lights up. On `idle` exactly the same three drawing calls happen in the
 * same order as without `rack`, otherwise each of the 16 golden images would depend on the
 * state of the rack and the intent of a bless diff would vanish in the
 * Rauschen.
 */
export function drawRack(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, rack?: RackOpts,
): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.rack.w);
  drawArt(ctx, RACK, cx, yBase, pal);
  fillA(ctx, pal, "wallHi", 0.20, artLeft(RACK, cx), yBase - SIZE.rack.h, SIZE.rack.w, 1);

  if (rack === undefined || rack.state === "idle") return;

  const xLeft = artLeft(RACK, cx);
  const x0 = xLeft + LED_X;
  const yTop = yBase - SIZE.rack.h;
  // The rising bar: one step per `LED_STEP_MS`, from bottom to top, then from the start. The
  // phase comes from `t - since`, so after a jump in the timeline it stands right immediately
  // instead of counting itself up frame by frame.
  const level = Math.floor(Math.max(0, rack.t - rack.since) / LED_STEP_MS);
  const an = rack.state === "start" ? 1 + (level % LED_ROWS.length) : LED_ROWS.length;

  // A faint wash of light across the whole front, in the colour of the **lowest** lit device:
  // from two metres away at 480×270 one sees first *that* the rack is alive, and only then
  // which rows. The colour is that of the state, not of the row: on `back` the topmost
  // (`blocked`) would be the wrong message for the surface.
  const area = ledKey(rack.state, LED_ROWS.length - 1);
  fillA(ctx, pal, area, 0.05, xLeft + 2, yTop + 2, SIZE.rack.w - 4, SIZE.rack.h - 6);

  for (let i = 0; i < LED_ROWS.length; i++) {
    if (LED_ROWS.length - i > an) continue;
    const key = ledKey(rack.state, i);
    const y = yTop + LED_ROWS[i];
    // Stray light first, then the LED itself: the other way round the pale veil would lie over
    // the light field and take exactly the colour that matters. Without the glow eight pixels
    // in 480×270 simply cannot be seen from two metres.
    fillA(ctx, pal, key, 0.30, x0 - 2, y - 2, LED_W + 4, LED_H + 4);
    fillA(ctx, pal, key, 0.55, x0 - 1, y - 1, LED_W + 2, LED_H + 2);
    fill(ctx, pal, key, x0, y, LED_W, LED_H);
  }
}

export function drawCoffee(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.coffee.w);
  drawArt(ctx, COFFEE, cx, yBase, pal);
}

/** The door. `yBase` is the threshold, so the floor line at the wall (`WALL_H`). */
export function drawDoor(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: { open?: boolean }): void {
  const open = opts?.open === true;
  drawArt(ctx, open ? DOOR_OPEN : DOOR_SHUT, cx, yBase, pal);
  if (open) {
    // A strip of light from the corridor onto the floor in front of it: it turns the passage
    // into a passage instead of a black rectangle.
    fillA(ctx, pal, "lamp", 0.12, cx - 4, yBase, 14, 2);
    fillA(ctx, pal, "lamp", 0.07, cx - 6, yBase + 2, 18, 2);
  }
}

/** A window element. `yBase` is the lower edge of the sill. Place several elements at distance
 *  `WINDOW_STEP` and two windows share the post. */
export function drawWindow(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  drawArt(ctx, WINDOW, cx, yBase, pal);
}

export function drawBoard(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  drawArt(ctx, BOARD, cx, yBase, pal);
}

export function drawClock(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  drawArt(ctx, CLOCK, cx, yBase, pal);
}

// ═══ Kulisse (prozedural) ════════════════════════════════════════════════════

const SALT_PLANK = 0x4449454c;  // "DIEL"
const SALT_SHADE = 0x53434841;  // "SCHA"

/** Length of a floorboard and height of a row of boards.
 *
 *  The ratio of the lengths is the whole secret: at 46×7 (the first attempt) the floor looks
 *  like a brick wall, because a board is then only six times as long as it is high. Real boards
 *  are 15:1 and above, hence 92×6. */
const PLANK_W = 92;
const PLANK_H = 10;
/** Minimum offset of two neighbouring rows. Without this bound the joints occasionally fall
 *  under one another and the floor gets a continuous seam, which a laid floor never has, and
 *  the eye sees the row at once. */
const MIN_STAGGER = 26;

/**
 * The back wall: surface, ceiling edge, a slight fall towards the bottom, skirting board.
 *
 * The fall is three stepped bands instead of a gradient (rule 2.1): at 38 rows of height the
 * difference cannot be seen anyway, and bands stay checkable against golden images.
 */
export function drawWall(ctx: Ctx, pal: Pal): void {
  // Finely drawn (stage 3): `ctx` is the HD view and the computation is in buffer pixels.
  // What that gives shows in the joints: one art unit wide they were bars, one
  // HD unit wide they are lines. The same wall, half as coarse.
  const W = ART.w * HD, H = WALL_H * HD;
  fill(ctx, pal, "wall", 0, 0, W, H);
  fill(ctx, pal, "wallHi", 0, 0, W, 3);
  // The fall towards the bottom in six steps instead of three bands: in the fine grid every
  // step is half as high, and stripes become a gradient one no longer reads as steps.
  for (let i = 0; i < 6; i++) {
    fillA(ctx, pal, "wallLo", 0.06 + i * 0.05, 0, H - 26 + i * 4, W, 4);
  }
  // Skirting board: shadow gap, board, bright top edge. Three rows that put the wall onto the
  // floor instead of butting it against it.
  fillA(ctx, pal, "wallLo", 0.55, 0, H - 9, W, 1);
  fill(ctx, pal, "wallLo", 0, H - 8, W, 8);
  fillA(ctx, pal, "wallHi", 0.30, 0, H - 8, W, 1);
  fillA(ctx, pal, "shadow", 0.18, 0, H - 2, W, 2);
  // Panel joints of the wall cladding: a HAIRLINE every 80 art units with a bright edge next to
  // it, so it reads as the joint of two panels instead of a scratch.
  for (let x = 40 * HD; x < W; x += 80 * HD) {
    fillA(ctx, pal, "wallLo", 0.28, x, 3, 1, H - 12);
    fillA(ctx, pal, "wallHi", 0.16, x + 1, 3, 1, H - 12);
  }
}

/**
 * The plank floor. Rows of `PLANK_H`, joints every `PLANK_W`, offset per row with an enforced
 * minimum offset. Every board gets one of three tints from its hash, because the same area in
 * one colour looks like linoleum.
 */
export function drawFloor(ctx: Ctx, pal: Pal): void {
  // Finely drawn (stage 3), in buffer pixels. The floor is seventy percent of the image and
  // decides whether the room looks like planks or like masonry. In the coarse grid both were
  // equally wide: joint and grain were two buffer pixels thick, like the board itself.
  const W = ART.w * HD, H = ART.h * HD, TOP = WALL_H * HD;
  const PW = PLANK_W * HD, PH = PLANK_H * HD;
  fill(ctx, pal, "floor", 0, TOP, W, H - TOP);

  let prev = -1000;
  let r = 0;
  for (let y = TOP; y < H; y += PH, r++) {
    const rowH = Math.min(PH, H - y);
    const raw = (mix(r, SALT_PLANK) % PLANK_W) * HD;
    let off = raw;
    if (prev >= 0) {
      const d = Math.abs(off - prev);
      // The distance is cyclic: 1 and 45 lie next to each other at `PLANK_W = 46`.
      if (Math.min(d, PW - d) < MIN_STAGGER * HD) {
        off = (prev + MIN_STAGGER * HD + (raw % (PW - 2 * MIN_STAGGER * HD))) % PW;
      }
    }
    prev = off;

    // Board tint, grain and vertical joints in one pass.
    let p = 0;
    for (let x = off - PW; x < W; x += PW, p++) {
      const v = mix(r * 131 + p, SALT_SHADE) % 4;
      // Faint: a board should stand out from its neighbour, not clash with it.
      if (v === 1) fillA(ctx, pal, "floorHi", 0.11, x, y, PW, rowH - 1);
      else if (v === 2) fillA(ctx, pal, "floorLo", 0.09, x, y, PW, rowH - 1);
      // Two grain lines per board instead of one, both only one buffer pixel high: the single
      // thick line read as a joint in the middle of the board.
      else if (v === 3) {
        fillA(ctx, pal, "floorLo", 0.10, x + 12, y + 4, PW - 36, 1);
        fillA(ctx, pal, "floorLo", 0.07, x + 24, y + rowH - 6, PW - 60, 1);
      }
      // The joint: a hairline plus a bright edge right next to it. Together that reads as the
      // edge of two boards; the bare dark line read as a mortar joint.
      if (x >= 0) {
        fillA(ctx, pal, "floorLo", 0.60, x, y, 1, rowH - 1);
        fillA(ctx, pal, "floorHi", 0.18, x + 1, y, 1, rowH - 1);
      }
    }
    // Horizontal joint at the lower edge of the row, clearly fainter than the vertical one,
    // otherwise the row wins visually over the board and the floor tips into masonry.
    if (rowH === PH) fillA(ctx, pal, "floorLo", 0.20, 0, y + rowH - 1, W, 1);
  }

  // A band of shadow under the wall: the floor catches no grazing light there. Four rows are
  // enough to seat the wall on the floor instead of gluing it against it.
  for (let i = 0; i < 7; i++) {
    fillA(ctx, pal, "shadow", 0.20 - i * 0.026, 0, TOP + i, W, 1);
  }
}

/**
 * Fields of light on the floor, what makes a room look lit instead of painted.
 *
 * In front of every window lies a trapezoid that grows wider and weaker towards the bottom:
 * light falls at an angle, so it spreads towards the viewer. A rectangle would not do, it
 * would read as a bright carpet, not as sunlight.
 *
 * At night the fill light is off (it is dark outside) and instead a very faint cold shimmer
 * lies there: a city outside the window throws light, only little of it.
 */
export function drawLight(ctx: Ctx, pal: Pal, xs: readonly number[], day: boolean): void {
  const TOP = WALL_H * HD;
  const levels = 14;
  for (const cx0 of xs) {
    const cx = cx0 * HD;
    for (let i = 0; i < levels; i++) {
      const half = 9 * HD + i * 3;
      const a = (day ? 0.085 : 0.05) * (1 - i / levels);
      fillA(ctx, pal, day ? "floorHi" : "glass", a, cx - half, TOP + i * 4, half * 2, 4);
    }
  }
}

/**
 * Carpet. `cx`/`yBase` are the centre and the **front edge**: the carpet keeps to the sorting
 * rule as well although it lies flat, because it is drawn in the background pass, but its front
 * edge is the point where a character standing in front of it has to cover it.
 */
export function drawRug(ctx: Ctx, cx: number, yBase: number, w: number, h: number, pal: Pal): void {
  const x = cx - (w >> 1);
  const y = yBase - h;
  fill(ctx, pal, "rug", x, y, w, h);
  // A border all round with a second stripe inside: two lines are enough for a rectangle to
  // read as a carpet and not as a patch of colour.
  fill(ctx, pal, "rugLo", x, y, w, 1);
  fill(ctx, pal, "rugLo", x, y + h - 1, w, 1);
  fill(ctx, pal, "rugLo", x, y, 1, h);
  fill(ctx, pal, "rugLo", x + w - 1, y, 1, h);
  fill(ctx, pal, "rugLo", x + 3, y + 3, w - 6, 1);
  fill(ctx, pal, "rugLo", x + 3, y + h - 4, w - 6, 1);
  fill(ctx, pal, "rugLo", x + 3, y + 3, 1, h - 6);
  fill(ctx, pal, "rugLo", x + w - 4, y + 3, 1, h - 6);
  // The front edge catches light, the rest lies in the shadow of the room.
  fillA(ctx, pal, "wallHi", 0.10, x, y + h - 1, w, 1);
}

/**
 * The carpet of light under a window. `cx` is the centre of the window, `yBase` the front end
 * of the patch of light; `w` is the width at the wall, `h` the depth.
 *
 * In perspective a window light is a trapezoid: it grows wider and paler towards the viewer.
 * Both happen here row by row, one rectangle per floor row with its own width
 * and its own opacity. The gap in the middle is the shadow of the transom; without it the
 * patch looks like a lamp, not like a window.
 *
 * Belongs to the day picture. At night there is no sun outside, and the scene does not draw it.
 */
export function drawWindowLight(
  ctx: Ctx, cx: number, yBase: number, w: number, h: number, pal: Pal,
): void {
  const y0 = yBase - h;
  for (let i = 0; i < h; i++) {
    const t = i / h;
    const rowW = w + Math.round(t * w * 0.55);
    const alpha = 0.13 * (1 - t) + 0.02;
    // The transom shadow fades towards the front: a gap drawn through to the end looks like a
    // drawing error, not like a shadow.
    const gap = t > 0.62 ? 0 : 2 + Math.round(t * 3);
    const x = cx - (rowW >> 1);
    const half = (rowW - gap) >> 1;
    fillA(ctx, pal, "lamp", alpha, x, y0 + i, half, 1);
    fillA(ctx, pal, "lamp", alpha, x + half + gap, y0 + i, rowW - half - gap, 1);
  }
}
