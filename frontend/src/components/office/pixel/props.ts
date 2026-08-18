// Layer 1, everything lying above the characters: type, bubbles, plates, air.
//
// Two things make up this file.
//
// **First the type.** `fillText` is forbidden (rule 2.1), and for a hard reason: the same text
// yields different pixels per platform, so with a system font the golden pixel hashes of the
// checker would differ on every machine. Type is therefore art, not a font: 3×5 pixels per
// character, set by hand. That is the largest single item of the art source and at the same time
// the one that carries the most: without readable names the room is decoration, with them it is
// a view.
//
// The German umlauts are **not** optional: agents are called `gedaechtnis_suchen`, tickets carry
// titles like "Prüfbericht", and a missing "ü" shows in the image at once as a hole. They are
// six rows high instead of five and reach one row into the line spacing: an umlaut squeezed into
// five rows cannot be told from its base letter any more, and then one might as well leave it
// out.
//
// **Second the particles.** Steam, dust and puffs of dust are pure functions of `(t, seed,
// index)` with small integer hashes. No `Math.random` (rule 3.1), no fractional movement (rule
// 2.3), no state carried across frames, otherwise the same second of a run would show a
// different room while rewinding.

import type { Ctx, Pt, Verdict } from "../types.ts";
import { ART, TYPE_CPS } from "../const.ts";
import { mix } from "../ids.ts";
import { fill, fillA } from "./art.ts";
import type { Pal, PalKey } from "./palette.ts";

// ═══ The type ════════════════════════════════════════════════════════════════

/** Character height and spacing in buffer pixels.
 *
 *  Five rows are the minimum for a closed `8`. The width is **not** fixed: almost all glyphs
 *  are 3 pixels wide (the minimum in which `E`, `F` and `B` differ), and `M`, `N` and `W` are 4.
 *  The reason stands with them: at three pixels they are the same block, and "NENNWERT" could
 *  not be told from "MEMMWERT" in the golden image. Four pixels for three letters cost two
 *  pixels of width in a typical name; illegible type costs the whole feature. */
const GH = 5;
/** Spacing between two glyphs. */
const GAP = 1;
/** A space is narrower than a letter, otherwise short names fall apart into single words. */
const SPACE_ADV = 3;
/** 5 rows of glyph plus 2 rows of air. Those two rows are also the space the umlaut dots of the
 *  **next** line reach into. */
const LINE_H = 7;

/**
 * The glyphs. `#` is set, `.` is free.
 *
 * All letters stand as capitals; lower case is mapped onto them. At 3×5 there is no sensible
 * x-height: an "e" would be 3×3 and could no longer be separated from a "c" or an "o". At this
 * size an all caps face is the more readable lie.
 *
 * Six row glyphs (the umlauts) are aligned at the **foot** and stick out upwards.
 */
