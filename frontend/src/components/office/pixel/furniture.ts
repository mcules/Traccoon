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
import { ART, PARTITIONS, WALLS } from "../const.ts";
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

// **The desk is a surface, not a table.** It used to be drawn from the front: a top, a front
// edge, two legs, and a monitor floating above it. In a room seen from straight above that is
// the wrong projection, and it is the reason the office read as a stage set rather than as a
// floor plan. Now the desk is what one actually sees from above: the desktop, the thickness of
// the top as two darker rows at the near edge, and **on** it a keyboard, a mouse and a screen
// at the far edge. That single change does more for the look than any colour.
//
// Everything in this file carries a **contour** (`L` = `line`). That is the single change that
// turns a set of coloured blocks into a drawn room: a silhouette holds against any floor
// underneath it, and twelve objects with the same contour read as one drawing instead of as
// twelve pasted clip arts. The contour is drawn into the art by hand and not painted around it
// afterwards, because an outline pass would be four extra draws per object and would put a line
// where the object touches the ground, exactly where there must be none.

/** The desk in art units. Written as numbers and built by a loop instead of as 14 hand counted
 *  strings: a desktop is a rectangle, and a rectangle whose width somebody has to count is the
 *  place where the next change puts a column out of line. */
const DESK_W = 34;
const DESK_H = 14;

function deskRows(): string[] {
  const inner = DESK_W - 2;
  const rows = ["L".repeat(DESK_W), "L" + "H".repeat(inner) + "L"];
  for (let i = 0; i < DESK_H - 5; i++) rows.push("L" + "D".repeat(inner) + "L");
  // Two darker rows at the near edge: that is the **thickness** of the top, the only thing that
  // says at what height the surface lies. Without them the desk is a sticker on the floor.
  rows.push("L" + "d".repeat(inner) + "L", "L" + "d".repeat(inner) + "L");
  rows.push("L".repeat(DESK_W));
  return rows;
}

const DESK = defineArt(deskRows(), { D: "desk", d: "deskLo", H: "wallHi", M: "metal", L: "line" });

// ── Office chair ─────────────────────────────────────────────────────────────
// Two versions. The occupied one leaves the seat free: the character sits there, and a chair
// shining through beneath them would look like a drawing error. The foot stays visible in both
// versions: it sticks out sideways under the character and is the detail that makes "sitting at
// the desk" recognisable at all.

// The chair from **above**, like the desk: a backrest at the far edge, the seat, the column and
// the star base. The person sitting on it covers the seat, so the occupied version leaves it
// out; what stays visible around them is exactly what one sees of a real office chair from
// above, the backrest behind the shoulders and the feet of the base beside the shoes.

const CHAIR_MAP = { C: "chair", c: "chairLo", M: "metal", L: "line" } as const;

// The office chair, taken from the reference: a **tall rounded backrest** with a lighter mesh
// panel, two armrest nubs at mid height, the seat, a column and a star base. That silhouette is
// what says "office" at a glance; a plain rounded box says "pouffe".
//
// It is 13 wide, so a little wider than a head (11): the armrests are exactly the part that may
// stick out past the person sitting on it, and they are what makes the chair readable when
// somebody is in it.

const CHAIR_BACKREST = [
  "...LLLLLLL...",
  "..LcccccccL..",
  ".LcCCCCCCCcL.",
  ".LcCMMMMMCcL.",
  "LLcCMMMMMCcLL",
  "LcccCMMMCcccL",
  "LcccccccccccL",
  ".LLcccccccLL.",
  "...LLLLLLL...",
];

const CHAIR_FOOT = [
  "....LLLLL....",
  "...LMMMMML...",
  "..LML...LML..",
  ".LL.......LL.",
];

const CHAIR_FREE = defineArt([
  ...CHAIR_BACKREST,
  "..LCCCCCCCL..",
  "..LCCCCCCCL..",
  "..LLLLLLLLL..",
  ...CHAIR_FOOT,
], CHAIR_MAP);

const CHAIR_TAKEN = defineArt([
  ...CHAIR_BACKREST,
  ".............",
  ".............",
  "..LLLLLLLLL..",
  ...CHAIR_FOOT,
], CHAIR_MAP);

// ── Monitor ──────────────────────────────────────────────────────────────────
// Only the case and an empty area. The content is drawn procedurally (`drawMonitor`), because
// seven kinds of image times four moods would eat half the budget as 28 pieces of art, and
// because the same strokes at a different length immediately look like a different tool.

