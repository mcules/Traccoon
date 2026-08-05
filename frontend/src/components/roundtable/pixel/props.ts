// Schicht 1 — alles, was über den Figuren liegt: Schrift, Blasen, Schilder, Luft.
//
// Zwei Dinge machen diese Datei aus.
//
// **Erstens die Schrift.** `fillText` ist verboten (Regel 2.1), und das aus einem harten Grund:
// derselbe Text ergibt je Plattform andere Pixel — mit einer Systemschrift wären die goldenen
// Pixel-Hashes des Prüfers auf jedem Rechner andere. Schrift ist deshalb ein Art, kein Font:
// 3×5 Pixel je Zeichen, von Hand gesetzt. Das ist der größte Einzelposten der Kunstquelle und
// zugleich der, der am meisten trägt — ohne lesbare Namen ist der Raum eine Zierde, mit ihnen
// eine Ansicht.
//
// Die deutschen Umlaute sind **nicht** optional: Agenten heißen `gedaechtnis_suchen`, Tickets
// tragen Titel wie „Prüfbericht", und ein fehlendes „ü" fällt im Bild sofort als Loch auf.
// Sie sind sechs Zeilen hoch statt fünf und ragen um eine Zeile in den Zeilenabstand hinein —
// ein Umlaut, der in fünf Zeilen gequetscht wird, ist von seinem Grundbuchstaben nicht mehr
// zu unterscheiden, und dann kann man ihn auch weglassen.
//
// **Zweitens die Partikel.** Dampf, Staub und Staubwölkchen sind reine Funktionen von
// `(t, seed, index)` mit kleinen ganzzahligen Hashes. Kein `Math.random` (Regel 3.1), keine
// Bruchteilbewegung (Regel 2.3), kein über Bilder fortgeschriebener Zustand — sonst zeigte
// dieselbe Sekunde des Laufs beim Zurückspulen einen anderen Raum.

import type { Ctx, Pt, Verdict } from "../types.ts";
import { PIX, TYPE_CPS } from "../const.ts";
import { mix } from "../ids.ts";
import { fill, fillA } from "./art.ts";
import type { Pal, PalKey } from "./palette.ts";

// ═══ Die Schrift ═════════════════════════════════════════════════════════════

/** Zeichenhöhe und Zeichenabstand in Pufferpixeln.
 *
 *  5 Zeilen sind das Minimum für eine geschlossene `8`. Die Breite ist **nicht** fest: fast
 *  alle Glyphen sind 3 Pixel breit (das Minimum, in dem sich `E`, `F` und `B` unterscheiden),
 *  `M`, `N` und `W` sind 4. Der Grund steht bei ihnen — auf drei Pixeln sind sie derselbe
 *  Klotz, und „NENNWERT" war im Prüfbild nicht von „MEMMWERT" zu unterscheiden. Vier Pixel für
 *  drei Buchstaben kosten in einem typischen Namen zwei Pixel Breite; eine unlesbare Schrift
 *  kostet das ganze Feature. */
const GH = 5;
/** Zwischenraum zwischen zwei Glyphen. */
const GAP = 1;
/** Leerzeichen ist schmaler als ein Buchstabe — sonst zerfallen kurze Namen in Einzelwörter. */
const SPACE_ADV = 3;
/** 5 Zeilen Glyph + 2 Zeilen Luft. Die zwei Zeilen sind auch der Platz, in den die Umlautpunkte
 *  der **nächsten** Zeile hineinragen. */
const LINE_H = 7;