const GLYPHS: Record<string, readonly string[]> = {
  A: [".#.", "#.#", "###", "#.#", "#.#"],
  B: ["##.", "#.#", "##.", "#.#", "##."],
  C: [".##", "#..", "#..", "#..", ".##"],
  D: ["##.", "#.#", "#.#", "#.#", "##."],
  E: ["###", "#..", "##.", "#..", "###"],
  F: ["###", "#..", "##.", "#..", "#.."],
  G: [".##", "#..", "#.#", "#.#", ".##"],
  H: ["#.#", "#.#", "###", "#.#", "#.#"],
  I: ["###", ".#.", ".#.", ".#.", "###"],
  J: ["..#", "..#", "..#", "#.#", ".#."],
  K: ["#.#", "#.#", "##.", "#.#", "#.#"],
  L: ["#..", "#..", "#..", "#..", "###"],
  // The three four column ones. At three pixels `M`, `N` and `W` are all "two vertical strokes
  // with something in between": in the first golden image "NEIN" read as "MEIM" and "REVIEW"
  // ended in an `H`. With a fourth column `M` gets its middle peak, `N` a real diagonal and `W`
  // its heavy foot; only then are they three characters.
  M: ["#..#", "####", "####", "#..#", "#..#"],
  N: ["#..#", "##.#", "#.##", "#..#", "#..#"],
  O: [".#.", "#.#", "#.#", "#.#", ".#."],
  P: ["##.", "#.#", "##.", "#..", "#.."],
  Q: [".#.", "#.#", "#.#", "##.", ".##"],
  R: ["##.", "#.#", "##.", "#.#", "#.#"],
  S: [".##", "#..", ".#.", "..#", "##."],
  T: ["###", ".#.", ".#.", ".#.", ".#."],
  U: ["#.#", "#.#", "#.#", "#.#", "###"],
  // `V` tapers to a point (three columns are enough), `W` is four columns and heavy at the
  // bottom, exactly the mirror image of the top heavy `M`.
  V: ["#.#", "#.#", "#.#", ".#.", ".#."],
  W: ["#..#", "#..#", "#..#", "####", "####"],
  X: ["#.#", "#.#", ".#.", "#.#", "#.#"],
  Y: ["#.#", "#.#", ".#.", ".#.", ".#."],
  Z: ["###", "..#", ".#.", "#..", "###"],

  // Umlauts: a row of dots, an empty row, a four row base letter. The empty row is the whole
  // trick: without it the dots merge with the letter (with "Ü" into a very tall U).
  "Ä": ["#.#", "...", ".#.", "###", "#.#", "#.#"],
  "Ö": ["#.#", "...", "###", "#.#", "#.#", "###"],
  "Ü": ["#.#", "...", "#.#", "#.#", "#.#", "###"],
  "ß": ["##.", "#.#", "##.", "#.#", "##."],

  "0": ["###", "#.#", "#.#", "#.#", "###"],
  "1": [".#.", "##.", ".#.", ".#.", "###"],
  "2": ["##.", "..#", ".#.", "#..", "###"],
  "3": ["##.", "..#", ".#.", "..#", "##."],
  "4": ["#.#", "#.#", "###", "..#", "..#"],
  "5": ["###", "#..", "##.", "..#", "##."],
  "6": [".##", "#..", "###", "#.#", "###"],
  "7": ["###", "..#", ".#.", ".#.", ".#."],
  "8": ["###", "#.#", "###", "#.#", "###"],
  "9": ["###", "#.#", "###", "..#", "##."],

  ".": ["...", "...", "...", "...", ".#."],
  ",": ["...", "...", "...", ".#.", "#.."],
  ":": ["...", ".#.", "...", ".#.", "..."],
  ";": ["...", ".#.", "...", ".#.", "#.."],
  "!": [".#.", ".#.", ".#.", "...", ".#."],
  "?": ["##.", "..#", ".#.", "...", ".#."],
  "-": ["...", "...", "###", "...", "..."],
  "_": ["...", "...", "...", "...", "###"],
  "/": ["..#", "..#", ".#.", "#..", "#.."],
  "\\": ["#..", "#..", ".#.", "..#", "..#"],
  "(": ["..#", ".#.", ".#.", ".#.", "..#"],
  ")": ["#..", ".#.", ".#.", ".#.", "#.."],
  "[": [".##", ".#.", ".#.", ".#.", ".##"],
  "]": ["##.", ".#.", ".#.", ".#.", "##."],
  "<": ["..#", ".#.", "#..", ".#.", "..#"],
  ">": ["#..", ".#.", "..#", ".#.", "#.."],
  "+": ["...", ".#.", "###", ".#.", "..."],
  "=": ["...", "###", "...", "###", "..."],
  "*": ["#.#", ".#.", "#.#", "...", "..."],
  "#": ["#.#", "###", "#.#", "###", "#.#"],
  "%": ["#.#", "..#", ".#.", "#..", "#.#"],
  "$": [".#.", "###", "##.", "###", ".#."],
  "'": [".#.", ".#.", "...", "...", "..."],
  "\"": ["#.#", "#.#", "...", "...", "..."],
  "@": ["###", "#.#", "###", "#..", "###"],
  "&": ["##.", "##.", "###", "#.#", "###"],
  "|": [".#.", ".#.", ".#.", ".#.", ".#."],
  "~": ["...", "..#", "###", "#..", "..."],

  // The three characters the engine produces as `emote` text, small here so that they may also
  // appear in the middle of a bubble text. `emotePop` draws them large.
  "✓": ["..#", "..#", "#.#", "##.", ".#."],
  "✗": ["#.#", "#.#", ".#.", "#.#", "#.#"],
};

/** An unknown character. A visible box, not a silent omission: a gap in a name looks like a data
 *  error, a box like a missing glyph, and only the second leads to the right repair. */