const MONITOR = defineArt([
  "LLLLLLLLLLLLLLLL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LggggggggggggggL",
  "LLLLLLLLLLLLLLLL",
  "......LMML......",
  "......LMML......",
  "....LMMMMMML....",
  "..LMMMMMMMMMML..",
], { N: "screen", g: "screenLit", M: "metal", L: "line" });

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

/** A centred run of `inner` inside a centred run of `outer`: the outline ring of an ellipse
 *  row. Written as a helper because an ellipse with a contour is otherwise 13 hand counted
 *  strings, and a single miscount shifts the whole tabletop. */
function ring(w: number, outer: number, inner: number, chOut: string, chIn: string): string {
  const padO = (w - outer) >> 1;
  const left = (outer - inner) >> 1;
  return ".".repeat(padO) + chOut.repeat(left) + chIn.repeat(inner)
    + chOut.repeat(outer - inner - left) + ".".repeat(w - outer - padO);
}

const TABLE_W = 52;
const OFFICE = defineArt([
  band(TABLE_W, 32, "L"),
  ring(TABLE_W, 44, 40, "L", "H"),
  ring(TABLE_W, 50, 46, "L", "D"),
  ring(TABLE_W, 52, 48, "L", "D"),
  ring(TABLE_W, 50, 46, "L", "d"),
  ring(TABLE_W, 44, 40, "L", "d"),
  band(TABLE_W, 32, "L"),
  ring(TABLE_W, 8, 4, "L", "M"),
  ring(TABLE_W, 8, 4, "L", "M"),
  ring(TABLE_W, 8, 4, "L", "M"),
  ring(TABLE_W, 8, 4, "L", "M"),
  ring(TABLE_W, 18, 14, "L", "M"),
  band(TABLE_W, 24, "L"),
], { D: "desk", d: "deskLo", H: "wallHi", M: "metal", L: "line" });

// ── Stuhl am runden Tisch ────────────────────────────────────────────────────
// Narrower than the office chair (8 instead of 10) and without castors: the meeting chair
// stands, it does not roll. At 8 pixels the difference is small, but it separates "workplace"
// from "meeting", and that is exactly what the huddle should show.

const TABLE_CHAIR = defineArt([
  "..LLLL..",
  ".LCCCCL.",
  ".LCCCCL.",
  ".LccccL.",
  ".LLLLLL.",
  "LLLLLLLL",
  "LCCCCCCL",
  "LLLLLLLL",
  ".L....L.",
  ".L....L.",
  ".L....L.",
  "LL....LL",
], { C: "chair", c: "chairLo", M: "metal", L: "line" });

// ── Pflanzen ─────────────────────────────────────────────────────────────────
// Two sizes. The large one stands in corners and breaks the edge of the wall, the small one
// stands on cabinets and window sills. Both are deliberately asymmetric: a plant with an axis
// of symmetry reads as an ornament, not as a plant.

const PLANT_MAP = { G: "plant", g: "plantLo", O: "soil", K: "clay", L: "line" } as const;

const PLANT_TALL = defineArt([
  "....LLLL.......",
  "..LLGGGGLL.....",
  ".LGGGGGGGGL....",
  "LGGGGGGGGGGL...",
  "LGGGgGGGGGGGL..",
  "LGGGGGGGGGGGGL.",
  "LGGGgGGGGgGGGL.",
  ".LGGGGGGGGGGGL.",
  ".LGGGGGgGGGGGL.",
  "..LGGGGGGGGGL..",
  "..LLGGGGGGGL...",
  "....LGGGGL.....",
  ".....LGGL......",
  "......LGL......",
  "......LGL......",
  "....LLLLLLL....",
  "...LOOOOOOOL...",
  "...LKKKKKKKL...",
  "...LKKKKKKKL...",
  "....LKKKKKL....",
  "....LKKKKKL....",
  ".....LLLLL.....",
], PLANT_MAP);

const PLANT_SMALL = defineArt([
  "...LLL...",
  "..LGGGL..",
  ".LGGGGGL.",
  "LGGGgGGGL",
  "LGGGGGGGL",
  ".LGGgGGL.",
  "..LGGGL..",
  "..LLLLL..",
  ".LOOOOOL.",
  ".LKKKKKL.",
  ".LKKKKKL.",
  "..LKKKL..",
  "..LLLL...",
], PLANT_MAP);

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