/**
 * Die Glyphen. `#` = gesetzt, `.` = frei.
 *
 * Alle Buchstaben stehen als Versalien da; Kleinbuchstaben werden darauf abgebildet. Bei 3×5
 * gibt es keine sinnvolle x-Höhe — ein „e" wäre 3×3 groß und von einem „c" oder „o" nicht mehr
 * zu trennen. Eine Versalschrift ist bei dieser Größe die lesbarere Lüge.
 *
 * Sechszeilige Glyphen (die Umlaute) werden am **Fuß** ausgerichtet und ragen nach oben heraus.
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
  // Die drei Vierspaltigen. `M`, `N` und `W` sind bei drei Pixeln alle „zwei senkrechte
  // Striche mit etwas dazwischen" — im ersten Prüfbild las sich „NEIN" als „MEIM" und
  // „REVIEW" endete auf einem `H`. Mit einer vierten Spalte bekommt `M` seine Mittelspitze,
  // `N` eine echte Diagonale und `W` sein schweres Fußende; erst dann sind sie drei Zeichen.
  M: ["#..#", "####", "####", "#..#", "#..#"],
  N: ["#..#", "##.#", "#.##", "#..#", "#..#"],
  O: [".#.", "#.#", "#.#", "#.#", ".#."],
  P: ["##.", "#.#", "##.", "#..", "#.."],
  Q: [".#.", "#.#", "#.#", "##.", ".##"],
  R: ["##.", "#.#", "##.", "#.#", "#.#"],
  S: [".##", "#..", ".#.", "..#", "##."],
  T: ["###", ".#.", ".#.", ".#.", ".#."],
  U: ["#.#", "#.#", "#.#", "#.#", "###"],
  // `V` läuft spitz zu (drei Spalten genügen), `W` ist vierspaltig und unten schwer — genau
  // spiegelbildlich zum oben schweren `M`.
  V: ["#.#", "#.#", "#.#", ".#.", ".#."],
  W: ["#..#", "#..#", "#..#", "####", "####"],
  X: ["#.#", "#.#", ".#.", "#.#", "#.#"],
  Y: ["#.#", "#.#", ".#.", ".#.", ".#."],
  Z: ["###", "..#", ".#.", "#..", "###"],

  // Umlaute: Punktzeile, Leerzeile, vierzeiliger Grundbuchstabe. Die Leerzeile ist der ganze
  // Trick — ohne sie verschmelzen die Punkte mit dem Buchstaben (bei „Ü" zu einem sehr hohen U).
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

  // Die drei Zeichen, die die Engine als `emote`-Text erzeugt — hier klein, damit sie auch
  // mitten in einem Blasentext auftauchen dürfen. Groß zeichnet sie `emotePop`.
  "✓": ["..#", "..#", "#.#", "##.", ".#."],
  "✗": ["#.#", "#.#", ".#.", "#.#", "#.#"],
};

/** Unbekanntes Zeichen. Ein sichtbarer Kasten, kein stilles Weglassen: eine Lücke im Namen
 *  sieht nach einem Datenfehler aus, ein Kasten nach einem fehlenden Glyph — und nur das
 *  Zweite führt zur richtigen Reparatur. */
const TOFU: readonly string[] = ["###", "#.#", "#.#", "#.#", "###"];

/**
 * Typografische Zeichen auf ihr ASCII-Gegenstück, **immer eins zu eins**.
 *
 * Das ist nicht Bequemlichkeit, sondern Notwendigkeit: Agententexte kommen aus Modellen und
 * Ticketfeldern und sind voller Halbgeviertstriche, Auslassungspunkte und deutscher
 * Anführungszeichen. Ohne diese Tabelle stünde mitten in jedem zweiten Satz ein Kasten — im
 * ersten Prüfbild war „Nein — die Zeile …" genau daran zu erkennen.
 *
 * Eins zu eins, weil der Schreibmaschineneffekt Zeichen zählt: würde „…" zu drei Punkten,
 * liefe die Blase gegenüber dem Originaltext aus dem Takt.
 */
const FOLD: Record<string, string> = {
  "—": "-", "–": "-", "‑": "-", "−": "-",
  "…": ".", "·": ".", "•": ".",
  "„": "\"", "“": "\"", "”": "\"", "«": "\"", "»": "\"",
  "‚": "'", "‘": "'", "’": "'", "´": "'", "`": "'",
  "→": ">", "←": "<", "×": "*", "≥": ">", "≤": "<",
  "\t": " ", "\n": " ", "\r": " ", " ": " ",
};

/** Ein Zeichen auf das, was gezeichnet wird. Auch der geschützte Zwischenraum landet hier
 *  auf dem gewöhnlichen — sonst stünde er als Kasten im Text. */
function fold(ch: string): string {
  return GLYPHS[ch] !== undefined ? ch : (FOLD[ch] ?? ch);
}

function glyphOf(ch: string): readonly string[] {
  const direct = GLYPHS[ch];
  if (direct !== undefined) return direct;
  // `toUpperCase` ist gebietsschema-unabhängig (Unicode-Standardabbildung) und damit
  // deterministisch — `toLocaleUpperCase` wäre es nicht und ist verboten (Regel 3.1).
  const up = GLYPHS[ch.toUpperCase()];
  return up !== undefined ? up : TOFU;
}