const TOFU: readonly string[] = ["###", "#.#", "#.#", "#.#", "###"];

/**
 * Typographic characters mapped onto their ASCII counterpart, **always one to one**.
 *
 * That is not convenience but necessity: agent texts come from models and ticket fields and are
 * full of en dashes, ellipses and German quotation marks. Without this table a box would stand
 * in the middle of every second sentence; in the first golden image "Nein — die Zeile …" was
 * recognisable by exactly that.
 *
 * One to one, because the typewriter effect counts characters: if "…" became three dots, the
 * bubble would run out of step with the original text.
 */
const FOLD: Record<string, string> = {
  "—": "-", "–": "-", "‑": "-", "−": "-",
  "…": ".", "·": ".", "•": ".",
  "„": "\"", "“": "\"", "”": "\"", "«": "\"", "»": "\"",
  "‚": "'", "‘": "'", "’": "'", "´": "'", "`": "'",
  "→": ">", "←": "<", "×": "*", "≥": ">", "≤": "<",
  "\t": " ", "\n": " ", "\r": " ", " ": " ",
};

/** One character mapped onto what is drawn. The non breaking space lands on the ordinary one as
 *  well, otherwise it would stand in the text as a box. */
function fold(ch: string): string {
  return GLYPHS[ch] !== undefined ? ch : (FOLD[ch] ?? ch);
}

function glyphOf(ch: string): readonly string[] {
  const direct = GLYPHS[ch];
  if (direct !== undefined) return direct;
  // `toUpperCase` is locale independent (the Unicode standard mapping) and therefore
  // deterministic; `toLocaleUpperCase` would not be and is forbidden (rule 3.1).
  const up = GLYPHS[ch.toUpperCase()];
  return up !== undefined ? up : TOFU;
}

/** Advance of one character including the spacing. */
function advOf(ch: string): number {
  return ch === " " ? SPACE_ADV : glyphOf(ch)[0].length + GAP;
}

/** Width of a text in buffer pixels (without the trailing spacing). */
export function textW(text: string): number {
  let w = 0;
  for (let i = 0; i < text.length; i++) w += advOf(fold(text[i]));
  return w > 0 ? w - GAP : 0;
}

/**
 * Draws text. `x`/`y` are the top left corner of the **five row cell**; six row glyphs stick out
 * one row upwards, because they are aligned at the foot.
 *
 * Horizontal runs are merged: with a bubble of 40 characters that is, instead of
 * ~250 Einzelpixeln rund 80 `fillRect`.
 */
export function drawText(
  ctx: Ctx, pal: Pal, key: PalKey, x: number, y: number, text: string, alpha?: number,
): number {
  const a = alpha ?? 1;
  if (a <= 0) return 0;
  if (a < 1) ctx.globalAlpha = a;
  ctx.fillStyle = pal[key];

  let cx = x;
  for (let i = 0; i < text.length; i++) {
    const ch = fold(text[i]);
    if (ch === " ") { cx += SPACE_ADV; continue; }
    const rows = glyphOf(ch);
    const gw = rows[0].length;
    // Six row glyphs (umlauts) stick out upwards: alignment is at the foot.
    const top = y + GH - rows.length;
    for (let r = 0; r < rows.length; r++) {
      const row = rows[r];
      let run = 0;
      for (let c = 0; c <= gw; c++) {
        const on = c < gw && row[c] === "#";
        if (on) { run++; continue; }
        if (run > 0) { ctx.fillRect(cx + c - run, top + r, run, 1); run = 0; }
      }
    }
    cx += gw + GAP;
  }

  if (a < 1) ctx.globalAlpha = 1;
  return cx - x - GAP;
}

/** Wraps text to `maxW` buffer pixels. Words that are too long (paths!) are broken hard: a
 *  single `services/office.py` would otherwise burst every bubble. */
export function wrap(text: string, maxW: number): string[] {
  const out: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    let w = word;
    // Break hard as long as the word alone does not fit. The breaking point is measured and not
    // estimated: with mixed widths (3 and 4) an estimate would sometimes be off, and then a line
    // would stand past the edge of the bubble.
    while (textW(w) > maxW) {
      let fit = 1;
      while (fit < w.length && textW(w.slice(0, fit + 1)) <= maxW) fit++;
      if (line.length > 0) { out.push(line); line = ""; }
      out.push(w.slice(0, fit));
      w = w.slice(fit);
    }
    if (w.length === 0) continue;
    const merged = line.length === 0 ? w : line + " " + w;
    if (textW(merged) <= maxW) { line = merged; continue; }
    if (line.length > 0) out.push(line);
    line = w;
  }
  if (line.length > 0) out.push(line);
  return out.length > 0 ? out : [""];
}

