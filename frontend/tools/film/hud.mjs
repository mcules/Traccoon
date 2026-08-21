// The two overlays of the film: the permanent line at the bottom and the chapter card.
//
// Both draw with the same means as the stage (`drawText`/`fill`/`fillA` from layer 1) and
// thereby keep the `Ctx` contract (only `fillStyle`, `globalAlpha`, `fillRect`) without a
// second drawing world. A font routine of its own would be the second truth about the 3x5
// font, and the first deviation would stand out only in the finished GIF.
//
// **No word comes into being here.** Time, title and label arrive ready made: the time
// computation as integer arithmetic from `film.mjs`, everything linguistic from Python. A
// `toLocaleTimeString` at this place would make the picture depend on the ICU version of the
// base image, and thereby the golden hash on a library update.

import { ART } from "../../src/components/office/const.ts";
import { fill, fillA } from "../../src/components/office/pixel/art.ts";
import { GRADES } from "../../src/components/office/pixel/palette.ts";
import { drawText, textW } from "../../src/components/office/pixel/props.ts";

/** Height of the bar: 5 pixels of font plus 3 of air each. Narrower and the descenders stick
 *  to the edge of the picture, wider and the bar eats the front desk row. */
const BAND_H = 11;

/**
 * The line at the bottom edge. It stands **permanently**: it is the only place where the
 * viewer sees that an hour passed between two chapters. Without it the time lapse would look
 * like a continuous recording, and exactly that would be a lie.
 */
export function hudLine(ctx, grade, text) {
  const pal = GRADES[grade];
  const y = ART.h - BAND_H;
  fillA(ctx, pal, "shadow", 0.62, 0, y, ART.w, BAND_H);
  // One edge upwards; otherwise the bar floats above the floor instead of lying on it.
  fill(ctx, pal, "ink", 0, y, ART.w, 1);
  drawText(ctx, pal, "paper", 4, y + 3, text);
}

/**
 * The chapter card between two chapters.
 *
 * It only covers the scene, it does not replace it: below it the picture of the moment the
 * card points at keeps running. A black full screen would be cheaper and would be the moment
 * the film stops being a window into the room.
 *
 * `k` (0..1) is the fade in and out. The caller determines the curve; here it is only applied,
 * so that the card has no time notion of its own.
 */
export function chapterCard(ctx, grade, title, time, k) {
  const a = k <= 0 ? 0 : k >= 1 ? 1 : k;
  if (a <= 0) return;
  const pal = GRADES[grade];

  fillA(ctx, pal, "shadow", 0.82 * a, 0, 0, ART.w, ART.h);

  // A box behind the text, not only a veil: the room has bright monitors and a patterned
  // carpet, and a 3x5 font over that cannot be read from three metres.
  const width = Math.max(textW(title), textW(time)) + 22;
  const height = 26;
  const x0 = (ART.w - width) >> 1;
  const y0 = (ART.h - height) >> 1;
  fillA(ctx, pal, "ink", 0.92 * a, x0, y0, width, height);
  fillA(ctx, pal, "acc", 0.9 * a, x0, y0, width, 1);
  fillA(ctx, pal, "acc", 0.9 * a, x0, y0 + height - 1, width, 1);

  drawText(ctx, pal, "paper", (ART.w - textW(title)) >> 1, y0 + 6, title, a);
  drawText(ctx, pal, "acc", (ART.w - textW(time)) >> 1, y0 + 15, time, a);
}