/** Vorschub eines Zeichens einschließlich Zwischenraum. */
function advOf(ch: string): number {
  return ch === " " ? SPACE_ADV : glyphOf(ch)[0].length + GAP;
}

/** Breite eines Textes in Pufferpixeln (ohne den nachlaufenden Zwischenraum). */
export function textW(text: string): number {
  let w = 0;
  for (let i = 0; i < text.length; i++) w += advOf(fold(text[i]));
  return w > 0 ? w - GAP : 0;
}

/**
 * Zeichnet Text. `x`/`y` sind linke obere Ecke der **Fünfzeilen-Zelle**; sechszeilige Glyphen
 * ragen eine Zeile nach oben heraus, weil sie am Fuß ausgerichtet werden.
 *
 * Waagerechte Läufe werden zusammengefasst — bei einer Blase mit 40 Zeichen sind das statt
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
    // Sechszeilige Glyphen (Umlaute) ragen nach oben heraus: ausgerichtet wird am Fuß.
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

/** Bricht Text auf `maxW` Pufferpixel um. Zu lange Wörter (Pfade!) werden hart getrennt —
 *  ein einzelnes `services/roundtable.py` sprengte sonst jede Blase. */
export function wrap(text: string, maxW: number): string[] {
  const out: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    let w = word;
    // Hart trennen, solange das Wort allein nicht passt. Die Trennstelle wird gemessen und
    // nicht geschätzt — bei gemischten Breiten (3 und 4) läge eine Schätzung mal daneben,
    // und dann stünde eine Zeile über den Blasenrand hinaus.
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

/** Innenabstand der Blase (Rand + eine Zeile Luft). */
const PAD = 2;
/** Höhe des Zipfels unter der Blase. */
const TAIL_H = 4;
/** Voreingestellte Höchstbreite einer Blase. Ein Drittel des Puffers: breiter und zwei
 *  benachbarte Sprecher überdecken sich, schmaler und jeder Satz wird sechs Zeilen hoch. */
const BUBBLE_MAX_W = 108;

const VERDICT_KEY: Record<"ok" | "err" | "blocked", PalKey> = {
  ok: "ok", err: "err", blocked: "blocked",
};

/** Kasten mit abgerundeten Ecken: die vier Eckpixel bleiben frei. Zwei Rechtecke für die
 *  Fläche, vier für den Rand — ein Pfad wäre hier das Naheliegende und ist verboten. */
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
  /** 0..1 — Fortschritt des Schreibmaschineneffekts. */
  reveal: number;
  /** Färbt **nur** den Umriss. Die Fläche bleibt Papier: ein rot geflutetes Rechteck liest
   *  sich als kaputte Anzeige, nicht als gescheiterter Schritt. */
  verdict?: Verdict;
  maxW?: number;
}

/**
 * Sprechblase mit Schreibmaschineneffekt.
 *
 * Die Box wird **einmal am vollen Text** gemessen und füllt sich dann. Mitwachsen wäre der
 * naheliegende Weg und der falsche: der Kasten zuckte bei jedem Zeilenumbruch, und weil unter
 * ihm eine Figur steht, wackelte scheinbar die Figur.
 *
 * `yBase` ist die Spitze des Zipfels — also der Punkt, auf den die Blase zeigt (Kopfoberkante
 * plus ein wenig Luft). Auch hier gilt die Fußpunkt-Regel: der Zipfel ist der Kontakt.
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
  // Am Bildrand rutscht die Blase herein, statt abgeschnitten zu werden. Der Zipfel bleibt,
  // wo er ist — er zeigt auf den Sprecher, nicht auf die Blase.
  const x0 = Math.max(1, Math.min(PIX.w - w - 1, cx - (w >> 1)));

  const edge: PalKey = o.verdict ? VERDICT_KEY[o.verdict] : "ink";
  panel(ctx, pal, x0, y0, w, h, "paper", edge, 1);

  // Zipfel: drei Papierzeilen mit Randpixeln, dann die Spitze. Er wird **nach** dem Kasten
  // gezeichnet und überschreibt dessen untere Randzeile — sonst hätte die Blase einen Boden,
  // durch den der Zipfel hindurchstieße.
  for (let i = 0; i < 3; i++) {
    const half = 2 - i;
    const y = y0 + h - 1 + i;
    fill(ctx, pal, "paper", cx - half, y, half * 2 + 1, 1);
    fill(ctx, pal, edge, cx - half - 1, y, 1, 1);
    fill(ctx, pal, edge, cx + half + 1, y, 1, 1);
  }
  fill(ctx, pal, edge, cx, y0 + h + 2, 1, 1);

  // Schreibmaschine: über alle Zeilen hinweg gezählt, damit der Umbruch die Geschwindigkeit
  // nicht ändert. Der getrennte Zwischenraum zählt mit — sonst liefe der Text nach jedem
  // Umbruch einen Anschlag vor.
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

/** Zeichen je Sekunde → Fortschritt. Eine Zeile, aber sie gehört hierher: Bühne und Blase
 *  müssen sich über `TYPE_CPS` einig sein, sonst schreibt die eine schneller als die andere. */