// ═══ Blasen ══════════════════════════════════════════════════════════════════

/** Inner padding of the bubble (border plus one row of air). */
const PAD = 2;
/** Height of the tail under the bubble. */
const TAIL_H = 4;
/** Default maximum width of a bubble. A third of the buffer: wider and two neighbouring
 *  speakers cover each other, narrower and every sentence becomes six lines tall. */
const BUBBLE_MAX_W = 108;

const VERDICT_KEY: Record<"ok" | "err" | "blocked", PalKey> = {
  ok: "ok", err: "err", blocked: "blocked",
};

/** A box with rounded corners: the four corner pixels stay free. Two rectangles for the area,
 *  four for the border. A path would be the obvious thing here and is forbidden. */
function panel(
  ctx: Ctx, pal: Pal, x: number, y: number, w: number, h: number,
  face: PalKey, edge: PalKey, alpha: number,
): void {
  fillA(ctx, pal, face, alpha, x + 1, y + 1, w - 2, h - 2);
  fillA(ctx, pal, edge, alpha, x + 1, y, w - 2, 1);
  fillA(ctx, pal, edge, alpha, x + 1, y + h - 1, w - 2, 1);
  fillA(ctx, pal, edge, alpha, x, y + 1, 1, h - 2);
  fillA(ctx, pal, edge, alpha, x + w - 1, y + 1, 1, h - 2);
}

export interface SpeechOpts {
  /** 0..1, the progress of the typewriter effect. */
  reveal: number;
  /** Colours **only** the outline. The area stays paper: a rectangle flooded red reads as a
   *  broken display, not as a failed step. */
  verdict?: Verdict;
  maxW?: number;
}

/**
 * Speech bubble with a typewriter effect.
 *
 * The box is measured **once on the full text** and then fills up. Growing along would be the
 * obvious way and the wrong one: the box would twitch at every line break, and because a
 * character stands below it, the character would seem to wobble.
 *
 * `yBase` is the tip of the tail, so the point the bubble points at (the top of the head plus a
 * little air). The foot point rule applies here as well: the tail is the contact.
 */
export function speechBubble(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, text: string, o: SpeechOpts,
): void {
  const maxW = o.maxW ?? BUBBLE_MAX_W;
  const lines = wrap(text, maxW);
  let inner = 0;
  for (const l of lines) inner = Math.max(inner, textW(l));

  const w = inner + 2 * PAD + 2;
  const h = GH + (lines.length - 1) * LINE_H + 2 * PAD + 2;
  const y0 = yBase - TAIL_H - h;
  // At the edge of the image the bubble slides inwards instead of being cut off. The tail stays
  // where it is: it points at the speaker, not at the bubble.
  const x0 = Math.max(1, Math.min(ART.w - w - 1, cx - (w >> 1)));

  const edge: PalKey = o.verdict ? VERDICT_KEY[o.verdict] : "ink";
  panel(ctx, pal, x0, y0, w, h, "paper", edge, 1);

  // Tail: three rows of paper with border pixels, then the tip. It is drawn **after** the box
  // and overwrites its lower border row, otherwise the bubble would have a floor the tail would
  // stick through.
  for (let i = 0; i < 3; i++) {
    const half = 2 - i;
    const y = y0 + h - 1 + i;
    fill(ctx, pal, "paper", cx - half, y, half * 2 + 1, 1);
    fill(ctx, pal, edge, cx - half - 1, y, 1, 1);
    fill(ctx, pal, edge, cx + half + 1, y, 1, 1);
  }
  fill(ctx, pal, edge, cx, y0 + h + 2, 1, 1);

  // Typewriter: counted across all lines so that the line break does not change the speed. The
  // separating space counts as well, otherwise the text would run one stroke ahead after every
  // break.
  const total = text.length;
  let shown = Math.round(Math.max(0, Math.min(1, o.reveal)) * total);
  let ty = y0 + PAD + 1;
  for (const line of lines) {
    if (shown <= 0) break;
    const part = shown >= line.length ? line : line.slice(0, shown);
    drawText(ctx, pal, "ink", x0 + PAD + 1, ty, part);
    shown -= line.length + 1;
    ty += LINE_H;
  }
}

