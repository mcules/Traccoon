// Layer 1, the people.
//
// Scale (rule 1 of the pixel contract): a character is **16×24 buffer pixels**, not 16×24
// scene pixels. `POS_SCALE` appears here exactly once, to bring in the scene coordinate of an
// actor. For no dimension at all.
//
// The character is assembled from 19 parts instead of drawn as 8 finished poses:
//
//   head   3 × 10×9   front · side · back
//   hair   5 × 12×11  overlay, the lever for distinguishable silhouettes
//   Torso  3 ×  8×10
//   arms   4 ×  4×6   overlay: resting · typing A · typing B · reaching or carrying
//   Beine  4 ×  8×6   sitzen · stehen · gehen-A · gehen-B
//
// 19 parts instead of 8×12 finished sprites: eight poses times twelve people would be 96
// images of 384 pixels, four times the entire art budget. Assembled, the same variety costs 19
// parts, and every new pose afterwards costs one part, not twelve images.
//
// The build from bottom to top (the order is the occlusion):
//
//   yBase-1  … yBase-6    Beine        (8 breit, mittig)
//   yBase-6  … yBase-15   torso        (8 wide, overlaps the legs by one row)
//   yBase-9  … yBase-14   arms         (4 wide each, left and right of the torso)
//   yBase-16 … yBase-24   Kopf         (10 breit)
//   yBase-14 … yBase-24   hair         (12 wide, over head **and** shoulders)
//
// That makes 24 rows. The head takes 9 of them, deliberately too large for an adult: at 24
// pixels of total height the head is the only thing that reads as "human" at a glance.

import type { ActorState, Ctx, Gait, Look, Pose } from "../types.ts";
import { GATE_PULSE_MS, POS_SCALE } from "../const.ts";
import { mix } from "../ids.ts";
import type { Art } from "./art.ts";
import { defineArt, drawArt, fill, fillA, doubled } from "./art.ts";
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

/** How far the upper body sinks when sitting. Three pixels are few and enough: together with
 *  the sitting legs (thighs horizontal) the character reads as seated at once, and more would
 *  make the head disappear behind the desktop. */
const SIT_DROP = 3;

// ═══ The art ═════════════════════════════════════════════════════════════════
//
// Legend: `S`/`s` skin and skin shadow · `H`/`h` hair and hair shadow · `T`/`t` top and its
// shadow · `P` trousers. These seven characters are **reserved** for the figure and are only
// filled from `palFor(grade, look)` while drawing, so the same 19 parts make twelve different
// people without a single pixel appearing twice in the source.
// `i` (= `ink`) is not a reserved character but real ink: eyes and shoes are dark on every
// person and must not travel with the skin colour.

// ── Kopf ─────────────────────────────────────────────────────────────────────
// The eyes lie in **row 4**, not higher up. That is not anatomy but space management: the hair
// covers rows 0 to 3, and a fringe that overwrites an eye row turns every second hairstyle into
// a blind face.

const HEAD_FRONT = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "SSiSSSSiSS",
  "SSSSSSSSSS",
  ".SSSSSSSS.",
  "..sSSSSs..",
  "...sSSs...",
], { S: "S", s: "s", i: "ink" });

/** Facing right; facing left is mirrored. Two features separate the side view from the front
 *  view: the ear (`s`, row 4) and the nose, which sticks out one column further in row 5.
 *  Without the nose the head in profile looks like a front head that is too narrow. */
const HEAD_SIDE = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  ".SSSSSSSS.",
  ".SSSSSSSS.",
  ".SsSSSiSS.",
  ".SSSSSSSSS",
  ".SSSSSSSs.",
  "..sSSSSs..",
  "...sSSs...",
], { S: "S", s: "s", i: "ink" });

const HEAD_BACK = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "sSSSSSSSSs",
  ".SSSSSSSS.",
  "..ssssss..",
  "...sSSs...",
], { S: "S", s: "s" });


/**
 * The head from the front, the first part drawn by hand in the **fine grid** (stage 2).
 *
 * Doubling the old 10×9 head gives the same area in larger blocks: two black dots in an oval.
 * Only in the 20×18 grid is there room for what a face is recognised by: white of the eye next
 * to the pupil, a nose, a mouth, ears, and an edge that separates the head from the wooden
 * floor. That is exactly what distinguishes "pixel character" from "block".
 *
 * The edge is `s` (skin shadow), not `ink`: a deep black outline around a head 20 pixels wide
 * eats half the face and makes every character look like a cartoon mask. The same colour
 * shades nose and chin as well: one tone, three jobs.
 */
