// Layer 1: sprites are source code.
//
// An `Art` is a picture as an array of strings: one character per pixel, `.` and space are
// transparent, and every other character points over `map` at a palette key. No image files,
// no `drawImage`, no loading state, no network: a sprite is readable in the diff and can be
// commented on in a code review.
//
// The seven reserved characters (`S H T P h s t`) stand in the person arts and are served
// **only while drawing** from the palette passed in. The same 19 sprite parts thereby give
// twelve different people without a single pixel standing twice in the source.

import type { Ctx } from "../types.ts";
import type { Pal, PalKey } from "./palette.ts";

/** One sprite. `rows` are lines of equal length, `map` maps every character used. */
export type Art = { rows: readonly string[]; map: Readonly<Record<string, PalKey>> };

/** Transparent. Two characters, because `.` keeps the grid legible and a space is faster to
 *  type while rebuilding a sprite. */
function transparent(ch: string): boolean {
  return ch === "." || ch === " ";
}

/**
 * Builds an `Art` and checks it in the process.
 *
 * The check is half the value of this file: a sprite is a block of strings of equal length,
 * and a single forgotten character shifts everything below it by one column. In the finished
 * picture that looks like a drawing bug somewhere in the renderer, and one searches for hours
 * in the wrong place. A missing `map` character is even more insidious: the pixel is simply
 * missing without a sound.
 *
 * That is why it throws while the module loads instead of reporting later. That is safe:
 * arts are static source, and the check depends on no input. What loads once loads always.
 * lädt immer.
 */
export function defineArt(rows: readonly string[], map: Readonly<Record<string, PalKey>>): Art {
  if (rows.length === 0) throw new Error("Art ohne Zeilen");
  const w = rows[0].length;
  if (w === 0) throw new Error("Art mit leerer Zeile 0");
  for (let y = 0; y < rows.length; y++) {
    const row = rows[y];
    if (row.length !== w) {
      throw new Error(`Art: Zeile ${y} ist ${row.length} Zeichen breit, Zeile 0 aber ${w}`);
    }
    for (let x = 0; x < w; x++) {
      const ch = row[x];
      if (transparent(ch)) continue;
      if (!(ch in map)) {
        throw new Error(`Art: Zeichen "${ch}" bei ${x},${y} steht nicht in map`);
      }
    }
  }
  return { rows, map };
}

/** Breite in Pufferpixeln. */
export function artW(art: Art): number {
  return art.rows[0].length;
}

/** Height in buffer pixels. */
export function artH(art: Art): number {
  return art.rows.length;
}

/** Left edge of an art centred at `cx`. With an even width the centre falls half a column to
 *  the left, consistently for all arts, so that two objects placed beside each other do not
 *  jump against each other by a pixel depending on whether their width is even. */
export function artLeft(art: Art, cx: number): number {
  return cx - (artW(art) >> 1);
}

export interface DrawOpts {
  /** Mirrored horizontally, so looking to the left. */
  flip?: boolean;
  /** Opacity. It is **always** reset to 1 after drawing (rule 2.1: there is no `restore`, and
   *  resetting is the duty of the drawer). */
  alpha?: number;
  /** All visible pixels in one colour, for shadow and outline passes. Transparent pixels
   *  stay transparent. */
  tint?: PalKey;
}

/**
 * Draws an `Art` with the centre `cx` and the foot point `yBase`.
 *
 * `yBase` is the first line **below** the sprite (rule 2.2): a sprite 24 pixels high occupies
 * `yBase-24 … yBase-1`. The scene sorts by exactly this value, and an object that passes its
 * top edge sorts wrongly and disappears behind furniture it stands in front of.
 *
 * **Why the runs**: naively it would be one `fillRect(1x1)` per pixel, so a 16x24 figure would
 * be 384 calls, twelve figures over 4600, and the frame rate would die of the number of calls,
 * not of the filled area. Horizontal neighbours of the same colour are therefore combined into
 * **one** `fillRect`, which puts a figure at 60 to 120 calls. `fillStyle` is set only on a
 * colour change, which is not free in a canvas either.
 */
export function drawArt(
  ctx: Ctx, art: Art, cx: number, yBase: number, pal: Pal, opts?: DrawOpts,
): void {
  const rows = art.rows;
  const h = rows.length;
  const w = rows[0].length;
  const x0 = cx - (w >> 1);
  const y0 = yBase - h;

  const flip = opts?.flip === true;
  const tint = opts?.tint;
  const alpha = opts?.alpha ?? 1;
  if (alpha !== 1) ctx.globalAlpha = alpha;

  let style = "";
  for (let ry = 0; ry < h; ry++) {
    const row = rows[ry];
    const y = y0 + ry;
    // Run state: `key === ""` means "transparent right now".
    let key: PalKey | "" = "";
    let start = 0;
    // One step past the end of the line closes the last run, which saves repeating the
    // closing code behind the loop.
    for (let rx = 0; rx <= w; rx++) {
      const ch = rx < w ? row[flip ? w - 1 - rx : rx] : ".";
      const next: PalKey | "" = transparent(ch) ? "" : (tint ?? art.map[ch]);
      if (next !== key) {
        if (key !== "") {
          const col = pal[key];
          if (col !== style) { ctx.fillStyle = col; style = col; }
          ctx.fillRect(x0 + start, y, rx - start, 1);
        }
        key = next;
        start = rx;
      }
    }
  }

  if (alpha !== 1) ctx.globalAlpha = 1;
}

/** One rectangle in a palette colour. The only place where procedural parts (wall, floor,
 *  bubbles) touch `fillStyle`, which keeps the contract in one place. */
export function fill(
  ctx: Ctx, pal: Pal, key: PalKey, x: number, y: number, w: number, h: number,
): void {
  if (w <= 0 || h <= 0) return;
  ctx.fillStyle = pal[key];
  ctx.fillRect(x, y, w, h);
}

/** The same with opacity, and with the mandatory reset to 1. A forgotten `globalAlpha` makes
 *  the whole rest of the picture pale; that is the most common mistake in a drawing layer
 *  without `save`/`restore`. */
export function fillA(
  ctx: Ctx, pal: Pal, key: PalKey, alpha: number,
  x: number, y: number, w: number, h: number,
): void {
  if (w <= 0 || h <= 0 || alpha <= 0) return;
  ctx.globalAlpha = alpha;
  ctx.fillStyle = pal[key];
  ctx.fillRect(x, y, w, h);
  ctx.globalAlpha = 1;
}

/**
 * An art in double resolution: every pixel becomes a 2x2 block.
 *
 * The bridge of the resolution stages: a family that is not drawn finely yet is drawn in the
 * HD grid with this and looks **exactly** as before. That way a figure can already carry a
 * finely drawn head while its legs are still coarse, instead of all sixteen arts having to be
 * finished before anything looks better.
 *
 * Whoever passes an art through here gains no detail. They only gain the right to draw neighbouring parts finely.
 */
export function verdoppelt(art: Art): Art {
  const rows: string[] = [];
  for (const row of art.rows) {
    let breit = "";
    for (const ch of row) breit += ch + ch;
    rows.push(breit, breit);
  }
  return { rows, map: art.map };
}