/** Characters per second turned into progress. One line, but it belongs here: the stage and the
 *  bubble have to agree on `TYPE_CPS`, otherwise one writes faster than the other. */
export function revealOf(text: string, elapsedMs: number): number {
  if (text.length === 0) return 1;
  return Math.max(0, Math.min(1, (elapsedMs / 1000) * TYPE_CPS / text.length));
}

/**
 * Thought bubble. A cloud box with trailing puffs and three dots.
 *
 * The dots come from `t` and the box is measured with **all three** of them: a bubble growing
 * wider at the beat of the dots flickers just as annoyingly as a growing speech bubble.
 */
export function thoughtBubble(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, text: string, t: number, maxW?: number,
): void {
  const lim = maxW ?? BUBBLE_MAX_W;
  const lines = wrap(text + "...", lim);
  let inner = 0;
  for (const l of lines) inner = Math.max(inner, textW(l));

  const w = inner + 2 * PAD + 2;
  const h = GH + (lines.length - 1) * LINE_H + 2 * PAD + 2;
  const y0 = yBase - 9 - h;
  const x0 = Math.max(1, Math.min(ART.w - w - 1, cx - (w >> 1)));

  panel(ctx, pal, x0, y0, w, h, "paper", "ink", 1);

  // Puffs at the top edge: three bulges, unequally wide and unequally spread. Placed evenly they
  // would read as a decorative strip, not as a cloud. Two rows high, because a single one
  // disappears next to the 1 pixel border of the box.
  const bumps: readonly (readonly [number, number])[] = [
    [3, 5], [Math.max(5, (w >> 1) - 3), 7], [Math.max(9, w - 11), 4],
  ];
  for (const [ox, ow] of bumps) {
    fill(ctx, pal, "paper", x0 + ox, y0 - 2, ow, 3);
    fill(ctx, pal, "ink", x0 + ox, y0 - 3, ow, 1);
    fill(ctx, pal, "ink", x0 + ox - 1, y0 - 2, 1, 2);
    fill(ctx, pal, "ink", x0 + ox + ow, y0 - 2, 1, 2);
  }
  // One bulge on each flank: only then is the silhouette restless all round and the box stops
  // being a box.
  fill(ctx, pal, "paper", x0 - 1, y0 + 3, 2, 4);
  fill(ctx, pal, "ink", x0 - 2, y0 + 3, 1, 4);
  fill(ctx, pal, "paper", x0 + w - 1, y0 + h - 8, 2, 4);
  fill(ctx, pal, "ink", x0 + w + 1, y0 + h - 8, 1, 4);

  // Two trailing puffs instead of a tail: that is the whole difference between "says" and
  // "thinks", and it has to be visible from three metres away as well.
  panel(ctx, pal, cx - 3, y0 + h, 6, 4, "paper", "ink", 1);
  panel(ctx, pal, cx - 1, y0 + h + 5, 3, 3, "paper", "ink", 1);

  const dots = 1 + (((t / 420) | 0) % 3);
  const shown = text + "...".slice(0, dots);
  let ty = y0 + PAD + 1;
  let left = shown.length;
  for (const line of lines) {
    if (left <= 0) break;
    drawText(ctx, pal, "ink", x0 + PAD + 1, ty, left >= line.length ? line : line.slice(0, left));
    left -= line.length + 1;
    ty += LINE_H;
  }
}

// ═══ Namensschild ════════════════════════════════════════════════════════════

export interface PlateOpts {
  /** Zweite Zeile, kleiner gesetzt (Ticket, Modell). */
  sub?: string;
  selected?: boolean;
  dim?: boolean;
}

/**
 * The plate under a character. `yBase` is its lower edge.
 *
 * A dark area instead of a light one: it lies on the plank floor, and a light plate on light
 * wood needs a border to be read at all. The border in turn is the only black line in the whole
 * room in the day picture and would then draw all the attention.
 */