export interface DeskOpts {
  /** Which way the person sitting at this half of the bench is: `-1` left, `+1` right. The
   *  keyboard belongs in front of **them**, not in the middle of the desk. */
  toward?: number;
}

export function drawDesk(ctx: Ctx, cx: number, yBase: number, pal: Pal, o?: DeskOpts): void {
  // Finely drawn (stage 4). `cx`/`yBase` stay art units: the scene still places furniture in
  // its own grid, only the drawing is twice as fine.
  const X = cx * HD, Y = yBase * HD;
  const w = SIZE.desk.w * HD, h = SIZE.desk.h * HD;
  const x0 = X - (w >> 1);
  contactShadowHD(ctx, pal, X, Y, w);
  drawArt(ctx, DESK_HD, X, Y, pal);

  // A hint of light on the top towards the windows, so the surface is not a flat area.
  fillA(ctx, pal, "wallHi", 0.12, x0 + 2, Y - h + 3, (w >> 1) - 4, 6);

  // What lies **on** the desk. This is the part that makes a surface read as a workplace: an
  // empty rectangle is a table, a rectangle with a keyboard on it is a desk. All of it is
  // measured from the near edge, because that is where a person reaches.
  const seat = X + (o?.toward ?? 0) * 12;
  const kbW = 30, kbH = 8;
  const kbX = seat - (kbW >> 1), kbY = Y - 13;
  fill(ctx, pal, "line", kbX, kbY, kbW, kbH);
  fill(ctx, pal, "metal", kbX + 1, kbY + 1, kbW - 2, kbH - 2);
  fillA(ctx, pal, "wallHi", 0.40, kbX + 1, kbY + 1, kbW - 2, 1);
  // Three rows of keys as hairlines. More would be noise at this size, fewer would be a lid.
  for (let r = 0; r < 3; r++) {
    fillA(ctx, pal, "line", 0.30, kbX + 3, kbY + 2 + r * 2, kbW - 6, 1);
  }
  // The mouse, on the side the hand is on.
  const mx = seat - (o?.toward ?? -1) * 20;
  fill(ctx, pal, "line", mx - 3, kbY + 1, 6, 7);
  fill(ctx, pal, "metal", mx - 2, kbY + 2, 4, 5);

  // One thing lying on the far half of the desk, chosen from the position of the desk itself.
  // In the reference every desk carries something: papers, a mug, a plant. That is what makes
  // twelve identical workplaces read as twelve **used** workplaces instead of as a showroom,
  // and it costs four rectangles.
  //
  // The choice comes out of `mix(...)` over the position, so the same desk carries the same
  // thing in every frame and in the replay (rule 3.2). A `Math.random` here would make the mug
  // dance across the desk while rewinding.
  // The drawer unit under the desk, on the side away from the seat. In the reference there is
  // one at every workplace and they carry most of the colour in that part of the picture:
  // twelve tan tops and twelve grey chairs are a lot of nothing without them.
  const TONES: readonly PalKey[] = ["chair", "rug", "clay", "plant", "metal"];
  const dx = (o?.toward ?? -1) * -20;
  const dw = 14, dh = 18;
  fill(ctx, pal, "line", X + dx - (dw >> 1), Y - dh, dw, dh);
  fill(ctx, pal, TONES[mix(cx * 17 + yBase, SALT_DRAWER) % TONES.length],
       X + dx - (dw >> 1) + 1, Y - dh + 1, dw - 2, dh - 2);
  for (let k = 0; k < 3; k++) {
    fillA(ctx, pal, "line", 0.35, X + dx - (dw >> 1) + 2, Y - dh + 4 + k * 5, dw - 4, 1);
  }

  const px = X + (o?.toward ?? -1) * -18;
  const py = Y - 20;
  switch (mix(cx * 131 + yBase, SALT_CLUTTER) % 4) {
    case 0:  // a stack of paper
      fill(ctx, pal, "line", px - 5, py, 10, 8);
      fill(ctx, pal, "paper", px - 4, py + 1, 8, 6);
      fillA(ctx, pal, "line", 0.30, px - 3, py + 3, 6, 1);
      fillA(ctx, pal, "line", 0.30, px - 3, py + 5, 4, 1);
      break;
    case 1: {  // a mug
      fill(ctx, pal, "line", px - 3, py + 1, 7, 7);
      fill(ctx, pal, "paper", px - 2, py + 2, 4, 5);
      fill(ctx, pal, "clay", px - 2, py + 2, 4, 2);
      break;
    }
    case 2:  // a small plant in a pot
      fill(ctx, pal, "line", px - 4, py, 8, 5);
      fill(ctx, pal, "plant", px - 3, py + 1, 6, 3);
      fillA(ctx, pal, "plantLo", 0.55, px - 3, py + 3, 6, 1);
      fill(ctx, pal, "line", px - 3, py + 5, 6, 4);
      fill(ctx, pal, "clay", px - 2, py + 6, 4, 2);
      break;
    default:  // nothing: an empty desk among eleven full ones is the one that looks used
      break;
  }
}