export function revealOf(text: string, elapsedMs: number): number {
  if (text.length === 0) return 1;
  return Math.max(0, Math.min(1, (elapsedMs / 1000) * TYPE_CPS / text.length));
}

/**
 * Denkblase. Wolkenkasten mit nachlaufenden Wölkchen und drei Punkten.
 *
 * Die Punkte kommen aus `t` und der Kasten wird mit **allen dreien** gemessen: eine Blase, die
 * im Takt der Punkte breiter wird, flackert genau so störend wie eine mitwachsende Sprechblase.
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
  const x0 = Math.max(1, Math.min(PIX.w - w - 1, cx - (w >> 1)));

  panel(ctx, pal, x0, y0, w, h, "paper", "ink", 1);

  // Wölkchen an der Oberkante: drei Ausbuchtungen, ungleich breit und ungleich verteilt.
  // Gleichmäßig gesetzt läsen sie sich als Zierleiste, nicht als Wolke. Zwei Zeilen hoch,
  // denn eine einzige verschwindet neben dem 1 Pixel starken Rand des Kastens.
  const bumps: readonly (readonly [number, number])[] = [
    [3, 5], [Math.max(5, (w >> 1) - 3), 7], [Math.max(9, w - 11), 4],
  ];
  for (const [ox, ow] of bumps) {
    fill(ctx, pal, "paper", x0 + ox, y0 - 2, ow, 3);
    fill(ctx, pal, "ink", x0 + ox, y0 - 3, ow, 1);
    fill(ctx, pal, "ink", x0 + ox - 1, y0 - 2, 1, 2);
    fill(ctx, pal, "ink", x0 + ox + ow, y0 - 2, 1, 2);
  }
  // Je eine Ausbuchtung an den Flanken — erst damit ist die Silhouette rundum unruhig und
  // der Kasten hört auf, ein Kasten zu sein.
  fill(ctx, pal, "paper", x0 - 1, y0 + 3, 2, 4);
  fill(ctx, pal, "ink", x0 - 2, y0 + 3, 1, 4);
  fill(ctx, pal, "paper", x0 + w - 1, y0 + h - 8, 2, 4);
  fill(ctx, pal, "ink", x0 + w + 1, y0 + h - 8, 1, 4);

  // Zwei nachlaufende Wölkchen statt eines Zipfels — das ist der ganze Unterschied zwischen
  // „sagt" und „denkt", und er muss auch aus drei Metern Abstand zu sehen sein.
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
 * Das Schild unter einer Figur. `yBase` ist seine Unterkante.
 *
 * Dunkle Fläche statt heller: es liegt auf dem Dielenboden, und ein helles Schild auf hellem
 * Holz braucht einen Rand, um überhaupt gelesen zu werden — der Rand wiederum ist im Tagbild
 * die einzige schwarze Linie im ganzen Raum und zieht dann alle Aufmerksamkeit auf sich.
 */