export function nameplate(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, name: string, o?: PlateOpts,
): void {
  const sub = o?.sub;
  const wName = textW(name);
  const wSub = sub ? textW(sub) : 0;
  const w = Math.max(wName, wSub) + 6;
  const h = sub ? GH * 2 + 7 : GH + 4;
  const x0 = Math.max(1, Math.min(ART.w - w - 1, cx - (w >> 1)));
  const y0 = yBase - h;

  const dim = o?.dim === true;
  const sel = o?.selected === true;
  const alpha = dim ? 0.40 : sel ? 0.92 : 0.72;

  panel(ctx, pal, x0, y0, w, h, "screen", sel ? "acc" : "ink", alpha);
  drawText(ctx, pal, "paper", x0 + 3, y0 + 2, name, dim ? 0.65 : 1);
  if (sub) drawText(ctx, pal, "metal", x0 + 3, y0 + 2 + LINE_H, sub, dim ? 0.55 : 0.9);
}

// ═══ Emote ═══════════════════════════════════════════════════════════════════
//
// Instead of separate reaction poses (cheering, frustration). The reasoning stands in the plan
// and holds up: at 16×24 a cheering pose cannot be told from a stretch, while a tick above the
// head is unambiguous at 480×270 from any distance, and it costs three pieces of art instead of
// twelve times two.

const EMOTE_OK: readonly string[] = [
  "......#",
  ".....##",
  "....##.",
  "#...##.",
  "##.##..",
  ".####..",
  "..##...",
];

const EMOTE_ERR: readonly string[] = [
  "##...##",
  "###.###",
  ".#####.",
  "..###..",
  ".#####.",
  "###.###",
  "##...##",
];

const EMOTE_BANG: readonly string[] = [
  "..###..",
  "..###..",
  "..###..",
  "..###..",
  "...#...",
  ".......",
  "..###..",
];

export type EmoteGlyph = "✓" | "✗" | "!";

const EMOTE_ART: Record<EmoteGlyph, readonly string[]> = {
  "✓": EMOTE_OK, "✗": EMOTE_ERR, "!": EMOTE_BANG,
};

const EMOTE_COL: Record<EmoteGlyph, PalKey> = {
  "✓": "ok", "✗": "err", "!": "blocked",
};

/**
 * The pop above the head. `age` 0..1 is the progress of the effect (`(t - t0)/(until - t0)`),
 * **not** a time in ms: layer 1 does not know the duration of the effect and should not know
 * it; that stands in `const.ts` and is worked out by the engine into `Fx.until`.
 */
export function emotePop(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, glyph: EmoteGlyph, age: number,
): void {
  const a = Math.max(0, Math.min(1, age));
  // Rises six pixels and fades in the last quarter. Fading earlier would take away exactly the
  // time in which the sign is read.
  const rise = Math.round(a * 6);
  const alpha = a > 0.75 ? Math.max(0, (1 - a) / 0.25) : 1;
  const rows = EMOTE_ART[glyph];
  const x0 = cx - 3;
  const y0 = yBase - rise - rows.length;

  // A bright backing: the sign has to be readable in front of a dark wall as well as in front of a
  // bright monitor. The corners stay free so the mark looks round.
  fillA(ctx, pal, "paper", alpha * 0.92, x0 - 1, y0, 9, rows.length);
  fillA(ctx, pal, "paper", alpha * 0.92, x0, y0 - 1, 7, rows.length + 2);

  const col = EMOTE_COL[glyph];
  if (alpha < 1) ctx.globalAlpha = alpha;
  ctx.fillStyle = pal[col];
  for (let r = 0; r < rows.length; r++) {
    const row = rows[r];
    let run = 0;
    for (let c = 0; c <= row.length; c++) {
      const on = c < row.length && row[c] === "#";
      if (on) { run++; continue; }
      if (run > 0) { ctx.fillRect(x0 + c - run, y0 + r, run, 1); run = 0; }
    }
  }
  if (alpha < 1) ctx.globalAlpha = 1;
}

// ═══ Linien ══════════════════════════════════════════════════════════════════

/**
 * The spawn or handover line between two points (both in **buffer pixels**, not in scene
 * coordinates: `scene.ts` does the conversion).
 *
 * Dashed and travelling: a solid line says "these two belong together", a travelling one also
 * says **in which direction** the delegation went. That is the whole reason the line exists.
 *
 * Bresenham by hand, because there is no `lineTo`, and because a rasterised line is exactly
 * what belongs here anyway.
 */