const HEAD_FRONT_HD = defineArt([
  "......ssssssss......",
  "....ssSSSSSSSSss....",
  "...sSSSSSSSSSSSSs...",
  "..sSSSSSSSSSSSSSSs..",
  "..sSSSSSSSSSSSSSSs..",
  ".sSSSSSSSSSSSSSSSSs.",
  ".sSSSSSSSSSSSSSSSSs.",
  ".sSSSSSSSSSSSSSSSSs.",
  ".sSSppiiSSSSiippSSs.",
  "ssSSSSiiSSSSiiSSSSss",
  "ssSSSSSSSssSSSSSSSss",
  ".sSSSSSSSssSSSSSSSs.",
  ".sSSSSSSSSSSSSSSSSs.",
  ".sSSSSSSssssSSSSSSs.",
  ".sSSSSSSSSSSSSSSSSs.",
  "..sSSSSSSSSSSSSSSs..",
  "...ssSSSSSSSSSSss...",
  ".....ssssssssss.....",
], { S: "S", s: "s", i: "ink", p: "paper" });

// Front by hand and fine, side and back doubled for now: the front view is the one seen almost
// always (standing, typing, speaking), and only the boss seat shows a back.
const HEADS: readonly Art[] = [HEAD_FRONT_HD, doubled(HEAD_SIDE), doubled(HEAD_BACK)];

/** Head direction. Numbers instead of strings, because they index into `HEADS` directly. */
const DIR_FRONT = 0;
const DIR_SIDE = 1;
const DIR_BACK = 2;

// ── Haar ─────────────────────────────────────────────────────────────────────
// Twelve characters differ at 16 pixels of width **only** through their silhouette. The skin
// colour cannot be seen from two metres, the shirt barely, the shape of the head at once. That
// is why the hair is the only part allowed to stick out past the head (12 instead of 10 wide)
// and to reach down to the shoulders (rows 9 and 10 already lie on the torso).
//
// Rows 0 to 8 coincide with the head, column n of the hair is column n-1 of the head.

const HAIR_SHORT = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHH.",
  ".HH......HH.",
  ".H........H.",
  "............",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_PART = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HhHHHHHHHH.",
  ".HH.....HHH.",
  ".H.......HH.",
  "..........H.",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_LONG = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHH.",
  "HHH......HHH",
  "HHh......hHH",
  "HHH......HHH",
  ".HH......HH.",
  ".HH......HH.",
  ".Hh......hH.",
  "............",
], { H: "H", h: "h" });

const HAIR_TAIL = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHHH",
  ".HH......HHH",
  ".H.......Hhh",
  "..........HH",
  "..........hH",
  "...........H",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_CURL = defineArt([
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  "HHHHHHHHHHHH",
  "HHHHHHHHHHHH",
  "HHH......HHH",
  "HHh......hHH",
  ".H........H.",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });


// ── Haar, fein gezeichnet ────────────────────────────────────────────────────
// The doubled headwear sat as an angular cap on the round skull: the old silhouette did not
// know the curve the fine grid now has. What is followed is therefore the EDGE, row by row the
// same curve as the head below it, plus one row of `h` at the lower edge of the fringe. That
// single row of shadow turns an area into a strand of hair: without it the hair sticks to the
// forehead like a painted patch.
//
// 24 wide against 20 of the head (hair may stick out, column n is head column n-2), 22 high.

/** A row without hair: the lower rows are empty or nearly empty in every hairstyle. */
const H_EMPTY = "........................";

/** The curve every hairstyle shares: it follows the skull of HEAD_FRONT_HD. */
const H_CAP: readonly string[] = [
  "........HHHHHHHH........",
  "......HHHHHHHHHHHH......",
  ".....HHHHHHHHHHHHHH.....",
  "....HHHHHHHHHHHHHHHH....",
  "....HHHHHHHHHHHHHHHH....",
  "...HHHHHHHHHHHHHHHHHH...",
  "...HHHHHHHHHHHHHHHHHH...",
];

const HAIR_MAP = { H: "H", h: "h" } as const;

const HAIR_SHORT_HD = defineArt([
  ...H_CAP,
  "...HHhhhhhhhhhhhhhhHH...",
  "...HH..............HH...",
  "...HH..............HH...",
  "...H................H...",
  H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY,
], HAIR_MAP);

/** Parting: a groove of shadow on the left, and on the right the top hair falls lower. */
const HAIR_PART_HD = defineArt([
  ...H_CAP.map((r, i) => (i >= 2 ? r.slice(0, 7) + "h" + r.slice(8) : r)),
  "...HHhhhhhhhhhhhhhhHH...",
  "...HH.............HHH...",
  "...HH..............HH...",
  "...H................HH..",
  "....................H...",
  H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY,
], HAIR_MAP);