/** The salt of the drawer unit. */
const SALT_DRAWER = 0x524f4c4c;  // "ROLL"

/** The salt of what lies on a desk. Its own, so the clutter does not correlate with the floor
 *  or with the books in the shelves. */
const SALT_CLUTTER = 0x4b52414d;  // "KRAM"

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
  // A light edge along the backrest: from above it is the part furthest from the floor and
  // therefore the part that catches the light. Without it seat and backrest are one blob.
  fillA(ctx, pal, "wallHi", 0.22, X - (w >> 1) + 4, Y - h + 2, w - 8, 1);
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

/** The tint of a parquet block comes from its coordinates through this salt. Its own salt (rule
 *  3.2), so the floor does not correlate with anything else that varies. */
const SALT_SHADE = 0x53434841;  // "SCHA"

/**
 * The back wall: surface, ceiling edge, a slight fall towards the bottom, skirting board.
 *
 * The fall is three stepped bands instead of a gradient (rule 2.1): at 38 rows of height the
 * difference cannot be seen anyway, and bands stay checkable against golden images.
 */
export function drawWall(ctx: Ctx, pal: Pal): void {
  // Finely drawn (stage 3): `ctx` is the HD view and the computation is in buffer pixels.
  //
  // The wall is built the way a real one is: plaster on top, a panelled dado below, a rail
  // between them and a skirting board at the bottom. That horizontal division is the single
  // biggest reason a top-down room reads as a **room** and not as a coloured strip: it gives
  // the eye two edges at known heights, and everything standing in front of it inherits that
  // scale.
  const W = ART.w * HD, H = WALL_H * HD;
  /** Where the dado ends and the plaster begins. Roughly the lower third, as in a real one. */
  const RAIL = H - 22;

  fill(ctx, pal, "wall", 0, 0, W, H);
  fill(ctx, pal, "wallHi", 0, 0, W, 3);
  // The plaster darkens very slightly towards the rail: without it the upper half is a flat
  // area, and a flat area of 480 units wide looks like a missing texture.
  for (let i = 0; i < 5; i++) {
    fillA(ctx, pal, "wallLo", 0.03 + i * 0.02, 0, RAIL - 12 + i * 3, W, 3);
  }

  // The dado: a shade darker than the plaster, with vertical panel divisions every 40 art
  // units. Two lines per division (dark plus bright) so it reads as an edge between two
  // panels, not as a scratch.
  fillA(ctx, pal, "wallLo", 0.42, 0, RAIL, W, H - RAIL - 7);
  for (let x = 20 * HD; x < W; x += 40 * HD) {
    fillA(ctx, pal, "line", 0.16, x, RAIL + 3, 1, H - RAIL - 12);
    fillA(ctx, pal, "wallHi", 0.22, x + 1, RAIL + 3, 1, H - RAIL - 12);
  }
  // The chair rail on top of the dado: bright board, dark underside. Three rows, and they are
  // what makes the dado look applied instead of painted on.
  fill(ctx, pal, "wallHi", 0, RAIL - 3, W, 3);
  fillA(ctx, pal, "line", 0.22, 0, RAIL, W, 1);

  // Skirting board: bright board on the floor line with a dark shadow gap under it.
  fill(ctx, pal, "wallHi", 0, H - 7, W, 6);
  fillA(ctx, pal, "line", 0.20, 0, H - 8, W, 1);
  fillA(ctx, pal, "line", 0.55, 0, H - 1, W, 1);
  fillA(ctx, pal, "shadow", 0.16, 0, H, W, 2);
}

/**
 * The plank floor. Rows of `PLANK_H`, joints every `PLANK_W`, offset per row with an enforced
 * minimum offset. Every board gets one of three tints from its hash, because the same area in
 * one colour looks like linoleum.
 */