export function nameplate(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, name: string, o?: PlateOpts,
): void {
  const sub = o?.sub;
  const wName = textW(name);
  const wSub = sub ? textW(sub) : 0;
  const w = Math.max(wName, wSub) + 6;
  const h = sub ? GH * 2 + 7 : GH + 4;
  const x0 = Math.max(1, Math.min(PIX.w - w - 1, cx - (w >> 1)));
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
// Ersetzt Roundtables Reaktionsposen `cheer`/`slump`. Begründung steht im Plan und hält der
// Prüfung stand: eine Jubelpose ist bei 16×24 von einer Streckübung nicht zu unterscheiden,
// ein Häkchen über dem Kopf dagegen auf 480×270 aus jeder Entfernung eindeutig — und es kostet
// drei Arts statt zwölf mal zwei.

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
 * Der Pop über dem Kopf. `age` 0..1 ist der Fortschritt des Effekts (`(t - t0)/(until - t0)`),
 * **nicht** eine Zeit in ms — Schicht 1 kennt die Dauer des Effekts nicht und soll sie nicht
 * kennen; sie steht in `const.ts` und wird von der Engine in `Fx.until` verrechnet.
 */
export function emotePop(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, glyph: EmoteGlyph, age: number,
): void {
  const a = Math.max(0, Math.min(1, age));
  // Steigt sechs Pixel und verblasst im letzten Viertel. Vorher zu verblassen nähme dem
  // Zeichen genau die Zeit, in der man es liest.
  const rise = Math.round(a * 6);
  const alpha = a > 0.75 ? Math.max(0, (1 - a) / 0.25) : 1;
  const rows = EMOTE_ART[glyph];
  const x0 = cx - 3;
  const y0 = yBase - rise - rows.length;

  // Helle Unterlage: das Zeichen muss vor einer dunklen Wand ebenso lesbar sein wie vor einem
  // hellen Monitor. Ecken bleiben frei, damit die Marke rund wirkt.
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
 * Die Spawn-/Übergabelinie zwischen zwei Punkten (beide in **Pufferpixeln**, nicht in
 * Szenenkoordinaten — die Umrechnung macht `scene.ts`).
 *
 * Gestrichelt und wandernd: eine durchgezogene Linie sagt „diese zwei gehören zusammen", eine
 * wandernde sagt zusätzlich, **in welche Richtung** delegiert wurde. Das ist der ganze Grund,
 * warum es die Linie gibt.
 *
 * Bresenham von Hand, weil es kein `lineTo` gibt — und weil eine gerasterte Linie ohnehin
 * genau das ist, was hier hingehört.
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

  // Muster: zwei Pixel an, drei aus, um `phase` verschoben. Die Verschiebung läuft mit `age`
  // von der Quelle zum Ziel.
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
// Alle drei sind reine Funktionen von `(t, seed, index)`. Der Index ersetzt den Zustand: statt
// zwölf Staubkörner fortzuschreiben, wird für jedes Korn aus seinem Index ausgerechnet, wo es
// zum Zeitpunkt `t` steht. Damit ist das Bild aus jedem Zeitpunkt heraus herstellbar — genau
// die Eigenschaft, an der das Zurückspulen hängt.

const SALT_STEAM = 0x44414d50; // "DAMP"
const SALT_DUST = 0x53544142;  // "STAB"
const SALT_PUFF = 0x50554646;  // "PUFF"

/** Dreieckschwingung 0..n..0 über die Periode `p`. Ganzzahlig, damit nichts auf halben
 *  Pixeln landet (Regel 2.3). */
function tri(v: number, p: number, n: number): number {
  const m = ((v % (p * 2)) + p * 2) % (p * 2);
  const up = m < p ? m : p * 2 - m;
  return Math.round((up * n) / p);
}

/** Dampf über der Kaffeemaschine. Drei Fähnchen, verschieden schnell. */
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
    // Oben blasser: der Dampf löst sich auf, statt an einer Kante zu enden.
    fillA(ctx, pal, "wallHi", 0.35 * (1 - step / span) + 0.05, x, y, 1, 1);
  }
}

/** Schwebender Staub im ganzen Raum. Kein Ortsargument: der Effekt gehört der Luft, nicht
 *  einem Möbel. */
export function dust(ctx: Ctx, pal: Pal, t: number, seed: number): void {
  const n = 14;
  for (let i = 0; i < n; i++) {
    const h = mix(seed + i * 7919, SALT_DUST);
    const speed = 220 + (h % 260);
    const drift = ((t / speed) | 0) + (h % PIX.w);
    const x = drift % PIX.w;
    const y = 44 + ((h >>> 9) % 190) + tri(((t / 700) | 0) + i, 9, 3) - 1;
    fillA(ctx, pal, "wallHi", 0.13, x, y, 1, 1);
  }
}

/** Das Wölkchen unter einem aufsetzenden Fuß. `age` 0..1. */
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

/** Werkzeugfunke am Arbeitsplatz — der kurze Blitz, wenn ein Werkzeug anläuft. */
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