/** Long: falls to the shoulders on both sides. */
const HAIR_LONG_HD = defineArt([
  ...H_CAP,
  "...HHhhhhhhhhhhhhhhHH...",
  "..HHH..............HHH..",
  "..HHH..............HHH..",
  "..HHH..............HHH..",
  "..HHh..............hHH..",
  "..HHh..............hHH..",
  "...Hh..............hH...",
  "...h................h...",
  H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY,
], HAIR_MAP);

/** Ponytail: short at the sides, a bundle at the back that falls over the shoulder. */
const HAIR_TAIL_HD = defineArt([
  ...H_CAP,
  "...HHhhhhhhhhhhhhhhHH...",
  "...HH..............HHHH.",
  "...HH...............HHHH",
  "...H.................HHH",
  "......................HH",
  ".......................h",
  H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY,
], HAIR_MAP);

/** Curls: the edge frays instead of running smooth. */
const HAIR_CURL_HD = defineArt([
  "........HHHHHHHH........",
  "......HHHHHHHHHHHH......",
  ".....HHHHhHHHHhHHHHH....",
  "....HHHHHHHHHHHHHHHHH...",
  "...HHHhHHHHHHHHhHHHHHH..",
  "...HHHHHHHHHHHHHHHHHHH..",
  "..HHHHHHHHHHHHHHHHHHHH..",
  "..HHhhhhhhhhhhhhhhhhHH..",
  "..HHH..............HHH..",
  "..HHh..............hHH..",
  "...H................H...",
  H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY, H_EMPTY,
], HAIR_MAP);

const HAIRS: readonly Art[] = [
  HAIR_SHORT_HD, HAIR_PART_HD, HAIR_LONG_HD, HAIR_TAIL_HD, HAIR_CURL_HD,
];

// ── Torso ────────────────────────────────────────────────────────────────────
// The lowest row is trousers (`P`) and not the top: it is the waistband the legs sit on.
// Without it a gap gapes between shirt hem and leg while walking.