export function drawFloor(ctx: Ctx, pal: Pal): void {
  // Finely drawn (stage 3), in buffer pixels.
  //
  // **Carpet tiles.** Not planks, not parquet: a grid of squares, laid quarter turned so that
  // neighbours differ slightly in tone. That is what an office floor actually is, and it is the
  // one floor texture that stays quiet under twelve figures. Wood everywhere had the opposite
  // effect: a warm surface over 70 % of the picture pulls all the attention downwards, and
  // whatever stands on it has to fight the ground it stands on.
  //
  // The wood is not gone, it moved: it now marks the **zones** (`drawZone`), the meeting area
  // and the lounge. A floor that is the same everywhere says nothing about the room; a floor
  // that changes says "this part is for something else", and that is the whole job of a floor
  // in a top-down room.
  const W = ART.w * HD, H = ART.h * HD, TOP = WALL_H * HD;
  /** Edge length of a carpet tile in buffer pixels. 32 is the tile size of the games this is
   *  modelled on, and a figure is about one tile wide: that is what sets the scale of the room
   *  for the eye. */
  const T = 32;

  fill(ctx, pal, "floor", 0, TOP, W, H - TOP);

  // The chequerboard: only every second tile is touched at all, so half the calls.
  for (let by = 0, ty = 0; by < H - TOP; by += T, ty++) {
    const y = TOP + by;
    const th = Math.min(T, H - y);
    for (let bx = ((ty & 1) === 0 ? 0 : T), tx = 0; bx < W; bx += T * 2, tx++) {
      fillA(ctx, pal, "floorHi", 0.28, bx, y, Math.min(T, W - bx), th);
    }
  }

  // The seams, as whole lines across the picture: 45 calls instead of one border per tile.
  for (let y = TOP; y < H; y += T) fillA(ctx, pal, "floorLo", 0.45, 0, y, W, 1);
  for (let x = 0; x < W; x += T) fillA(ctx, pal, "floorLo", 0.45, x, TOP, 1, H - TOP);

  // A band of shadow under the back wall: the floor catches no grazing light there.
  for (let i = 0; i < 7; i++) {
    fillA(ctx, pal, "shadow", 0.22 - i * 0.028, 0, TOP + i, W, 1);
  }
}

/**
 * A floor zone in wood. `cx`/`yBase` are the centre and the **front edge**.
 *
 * Planks with a contour: what marks a zone is the edge, not the texture. Without the contour a
 * wooden area on carpet reads as a stain; with it, it reads as a platform somebody laid there
 * on purpose.
 */
export function drawZone(
  ctx: Ctx, cx: number, yBase: number, w: number, h: number, pal: Pal,
): void {
  const X = (cx - (w >> 1)) * HD, Y = (yBase - h) * HD;
  const W = w * HD, H = h * HD;
  fill(ctx, pal, "line", X, Y, W, H);
  fill(ctx, pal, "rug", X + 1, Y + 1, W - 2, H - 2);
  // **Long** boards, running the whole width of the zone, with one seam every 14 rows and no
  // butt joints at all. Short staggered boards were tried and gave the room a brick wall lying
  // on the ground for the second time: a board six times as long as it is high is a brick, and
  // the eye reads the row rather than the board. A real board is 15:1 and above.
  for (let y = Y + 14; y < Y + H - 1; y += 14) {
    fillA(ctx, pal, "rugLo", 0.38, X + 1, y, W - 2, 1);
    fillA(ctx, pal, "wallHi", 0.14, X + 1, y + 1, W - 2, 1);
  }
  fillA(ctx, pal, "wallHi", 0.22, X + 1, Y + 1, W - 2, 1);
  fillA(ctx, pal, "shadow", 0.14, X, Y + H, W, 1);
}

/**
 * The three walls that are not the back wall: left, right and the front edge.
 *
 * Without them the floor ran off all four sides of the picture, and a floor without an edge is
 * not a room but a texture. The frame is what turns the picture into a **place**: the eye reads
 * an enclosed area at once and stops looking for what lies beyond the edge.
 *
 * They are drawn as a top surface with a dark inner edge, not as a perspective wall. At twelve
 * pixels wide a perspective would need a vanishing point, and there is none: the room is seen
 * from straight above, and only the back wall stands up because a door and windows have to be
 * in it.
 */