export function linkLine(ctx: Ctx, from: Pt, to: Pt, pal: Pal, age: number): void {
  const a = Math.max(0, Math.min(1, age));
  const alpha = 0.75 * (1 - a) + 0.15;

  let x = Math.round(from.x);
  let y = Math.round(from.y);
  const x1 = Math.round(to.x);
  const y1 = Math.round(to.y);
  const dx = Math.abs(x1 - x);
  const dy = -Math.abs(y1 - y);
  const sx = x < x1 ? 1 : -1;
  const sy = y < y1 ? 1 : -1;
  let err = dx + dy;

  // Pattern: two pixels on, three off, shifted by `phase`. The shift travels with `age` from
  // the source to the target.
  const shift = Math.round(a * 20);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = pal.acc;
  for (let i = 0; i < 400; i++) {
    if ((i + shift) % 5 < 2) ctx.fillRect(x, y, 1, 1);
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x += sx; }
    if (e2 <= dx) { err += dx; y += sy; }
  }
  ctx.globalAlpha = 1;
}

// ═══ Partikel ════════════════════════════════════════════════════════════════
//
// All three are pure functions of `(t, seed, index)`. The index replaces the state: instead of
// carrying twelve grains of dust forward, the position of every grain at time `t` is computed
// from its index. That makes the image producible from any moment, exactly the property
// rewinding hangs on.

const SALT_STEAM = 0x44414d50; // "DAMP"
const SALT_DUST = 0x53544142;  // "STAB"
const SALT_PUFF = 0x50554646;  // "PUFF"

/** A triangle wave 0..n..0 over the period `p`. Integer, so that nothing lands on half
 *  Pixeln landet (Regel 2.3). */
function tri(v: number, p: number, n: number): number {
  const m = ((v % (p * 2)) + p * 2) % (p * 2);
  const up = m < p ? m : p * 2 - m;
  return Math.round((up * n) / p);
}

/** Steam above the coffee machine. Three wisps, at different speeds. */
export function steam(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, t: number, seed: number,
): void {
  for (let i = 0; i < 3; i++) {
    const h = mix(seed + i * 977, SALT_STEAM);
    const speed = 90 + (h % 60);
    const span = 10 + (h % 5);
    const step = (((t / speed) | 0) + (h % span)) % span;
    const y = yBase - step;
    const x = cx - 2 + i * 2 + tri(step + (h % 3), 3, 1);
    // Paler at the top: the steam dissolves instead of ending at an edge.
    fillA(ctx, pal, "wallHi", 0.35 * (1 - step / span) + 0.05, x, y, 1, 1);
  }
}

/** Floating dust in the whole room. No position argument: the effect belongs to the air, not to
 *  a piece of furniture. */
export function dust(ctx: Ctx, pal: Pal, t: number, seed: number): void {
  const n = 14;
  for (let i = 0; i < n; i++) {
    const h = mix(seed + i * 7919, SALT_DUST);
    const speed = 220 + (h % 260);
    const drift = ((t / speed) | 0) + (h % ART.w);
    const x = drift % ART.w;
    const y = 44 + ((h >>> 9) % 190) + tri(((t / 700) | 0) + i, 9, 3) - 1;
    fillA(ctx, pal, "wallHi", 0.13, x, y, 1, 1);
  }
}

/** The puff under a landing foot. `age` 0..1. */
export function footPuff(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, age: number,
): void {
  const a = Math.max(0, Math.min(1, age));
  const alpha = 0.30 * (1 - a);
  if (alpha <= 0.02) return;
  const spread = 1 + Math.round(a * 3);
  for (let i = 0; i < 3; i++) {
    const h = mix(i, SALT_PUFF);
    const dx = (i - 1) * spread + (h % 2);
    fillA(ctx, pal, "wallHi", alpha, cx + dx, yBase - 1 - ((h >>> 3) % 2), 1, 1);
  }
}

/** A tool spark at the workplace, the short flash when a tool starts up. */
export function spark(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, age: number, seed: number,
): void {
  const a = Math.max(0, Math.min(1, age));
  const alpha = Math.max(0, 1 - a) * 0.8;
  if (alpha <= 0.02) return;
  for (let i = 0; i < 3; i++) {
    const h = mix(seed + i * 131, SALT_PUFF);
    const dx = (h % 7) - 3;
    const dy = Math.round(a * 5) + ((h >>> 5) % 3);
    fillA(ctx, pal, "acc", alpha, cx + dx, yBase - dy, 1, 1);
  }
}