const TORSO_PLAIN = defineArt([
  ".TTTTTT.",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "tTTTTTTt",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P" });

/** Shirt: collar (two skin pixels at the neckline) and a vertical button placket. */
const TORSO_SHIRT = defineArt([
  ".TTTTTT.",
  "TTtSStTT",
  "TTTtsTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "tTTtTTTt",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P", S: "S", s: "s" });

/** Kapuzenpulli: breitere Schulter, Kapuzenkante, Bauchtasche. */
const TORSO_HOOD = defineArt([
  "tTTTTTTt",
  "TtTTTTtT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTtttttT",
  "TTtTTTtT",
  "TTtttttT",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P" });


// ── Upper body, finely drawn ─────────────────────────────────────────────────
// Doubled, the button placket of the shirt read as braces: two buffer pixels wide on a torso
// 16 pixels wide is a strap, not a seam. In the fine grid it is a line. Added to that is what
// turns an area of colour into a body: a column of shadow on the side away from the light (the
// windows are on the left), a hem and a waistband.
//
// 16 wide by 20 high, the same area as the old 8×10, only at twice the resolution.

const TORSO_MAP = { T: "T", t: "t", P: "P", S: "S", s: "s" } as const;

const TORSO_PLAIN_HD = defineArt([
  "...tTTTTTTTTt...",
  ".tTTTTTTTTTTTTt.",
  "tTTTTTTTTTTTTTTt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "ttTTTTTTTTTTTttt",
  "tttttttttttttttt",
  "PPPPPPPPPPPPPPPP",
], TORSO_MAP);

/** Shirt: collar (skin at the neckline) and a button placket ONE unit wide. */
const TORSO_SHIRT_HD = defineArt([
  "...tTTTTTTTTt...",
  ".tTTTtSSSStTTTt.",
  "tTTTTtsSSstTTTTt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "tTTTTTTtTTTTTTtt",
  "ttTTTTTtTTTTTttt",
  "tttttttttttttttt",
  "PPPPPPPPPPPPPPPP",
], TORSO_MAP);

/** Kapuzenpulli: breitere Schulter, Kapuzenkante, Bauchtasche. */
const TORSO_HOOD_HD = defineArt([
  "..tTTTTTTTTTTt..",
  ".tTTTTTTTTTTTTt.",
  "tTTTTTTTTTTTTTTt",
  "tTTttttttttTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTTTTTTTTTTTTtt",
  "tTTttttttttTTTtt",
  "tTTtTTTTTTttTTtt",
  "tTTtTTTTTTttTTtt",
  "tTTttttttttTTTtt",
  "tTTTTTTTTTTTTTtt",
  "ttTTTTTTTTTTTttt",
  "tttttttttttttttt",
  "PPPPPPPPPPPPPPPP",
], TORSO_MAP);

const TORSOS: readonly Art[] = [TORSO_PLAIN_HD, TORSO_SHIRT_HD, TORSO_HOOD_HD];

// ── Arme ─────────────────────────────────────────────────────────────────────
// Drawn as the **right** arm (column 0 lies against the torso); the left one is the same art,
// mirrored. Four states are enough, because an arm at 4×6 can only say three things: it hangs,
// it rests on the keyboard, it reaches forward.

const ARM_REMAINDER = defineArt([
  "TTt.",
  "TTt.",
  "TTt.",
  ".Tt.",
  ".SS.",
  ".Ss.",
], { T: "T", t: "t", S: "S", s: "s" });

const ARM_TYPE_A = defineArt([
  "TTt.",
  "TTt.",
  ".TTt",
  "..Tt",
  "..SS",
  "....",
], { T: "T", t: "t", S: "S" });

const ARM_TYPE_B = defineArt([
  "TTt.",
  "TTt.",
  ".TTt",
  "..SS",
  "....",
  "....",
], { T: "T", t: "t", S: "S" });

const ARM_REACH = defineArt([
  "TTt.",
  "TTTt",
  ".TTS",
  "..SS",
  "....",
  "....",
], { T: "T", t: "t", S: "S" });


// ── Arme, fein gezeichnet ────────────────────────────────────────────────────
// Doubled, the arm was a skin coloured block without a hand. In the fine grid (8×12) there is
// room for a sleeve, a wrist and a hand with a shadow edge, and it is exactly the hand that
// turns "block with an outrigger" into a character that DOES something.

const ARM_MAP = { T: "T", t: "t", S: "S", s: "s" } as const;

const ARM_REMAINDER_HD = defineArt([
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  ".TTtt...",
  ".TTtt...",
  ".SSSs...",
  ".SSSs...",
  "..sSs...",
  "........",
], ARM_MAP);

const ARM_TYPE_A_HD = defineArt([
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  ".TTTtt..",
  ".TTTtt..",
  "..TTTtt.",
  "..TTtt..",
  "..SSSs..",
  "..SSSs..",
  "...ss...",
  "........",
], ARM_MAP);

const ARM_TYPE_B_HD = defineArt([
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  "TTTtt...",
  ".TTTtt..",
  ".TTTtt..",
  "..TTTtt.",
  "..SSSs..",
  "..SSSs..",
  "...ss...",
  "........",
  "........",
], ARM_MAP);

const ARM_REACH_HD = defineArt([
  "TTTtt...",
  "TTTTtt..",
  "TTTTTtt.",
  ".TTTTSs.",
  "..SSSSs.",
  "...sSs..",
  "........",
  "........",
  "........",
  "........",
  "........",
  "........",
], ARM_MAP);

const ARMS: readonly Art[] = [ARM_REMAINDER_HD, ARM_TYPE_A_HD, ARM_TYPE_B_HD, ARM_REACH_HD];

const ARM_REMAINDER_I = 0;
const ARM_TYPE_A_I = 1;
const ARM_TYPE_B_I = 2;
const ARM_REACH_I = 3;

/** From which row of an arm art the forearm begins.
 *
 *  With it a "short sleeve" costs **no** extra art: the lower part of the same sprite is drawn
 *  a second time, this time entirely in skin colour (`tint`). Four more arms would be
 *  affordable too, but they would have to be maintained on every change of shape, and that is
 *  exactly what gets forgotten. */
/** From which HD row the forearm begins (a short sleeve shows skin from here). */
const ARM_CUFF: readonly number[] = [6, 6, 6, 4];

/** The forearm part of every arm, cut once at load time. That is allowed because `drawArt`
 *  anchors at the **foot point**: an art shortened from the top lands in the same place as the
 *  original. */
const ARM_FORE: readonly Art[] = ARMS.map((a, i) => ({
  rows: a.rows.slice(ARM_CUFF[i]), map: a.map,
}));

// ── Beine ────────────────────────────────────────────────────────────────────
// The walk cycle has four frames but only three pieces of art: `stand · walk A · stand · walk B`.
// The passing position is the same pose twice, and that it looks the same both times is right:
// real legs look the same in both passes.

/** Sitting, from the side: thighs horizontal to the front, lower legs vertical. Together with
 *  `SIT_DROP` that is the whole difference between "stands at the desk" and "sits at the
 *  desk", and without it half the room would look as if it worked standing up. */
const LEGS_SIT = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  "...PPPPP",
  "......PP",
  "......PP",
  "....iiii",
], { P: "P", i: "ink" });

const LEGS_STATE = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  "PPP..PPP",
  "PPP..PPP",
  "PPP..PPP",
  "iii..iii",
], { P: "P", i: "ink" });