export function drawRoomEdges(ctx: Ctx, pal: Pal): void {
  const W = ART.w * HD, H = ART.h * HD, TOP = WALL_H * HD;
  const S = SIDE_W * HD;

  // Plaster, a skirting board along the inner edge, a dark line where it meets the floor: the
  // same three parts as the back wall, only turned. That is why the frame reads as walls and
  // not as a border drawn around the picture.
  fill(ctx, pal, "wall", 0, TOP, S, H - TOP);
  fill(ctx, pal, "wallHi", S - 6, TOP, 5, H - TOP);
  fill(ctx, pal, "line", S - 1, TOP, 1, H - TOP);

  fill(ctx, pal, "wall", W - S, TOP, S, H - TOP);
  fill(ctx, pal, "wallHi", W - S + 1, TOP, 5, H - TOP);
  fill(ctx, pal, "line", W - S, TOP, 1, H - TOP);

  fill(ctx, pal, "wall", 0, H - S, W, S);
  fill(ctx, pal, "wallHi", 0, H - S + 1, W, 5);
  fill(ctx, pal, "line", 0, H - S, W, 1);

  // The shadow the walls throw onto the floor. Three rows are enough to lift the frame off the
  // ground instead of gluing it on.
  for (let i = 0; i < 3; i++) {
    const a = 0.16 - i * 0.05;
    fillA(ctx, pal, "shadow", a, S + i, TOP, 1, H - TOP - S);
    fillA(ctx, pal, "shadow", a, W - S - 1 - i, TOP, 1, H - TOP - S);
    fillA(ctx, pal, "shadow", a, S, H - S - 1 - i, W - 2 * S, 1);
  }
}

/** Width of the side and front walls in art units. */
export const SIDE_W = 8;

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

// ═══ Lounge and storage (procedural) ═════════════════════════════════════════
//
// These four pieces are drawn with `fill` instead of as an art, and that is a decision, not
// laziness: they are **boxes**. A box with a contour is three calls; as a string art it is
// twenty hand counted rows in which a single missing character shifts everything below it. The
// motifs of this room (a face, a plant, a rack front) stay art, the boxes become code.
//
// They exist because the room was empty. Twelve workplaces along the walls and one table in the
// middle leave two thirds of the floor bare, and a bare floor reads as an unfinished level, not
// as an office. What fills it has to be furniture nobody has to interpret: seating, shelves,
// water, pictures.

/** A body with a contour: the shape almost everything in here has. `top` gets a light edge, so
 *  the object catches the light of the windows and stands instead of lying. */
function slab(
  ctx: Ctx, pal: Pal, x: number, y: number, w: number, h: number, body: PalKey,
): void {
  if (w < 2 || h < 2) return;
  fill(ctx, pal, "line", x, y, w, h);
  fill(ctx, pal, body, x + 1, y + 1, w - 2, h - 2);
  fillA(ctx, pal, "wallHi", 0.30, x + 1, y + 1, w - 2, 1);
}

export const LOUNGE = { sofa: { w: 30, h: 15 }, table: { w: 18, h: 8 } } as const;

/**
 * A two seater. `cx` is the centre, `yBase` the row it stands on.
 *
 * Backrest, seat, two armrests, four feet: five boxes. The seat is drawn **after** the
 * backrest and one row lower, so the shadow line between them says which is in front. That one
 * row is the difference between a sofa and a coloured rectangle.
 */
export function drawSofa(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  const w = LOUNGE.sofa.w, h = LOUNGE.sofa.h;
  const x = cx - (w >> 1), y = yBase - h;
  contactShadow(ctx, pal, cx, yBase, w - 4);
  slab(ctx, pal, x + 2, y, w - 4, 7, "chair");             // backrest
  slab(ctx, pal, x, y + 5, 5, 8, "chair");                 // armrest left
  slab(ctx, pal, x + w - 5, y + 5, 5, 8, "chair");         // armrest right
  slab(ctx, pal, x + 4, y + 6, w - 8, 6, "chairLo");       // seat
  // The seam between the two cushions: a single line, otherwise it is a bench.
  fillA(ctx, pal, "line", 0.35, cx, y + 7, 1, 4);
  fill(ctx, pal, "line", x + 2, y + 12, 3, 3);
  fill(ctx, pal, "line", x + w - 5, y + 12, 3, 3);
}

/** The low table in front of the sofa. A top and four legs; the magazine on it is one bright
 *  rectangle, and it is what makes the table look used instead of delivered. */
export function drawLowTable(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  const w = LOUNGE.table.w, h = LOUNGE.table.h;
  const x = cx - (w >> 1), y = yBase - h;
  contactShadow(ctx, pal, cx, yBase, w - 2);
  slab(ctx, pal, x, y, w, 5, "desk");
  fill(ctx, pal, "paper", x + 4, y + 1, 6, 2);
  fill(ctx, pal, "line", x + 1, y + 5, 2, 3);
  fill(ctx, pal, "line", x + w - 3, y + 5, 2, 3);
}