// The full stride is deliberately **wide**: the first version put the feet only two pixels
// apart, and in the golden image a walking character could not be told from a standing one. At
// 8 pixels of leg width the swing has to reach the edge, otherwise it is smaller than the line
// weight.

const LEGS_WALK_A = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  ".PPPPPP.",
  ".PP..PP.",
  "PP....PP",
  "ii....ii",
], { P: "P", i: "ink" });

/** The counter step. Not the mirror of A, because then both half steps would look the same and
 *  the gait would be a hop. B lands narrower and offset one pixel forward. */
const LEGS_WALK_B = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  ".PPPPPP.",
  "..PP.PP.",
  ".PP...PP",
  ".ii...ii",
], { P: "P", i: "ink" });

const LEGS: readonly Art[] = [LEGS_SIT, LEGS_STATE, LEGS_WALK_A, LEGS_WALK_B].map(doubled);

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
  const hx = cx - 5 * HD;
  if (variant === 1) {
    // The finely drawn front head already has its mouth in the art; a second one here would
    // come out one row above it, a moustache from two metres away. The variant therefore shows
    // only where the head is still coarse (side view).
    if (dir === DIR_FRONT) return;
    fill(ctx, pal, "s", hx + 4 * HD, headTop + 6 * HD, 2 * HD, HD);
  } else if (variant === 2) {
    // Chin beard: **one** row at the chin, four pixels wide. Two rows over six columns (the
    // first version) read from a distance as a black bar across the face: on a head 10 pixels
    // wide every second column is a third of the face.
    fill(ctx, pal, "h", hx + 3 * HD, headTop + 7 * HD, 4 * HD, HD);
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
  // `cx`/`yBase` kommen in HD-Einheiten herein; `Stance` rechnet weiter in Kunsteinheiten
  // (it describes a pose, not pixels). The conversion therefore happens exactly here, at the
  // seam between the two, and not scattered through `stanceOf`.
  const dirSign = flip ? -1 : 1;
  const bodyY = yBase + (s.drop + s.lift) * HD;
  const legsY = yBase;

  const torsoY = bodyY - 5 * HD;
  const headY = torsoY - 10 * HD;
  const armY = torsoY - 3 * HD;
  const hairY = headY + 2 * HD;

  const bodyX = cx + s.leanX * HD * dirSign;
  const armXNear = bodyX + (5 + s.armX) * HD * dirSign;
  const armXFar = bodyX - (5 - s.armX) * HD * dirSign;

  // Legs: the leading shoe is lengthened by `shoe` pixels, the stride from the seed, without
  // needing a second leg art for it.
  drawArt(ctx, LEGS[s.legs], cx, legsY, pal, { flip, alpha });
  if (s.shoe > 0) {
    // Walking left the shoe grows to the left: a width multiplied by `dirSign` would be
    // negative, and `fill` silently discards negative widths, so the stride of the half of the
    // room walking left would simply be shorter.
    const sx = flip ? cx - (4 + s.shoe) * HD : cx + 4 * HD;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "ink", sx, legsY - HD, s.shoe * HD, HD);
    if (alpha < 1) ctx.globalAlpha = 1;
  }

  drawArt(ctx, TORSOS[look.torso], bodyX, torsoY, pal, { flip, alpha });
  arm(ctx, pal, s.armFar, armXFar, armY, !flip, look.arms, alpha);
  drawArt(ctx, HEADS[s.dir], bodyX, headY, pal, { flip, alpha });
  if (alpha >= 1) face(ctx, pal, bodyX, headY - 9 * HD, look.head, s.dir);

  if (s.paper) {
    // The sheet in the hand. A single bright rectangle in front of the chest is enough:
    // otherwise "reads" cannot be told from "types", because both arms point forward. In front
    // of the body, not on it: on the chest it would read as a name badge.
    const px = bodyX + (dirSign * 5 - 2) * HD;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "paper", px, torsoY - 8 * HD, 5 * HD, 5 * HD);
    fill(ctx, pal, "ink", px + HD, torsoY - 7 * HD, 3 * HD, HD);
    fill(ctx, pal, "ink", px + HD, torsoY - 5 * HD, 2 * HD, HD);
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

  drawShadow(ctx, cx, yBase, pal, (a.pose === "sit" ? 10 : 12) * HD);
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