export const SHELF = { w: 30, h: 22 } as const;

/**
 * A shelf with books. `cx` is the centre, `yBase` the row it stands on.
 *
 * The books are the point: three shelves of plain boxes are a cupboard, and a cupboard says
 * nothing. Their colours come from `mix(...)` over their position, so the same shelf carries
 * the same books in every frame and in the replay (rule 3.2), and no two neighbours are
 * certain to match.
 */
export function drawShelf(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  const w = SHELF.w, h = SHELF.h;
  const x = cx - (w >> 1), y = yBase - h;
  const TONES: readonly PalKey[] = ["clay", "chair", "plant", "acc", "desk", "rug"];
  contactShadow(ctx, pal, cx, yBase, w - 4);
  slab(ctx, pal, x, y, w, h, "deskLo");
  for (let row = 0; row < 3; row++) {
    const sy = y + 2 + row * 7;
    fill(ctx, pal, "line", x + 1, sy + 5, w - 2, 1);
    // Books, from the left, of unequal height: a shelf filled to the brim reads as a wall.
    let bx = x + 2;
    let k = 0;
    while (bx < x + w - 4) {
      const r = mix(row * 61 + k, SALT_SHELF);
      const bw = 2 + (r % 3);
      const gap = (r >> 3) % 5 === 0;
      if (!gap) {
        const top = sy + ((r >> 5) % 2);
        fill(ctx, pal, "line", bx, top, bw, sy + 5 - top);
        fill(ctx, pal, TONES[(r >> 7) % TONES.length], bx, top + 1, bw - 1, sy + 4 - top);
      }
      bx += bw + 1;
      k++;
    }
  }
}

/** The salt of the books. Its own, so the shelf does not correlate with the floor. */
const SALT_SHELF = 0x4255434b;  // "BUCK"

export const COOLER = { w: 11, h: 22 } as const;

/** Water cooler: a bottle on a body. The bottle is the recognisable part, so it gets the light
 *  edge and the body does not. */
export function drawCooler(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  const w = COOLER.w, h = COOLER.h;
  const x = cx - (w >> 1), y = yBase - h;
  contactShadow(ctx, pal, cx, yBase, w - 2);
  slab(ctx, pal, x + 1, y + 8, w - 2, h - 8, "metal");
  fill(ctx, pal, "line", x + 3, y + 15, w - 6, 2);          // the tap
  // The bottle: narrower at the neck, so it is not a second box.
  fill(ctx, pal, "line", x + 3, y, w - 6, 3);
  fill(ctx, pal, "line", x + 1, y + 2, w - 2, 7);
  fill(ctx, pal, "glass", x + 2, y + 3, w - 4, 5);
  fillA(ctx, pal, "wallHi", 0.45, x + 3, y + 3, 2, 4);
}

/** A framed picture on the wall. `yBase` is its lower edge. Two of them are enough to take the
 *  emptiness out of a long wall; a third would start to look like a gallery. */
export function drawPicture(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, tone: PalKey,
): void {
  const w = 15, h = 12;
  const x = cx - (w >> 1), y = yBase - h;
  fill(ctx, pal, "line", x, y, w, h);
  fill(ctx, pal, "paper", x + 1, y + 1, w - 2, h - 2);
  fill(ctx, pal, tone, x + 2, y + 2, w - 4, h - 5);
  fillA(ctx, pal, "line", 0.35, x + 2, y + h - 5, w - 4, 1);
  fillA(ctx, pal, "shadow", 0.18, x + 1, y + h, w, 1);
}

/**
 * The interior walls. `WALLS` comes out of `const.ts`, so from the same place the route finding
 * takes them: there is no second list that could drift.
 *
 * Drawn like the outer frame and for the same reason: the wall surface, a bright skirting board
 * along both long sides, and a dark line where it meets the floor. A wall without a skirting
 * board reads as a bar lying on the floor; with one it stands.
 *
 * The doorways draw themselves, because they are the gaps between two segments. What is drawn
 * on top of them is the **reveal**: the two dark ends where a segment stops, which is what makes
 * an opening read as a door and not as a wall somebody forgot to finish.
 */
export function drawWalls(ctx: Ctx, pal: Pal): void {
  for (const w of WALLS) {
    const x = w.x * HD, y = w.y * HD, ww = w.w * HD, wh = w.h * HD;
    fill(ctx, pal, "wall", x, y, ww, wh);
    fill(ctx, pal, "wallHi", x, y, ww, 2);
    fill(ctx, pal, "wallHi", x, y + wh - 3, ww, 3);
    fill(ctx, pal, "line", x, y, ww, 1);
    fill(ctx, pal, "line", x, y + wh - 1, ww, 1);
    // The ends: a dark edge, so a doorway has a jamb.
    fill(ctx, pal, "line", x, y, 1, wh);
    fill(ctx, pal, "line", x + ww - 1, y, 1, wh);
    // The shadow the wall throws onto the floor, on the side away from the windows.
    for (let i = 0; i < 3; i++) {
      fillA(ctx, pal, "shadow", 0.16 - i * 0.05, x, y + wh + i, ww, 1);
    }
  }
}

/**
 * The cubicle screens. Lower and thinner than a wall, and in fabric rather than plaster: a
 * bright top edge, a darker face, a shadow on the floor. That is enough for the eye to file it
 * as furniture and not as architecture, which is exactly the difference between the two.
 */
export function drawPartitions(ctx: Ctx, pal: Pal): void {
  for (const w of PARTITIONS) {
    const x = w.x * HD, y = w.y * HD, ww = w.w * HD, wh = w.h * HD;
    fill(ctx, pal, "line", x, y, ww, wh);
    fill(ctx, pal, "metal", x + 1, y + 1, ww - 2, wh - 2);
    fillA(ctx, pal, "wallHi", 0.35, x + 1, y + 1, ww - 2, 1);
    fillA(ctx, pal, "shadow", 0.14, x + ww, y + 2, 2, wh - 2);
  }
}

/**
 * A continuous window front. `x0`/`x1` are its ends in art units, `yBase` its lower edge.
 *
 * The wall used to carry eight separate windows in two groups, each one a little art with its
 * own frame, and it read as a row of picture frames. A modern office has a **band**: one
 * opening over the whole length, divided by mullions, with a transom near the top and a sill
 * that steps out below. That is what the reference has, and it is the one thing on this wall
 * that says which decade the building is from.
 *
 * Everything is drawn, nothing is an art: a band has to stretch to any length, and an art
 * cannot.
 */
export function drawWindowFront(
  ctx: Ctx, pal: Pal, x0: number, x1: number, yTop: number, h: number,
): void {
  const X = x0 * HD, W = (x1 - x0) * HD, Y = yTop * HD, H = h * HD;
  // The reveal: the opening sits **in** the wall, so it needs a dark edge all round, otherwise
  // the glass looks stuck onto the plaster.
  fill(ctx, pal, "line", X, Y, W, H);
  fill(ctx, pal, "out", X + 2, Y + 2, W - 4, H - 4);
  // The sky: three bands, brightest at the top. A gradient is forbidden (rule 2.1) and would be
  // mush at this height anyway.
  fillA(ctx, pal, "wallHi", 0.30, X + 2, Y + 2, W - 4, 3);
  fillA(ctx, pal, "wallHi", 0.16, X + 2, Y + 5, W - 4, 3);
  // The transom, a third down: without it the band is a slot, with it it is a window.
  const ty = Y + Math.round(H * 0.34);
  fill(ctx, pal, "metal", X + 2, ty, W - 4, 2);
  fillA(ctx, pal, "line", 0.35, X + 2, ty + 2, W - 4, 1);
  // Mullions every 28 art units, the same rhythm the old single windows had.
  for (let x = X + 28 * HD; x < X + W - 4; x += 28 * HD) {
    fill(ctx, pal, "metal", x, Y + 2, 3, H - 4);
    fillA(ctx, pal, "line", 0.30, x + 3, Y + 2, 1, H - 4);
  }
  // One diagonal of reflection per pane, so the glass is glass and not a hole.
  for (let x = X + 6; x < X + W - 6; x += 28 * HD) {
    for (let k = 0; k < 5; k++) fillA(ctx, pal, "wallHi", 0.22, x + k, Y + 5 + k, 2, 1);
  }
  // The sill: a bright board sticking out below, with its shadow on the parapet.
  fill(ctx, pal, "wallHi", X - 2, Y + H, W + 4, 3);
  fill(ctx, pal, "line", X - 2, Y + H + 3, W + 4, 1);
  fillA(ctx, pal, "shadow", 0.18, X - 2, Y + H + 4, W + 4, 2);
}
