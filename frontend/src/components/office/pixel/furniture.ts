// Schicht 1 — das Mobiliar und die Kulisse.
//
// Maßstab (Regel 1 des Pixel-Vertrags, die Regel, an der man scheitert): der Puffer ist
// 480×270, `POS_SCALE = 0.3` gilt **nur für Positionen**. Eine Figur ist 16×24 **Pufferpixel**.
// Alles hier ist an dieser Figur gemessen und nicht an Szenenkoordinaten:
//
//   Figur          16 × 24     — der Maßstab, an dem alles andere hängt
//   Schreibtisch   26 × 12     — passt neben die Figur, ohne sie zu verdecken
//   Bürostuhl      10 × 15
//   Monitor        16 × 13     — steht auf der Tischplatte, nicht auf dem Boden
//   runder Tisch   52 × 13
//   Tür            20 × 34     — reicht von der Bodenlinie bis fast an die Decke
//   Fenster        30 × 22     — kachelbar, siehe `WINDOW_STEP`
//
// Die Wand nimmt die obersten `WALL_H = 38` Zeilen ein, der Boden die restlichen 232.
//
// Wand, Boden, Teppich und Fensterlicht sind **prozedural**: sie sind Flächen, keine Motive.
// Als Art wären sie zusammen größer als das gesamte übrige Kunstbudget und ließen sich nicht
// auf beliebige Raumbreiten ziehen.

import type { Ctx } from "../types.ts";
import { PIX } from "../const.ts";
import { mix } from "../ids.ts";
import type { Pal, PalKey } from "./palette.ts";
import { artH, artLeft, artW, defineArt, drawArt, fill, fillA } from "./art.ts";

// ═══ Maße der Kulisse ════════════════════════════════════════════════════════

/** Höhe der Rückwand in Pufferpixeln. Alles darunter ist Boden; `WALL_H` ist damit auch die
 *  Bodenlinie, auf der Tür, Schränke und die hintere Schreibtischreihe stehen. */
export const WALL_H = 38;

/** Schrittweite beim Aneinanderreihen von Fenstern: zwei Fenster teilen sich den Pfosten,
 *  sonst stünde eine 4 Pixel breite Rahmennaht zwischen ihnen. */
export const WINDOW_STEP = 28;

// ═══ Die Arts ════════════════════════════════════════════════════════════════
//
// Zeichenlegende ist je Art eigenständig — `M` heißt im Monitor etwas anderes als im Schrank.
// Was in **keinem** Möbel-Art vorkommen darf: `S H T P h s t`. Diese sieben Zeichen sind für
// die Figur reserviert und würden hier die Hautfarbe des gerade gezeichneten Agenten annehmen.

// ── Schreibtisch ─────────────────────────────────────────────────────────────
// Platte, Vorderkante, Sichtblende, zwei Metallwangen. 26×12: schmal genug, dass eine
// 16 Pixel breite Figur davorsteht, ohne dass der Tisch sie einrahmt.

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

// ── Bürostuhl ────────────────────────────────────────────────────────────────
// Zwei Fassungen. Die besetzte lässt die Sitzfläche frei: dort sitzt die Figur, und ein
// durchscheinender Stuhl unter ihr sähe aus wie ein Zeichenfehler. Der Fuß bleibt in beiden
// Fassungen sichtbar — er ragt seitlich unter der Figur hervor und ist das Detail, an dem man
// „sitzt am Schreibtisch" überhaupt erkennt.

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
// Nur Gehäuse und leere Fläche. Der Inhalt wird prozedural hineingezeichnet (`drawMonitor`),
// weil sieben Bildsorten × vier Stimmungen als 28 Arts das halbe Budget fräßen — und weil
// dieselben Striche in anderer Länge sofort nach einem anderen Werkzeug aussehen.

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

/** Innenfläche des Monitors, relativ zur linken oberen Ecke des Arts. */
const MON_IN = { x: 1, y: 1, w: 14, h: 7 } as const;

// ── Runder Tisch ─────────────────────────────────────────────────────────────
// Die Ellipse ist eine Tabelle von Halbbreiten, kein `arc` (Regel 2.1). Die vier oberen Zeilen
// sind Platte, die drei unteren die vordere Zarge — dieser eine Farbwechsel macht aus einer
// Scheibe eine Tischplatte mit Dicke.

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
// Schmaler als der Bürostuhl (8 statt 10) und ohne Rollenfuß: der Besprechungsstuhl steht,
// er rollt nicht. Der Unterschied ist bei 8 Pixeln klein, aber er trennt „Arbeitsplatz" von
// „Besprechung", und genau das soll das Huddle zeigen.

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
// Zwei Größen. Die große steht in Ecken und bricht die Wandkante, die kleine steht auf
// Schränken und Fensterbänken. Beide sind bewusst unsymmetrisch — eine achsensymmetrische
// Pflanze liest sich als Ornament, nicht als Pflanze.

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
// 22×20, also gut kopfhoch neben einer 24 Pixel hohen Figur. Drei Schubladen mit Griffen;
// die waagerechten Fugen sind das, was ihn vom Blech-Quader unterscheidet.

const CABINET = defineArt([
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "cccccccccccccccccccccc",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMIIIIIIIIMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "cccccccccccccccccccccc",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMIIIIIIIIMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "cccccccccccccccccccccc",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMIIIIIIIIMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "MMMMMMMMMMMMMMMMMMMMMM",
  "cccccccccccccccccccccc",
  ".c..................c.",
], { M: "metal", c: "chairLo", I: "ink" });

// ── Kaffeeecke ───────────────────────────────────────────────────────────────
// Maschine auf einer kleinen Theke, mit Tasse in der Nische. Das ist der Ort, an den eine
// Figur nach `IDLE_COFFEE_MS` geht — das einzige Lebenszeichen in einer langen Werkzeugkette,
// und deshalb muss man auf einen Blick sehen, was es ist.

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

// ── Tür ──────────────────────────────────────────────────────────────────────
// Zwei Fassungen. Die offene zeigt einen dunklen Flur und das eingeschwenkte Blatt: durch
// sie kommen und gehen die Läufe. Ohne den dunklen Durchgang liest sich die offene Tür wie
// ein Loch in der Wand.

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
// Zwei Scheiben je Element, Kämpfer in der Mitte. Der schräge Glanz (`G`) sitzt in beiden
// Scheiben gleich: er ist eine Spiegelung des Raumlichts, keine Wolke — bei Nacht trägt er
// die Deckenbeleuchtung, bei Tag den Himmel.

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
// Zwei Kästen mit einem Pfeil dazwischen und drei Textzeilen. Der Inhalt ist bewusst fest:
// ein Board, dessen Skizze sich ändert, wäre eine Bewegung ohne Ereignis — und damit ein
// Detail, das der Zuschauer für eine Aussage über den Lauf hält, die es nicht ist.

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
// Die Zeiger stehen **fest**. Sie aus der Uhrzeit zu setzen wäre in Schicht 1 verboten
// (Regel 3.1) — und schlimmer: beim Zurückspulen zeigte dieselbe Sekunde des Laufs jedes Mal
// eine andere Uhrzeit. Die Uhr ist Einrichtung, keine Anzeige.

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

// ═══ Maßtabelle ══════════════════════════════════════════════════════════════

/** Die Maße aller Möbel in Pufferpixeln — abgeleitet, nicht getippt. Wer den Raum aufbaut
 *  (Welle F/H), rechnet damit Abstände aus, statt Zahlen aus diesem Kommentarkopf abzuschreiben,
 *  die beim nächsten Umbau eines Sprites still falsch würden. */
export const SIZE = {
  desk: { w: artW(DESK), h: artH(DESK) },
  chair: { w: artW(CHAIR_FREE), h: artH(CHAIR_FREE) },
  monitor: { w: artW(MONITOR), h: artH(MONITOR) },
  office: { w: artW(OFFICE), h: artH(OFFICE) },
  tableChair: { w: artW(TABLE_CHAIR), h: artH(TABLE_CHAIR) },
  plantTall: { w: artW(PLANT_TALL), h: artH(PLANT_TALL) },
  plantSmall: { w: artW(PLANT_SMALL), h: artH(PLANT_SMALL) },
  cabinet: { w: artW(CABINET), h: artH(CABINET) },
  coffee: { w: artW(COFFEE), h: artH(COFFEE) },
  door: { w: artW(DOOR_SHUT), h: artH(DOOR_SHUT) },
  window: { w: artW(WINDOW), h: artH(WINDOW) },
  board: { w: artW(BOARD), h: artH(BOARD) },
  clock: { w: artW(CLOCK), h: artH(CLOCK) },
} as const;

// ═══ Zeichnen ════════════════════════════════════════════════════════════════
//
// Alle Weltzeichenfunktionen tragen `(ctx, cx, yBase, pal, …)`: waagerechte Mitte plus
// **unterste** Kontaktzeile. Die Szene sortiert nach `yBase` (Maler-Algorithmus); ein Objekt,
// das seine Oberkante übergibt, sortiert falsch und verschwindet hinter dem, wovor es steht.
//
// Die Palette steht an vierter Stelle statt in den Optionen, weil sie jede dieser Funktionen
// braucht und ein Aufruf ohne sie gar nichts zeichnen könnte.

/** Ein weicher Kontaktschatten unter einem Möbel. Kein `shadowBlur` (verboten und in dieser
 *  Auflösung ohnehin Matsch): drei flache Rechtecke, nach außen blasser. Ohne ihn schwebt
 *  jedes Möbel einen Hauch über dem Boden — der Effekt ist winzig und fällt sofort auf. */
function contactShadow(ctx: Ctx, pal: Pal, cx: number, yBase: number, w: number): void {
  const h = w >> 1;
  fillA(ctx, pal, "shadow", 0.20, cx - (w >> 1), yBase - 1, w, 1);
  fillA(ctx, pal, "shadow", 0.12, cx - (w >> 1) - 1, yBase, w + 2, 1);
  fillA(ctx, pal, "shadow", 0.06, cx - (h >> 1) - h, yBase + 1, h * 2, 1);
}

export function drawDesk(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  const x0 = artLeft(DESK, cx);
  contactShadow(ctx, pal, cx, yBase, SIZE.desk.w);
  drawArt(ctx, DESK, cx, yBase, pal);
  // Ein heller Streifen auf der Vorderkante der Platte: die Kante fängt das Licht, und ohne
  // sie sieht die Platte aus wie ein aufgeklebtes Rechteck.
  fillA(ctx, pal, "wallHi", 0.18, x0, yBase - SIZE.desk.h, SIZE.desk.w, 1);
  // Die Sichtblende liegt im Eigenschatten der Platte. Ohne diesen Griff hat sie fast genau
  // den Ton des Dielenbodens und verschwindet darin — der Tisch sieht dann aus wie eine
  // Platte auf zwei Drähten.
  fillA(ctx, pal, "shadow", 0.30, x0 + 3, yBase - SIZE.desk.h + 3, SIZE.desk.w - 6, 5);
}

export interface ChairOpts {
  /** Es sitzt jemand darauf — Sitzfläche bleibt frei, die Figur füllt sie. */
  occupied?: boolean;
  /** Blickt nach links. */
  flip?: boolean;
}

export function drawChair(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: ChairOpts): void {
  const art = opts?.occupied === true ? CHAIR_TAKEN : CHAIR_FREE;
  contactShadow(ctx, pal, cx, yBase, SIZE.chair.w);
  drawArt(ctx, art, cx, yBase, pal, { flip: opts?.flip });
}

// ── Monitor: Bildsorte und Stimmung ──────────────────────────────────────────

/** Was auf dem Schirm zu sehen ist. Sieben Bilder für vierzig Werkzeuge — abgeleitet aus
 *  `ToolAct`, nicht aus dem Werkzeugnamen. */
export type ScreenKind = "code" | "log" | "page" | "search" | "link" | "wait" | "blank";

/** Wie es dem Lauf gerade geht. Färbt **den Schein, die Tinte einer Zeile und ein Pixel des
 *  Rahmens** — nicht die Fläche. Ein rot geflutetes Panel liest sich als „kaputter Monitor",
 *  nicht als „fehlgeschlagener Schritt", und übertönt bei zwölf Plätzen alles andere im Bild. */
export type Mood = "work" | "wait" | "error" | "done";

const MOOD_COLOR: Record<Mood, PalKey> = {
  work: "acc", wait: "blocked", error: "err", done: "ok",
};

/** Zeilenbild je Bildsorte: `[Zeile, Einzug, Länge]` innerhalb der 14×7-Innenfläche.
 *  Zeile 7 gibt es nicht — wer sie einträgt, malt in den Rahmen. */
const SCREEN_LINES: Record<ScreenKind, readonly (readonly [number, number, number])[]> = {
  // Editor: Einzugsstufen sind das, was Quelltext auf 14 Pixeln überhaupt erkennbar macht.
  code: [[0, 0, 6], [1, 2, 7], [2, 2, 4], [3, 4, 8], [4, 2, 5], [5, 4, 6], [6, 0, 7]],
  // Protokoll: linksbündig, dicht, ungleich lang.
  log: [[0, 0, 12], [1, 0, 9], [2, 0, 13], [3, 0, 7], [4, 0, 11], [5, 0, 13], [6, 0, 8]],
  // Dokument/Seite: Überschrift und Fließtext, mit Rand.
  page: [[1, 2, 6], [3, 2, 10], [4, 2, 10], [5, 2, 7]],
  // Suche: Eingabezeile oben, darunter Treffer.
  search: [[0, 1, 12], [2, 1, 9], [3, 1, 11], [4, 1, 6], [5, 1, 10]],
  // Übergabe: zwei Kästen, die Verbindung malt `drawScreenBody` in der Stimmungsfarbe.
  link: [[1, 1, 3], [2, 1, 3], [3, 1, 3], [1, 10, 3], [2, 10, 3], [3, 10, 3]],
  // Warten: nur der Balken unten; die Punkte kommen in Stimmungsfarbe.
  wait: [[6, 1, 12]],
  blank: [],
};

function drawScreenBody(
  ctx: Ctx, pal: Pal, x: number, y: number, kind: ScreenKind, mood: PalKey,
): void {
  if (kind === "blank") {
    // Dunkles Panel: der Platz ist besetzt, aber gerade tut niemand etwas.
    fill(ctx, pal, "screen", x, y, MON_IN.w, MON_IN.h);
    return;
  }
  if (kind === "page") {
    // Ein Dokument ist Papier, kein leuchtender Editor — deshalb der einzige Fall,
    // in dem die Fläche eine andere Grundfarbe bekommt.
    fill(ctx, pal, "paper", x, y, MON_IN.w, MON_IN.h);
  }
  if (kind === "code") {
    // Zeilennummernspalte.
    fill(ctx, pal, "screen", x, y, 2, MON_IN.h);
  }

  // Der Editor rückt zusätzlich um die Nummernspalte ein. Geklemmt wird **nach** diesem
  // Versatz: sonst läuft die längste Codezeile ein Pixel über die Innenfläche hinaus und
  // malt in den Rahmen — bei zwölf Monitoren fällt genau das auf.
  const gutter = kind === "code" ? 3 : 0;
  for (const [row, indent, len] of SCREEN_LINES[kind]) {
    const sx = indent + gutter;
    fill(ctx, pal, "ink", x + sx, y + row, Math.min(len, MON_IN.w - sx), 1);
  }

  // Genau ein Element trägt die Stimmung — nie die Fläche.
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
  /** Blickrichtung des Platzes — der Schein fällt dann zur anderen Seite. */
  flip?: boolean;
}

/**
 * Monitor mit Inhalt. `yBase` ist die Unterkante des Fußes, also die Tischplatte —
 * **nicht** der Boden.
 */
export function drawMonitor(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: MonitorOpts): void {
  const kind = opts?.screen ?? "blank";
  const mood = MOOD_COLOR[opts?.mood ?? "work"];
  const x0 = artLeft(MONITOR, cx);
  const y0 = yBase - SIZE.monitor.h;

  // Der Schein liegt **hinter** dem Gerät und wird vom Gehäuse überzeichnet: zwei Ringe,
  // außen schwächer. Ein Verlauf wäre hier das Naheliegende und ist verboten (Regel 2.1) —
  // zwei gestufte Rechtecke sehen bei 480×270 ohnehin besser aus als ein weicher Kreis.
  if (kind !== "blank") {
    fillA(ctx, pal, mood, 0.06, x0 - 5, y0 - 4, SIZE.monitor.w + 10, 17);
    fillA(ctx, pal, mood, 0.10, x0 - 2, y0 - 2, SIZE.monitor.w + 4, 13);
  }

  drawArt(ctx, MONITOR, cx, yBase, pal, { flip: opts?.flip });
  drawScreenBody(ctx, pal, x0 + MON_IN.x, y0 + MON_IN.y, kind, mood);

  // Das eine Rahmenpixel: die Betriebsleuchte unten rechts.
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

export function drawCabinet(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.cabinet.w);
  drawArt(ctx, CABINET, cx, yBase, pal);
  fillA(ctx, pal, "wallHi", 0.20, artLeft(CABINET, cx), yBase - SIZE.cabinet.h, SIZE.cabinet.w, 1);
}

export function drawCoffee(ctx: Ctx, cx: number, yBase: number, pal: Pal): void {
  contactShadow(ctx, pal, cx, yBase, SIZE.coffee.w);
  drawArt(ctx, COFFEE, cx, yBase, pal);
}

/** Tür. `yBase` ist die Schwelle, also die Bodenlinie an der Wand (`WALL_H`). */
export function drawDoor(ctx: Ctx, cx: number, yBase: number, pal: Pal, opts?: { open?: boolean }): void {
  const open = opts?.open === true;
  drawArt(ctx, open ? DOOR_OPEN : DOOR_SHUT, cx, yBase, pal);
  if (open) {
    // Lichtstreifen aus dem Flur auf den Boden davor — er macht den Durchgang zu einem
    // Durchgang statt zu einem schwarzen Rechteck.
    fillA(ctx, pal, "lamp", 0.12, cx - 4, yBase, 14, 2);
    fillA(ctx, pal, "lamp", 0.07, cx - 6, yBase + 2, 18, 2);
  }
}

/** Fensterelement. `yBase` ist die Unterkante der Fensterbank. Mehrere Elemente im Abstand
 *  `WINDOW_STEP` setzen, dann teilen sich zwei Fenster den Pfosten. */
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

/** Länge eines Dielenbretts und Höhe einer Dielenreihe.
 *
 *  Das Längenverhältnis ist das ganze Geheimnis: bei 46×7 (erster Versuch) sieht der Boden aus
 *  wie eine Ziegelmauer, weil ein Brett dann nur sechsmal so lang wie hoch ist. Echte Dielen
 *  liegen bei 15:1 und darüber — deshalb 92×6. */
const PLANK_W = 92;
const PLANK_H = 10;
/** Mindestversatz zweier benachbarter Reihen. Ohne diese Schranke fallen Stöße gelegentlich
 *  untereinander und der Boden bekommt eine durchgehende Fuge — ein verlegter Boden hat das
 *  nie, und das Auge sieht die Reihe sofort. */
const MIN_STAGGER = 26;

/**
 * Die Rückwand: Fläche, Deckenkante, ein leichter Abfall nach unten, Sockelleiste.
 *
 * Der Abfall ist drei gestufte Bänder statt eines Verlaufs (Regel 2.1) — bei 38 Zeilen Höhe
 * sieht man den Unterschied ohnehin nicht, und Bänder bleiben golden prüfbar.
 */
export function drawWall(ctx: Ctx, pal: Pal): void {
  fill(ctx, pal, "wall", 0, 0, PIX.w, WALL_H);
  fill(ctx, pal, "wallHi", 0, 0, PIX.w, 2);
  fillA(ctx, pal, "wallLo", 0.25, 0, WALL_H - 12, PIX.w, 6);
  fillA(ctx, pal, "wallLo", 0.45, 0, WALL_H - 6, PIX.w, 2);
  // Sockelleiste mit heller Oberkante.
  fill(ctx, pal, "wallLo", 0, WALL_H - 4, PIX.w, 4);
  fillA(ctx, pal, "wallHi", 0.35, 0, WALL_H - 4, PIX.w, 1);
  // Plattenstöße der Wandverkleidung: eine Linie alle 80 Pixel, sehr blass. Sie geben der
  // Wand einen Maßstab — ohne sie wirkt die Fläche wie ein Farbfeld, nicht wie ein Raum.
  for (let x = 40; x < PIX.w; x += 80) fillA(ctx, pal, "wallLo", 0.30, x, 2, 1, WALL_H - 6);
}

/**
 * Der Dielenboden. Reihen von `PLANK_H`, Stöße alle `PLANK_W`, je Reihe versetzt und dabei
 * mit erzwungenem Mindestversatz. Jedes Brett bekommt aus seinem Hash eine von drei
 * Tönungen — dieselbe Fläche in einer Farbe sieht aus wie Linoleum.
 */
export function drawFloor(ctx: Ctx, pal: Pal): void {
  fill(ctx, pal, "floor", 0, WALL_H, PIX.w, PIX.h - WALL_H);

  let prev = -1000;
  let r = 0;
  for (let y = WALL_H; y < PIX.h; y += PLANK_H, r++) {
    const rowH = Math.min(PLANK_H, PIX.h - y);
    const raw = mix(r, SALT_PLANK) % PLANK_W;
    let off = raw;
    if (prev >= 0) {
      const d = Math.abs(off - prev);
      // Der Abstand ist zyklisch: 1 und 45 liegen bei `PLANK_W = 46` nebeneinander.
      if (Math.min(d, PLANK_W - d) < MIN_STAGGER) {
        off = (prev + MIN_STAGGER + (raw % (PLANK_W - 2 * MIN_STAGGER))) % PLANK_W;
      }
    }
    prev = off;

    // Bretttönung, Maserung und senkrechte Stöße in einem Durchgang.
    let p = 0;
    for (let x = off - PLANK_W; x < PIX.w; x += PLANK_W, p++) {
      const v = mix(r * 131 + p, SALT_SHADE) % 4;
      // Schwach: ein Brett soll sich vom Nachbarn abheben, nicht von ihm abstechen.
      if (v === 1) fillA(ctx, pal, "floorHi", 0.11, x, y, PLANK_W, rowH - 1);
      else if (v === 2) fillA(ctx, pal, "floorLo", 0.09, x, y, PLANK_W, rowH - 1);
      // Eine Maserungslinie je Brett, Lage aus demselben Hash — das ist der Unterschied
      // zwischen „Holz" und „braune Kacheln".
      else if (v === 3) fillA(ctx, pal, "floorLo", 0.12, x + 6, y + 2, PLANK_W - 18, 1);
      if (x >= 0) fillA(ctx, pal, "floorLo", 0.75, x, y, 1, rowH - 1);
    }
    // Waagerechte Fuge am unteren Rand der Reihe — deutlich schwächer als der Stoß,
    // sonst gewinnt die Reihe optisch über das Brett und der Boden kippt ins Gemauerte.
    if (rowH === PLANK_H) fillA(ctx, pal, "floorLo", 0.28, 0, y + rowH - 1, PIX.w, 1);
  }

  // Schattenband unter der Wand: der Boden bekommt dort kein Streiflicht ab. Vier Zeilen
  // reichen, damit die Wand auf dem Boden aufsitzt statt an ihn angeklebt zu sein.
  fillA(ctx, pal, "shadow", 0.22, 0, WALL_H, PIX.w, 1);
  fillA(ctx, pal, "shadow", 0.14, 0, WALL_H + 1, PIX.w, 1);
  fillA(ctx, pal, "shadow", 0.08, 0, WALL_H + 2, PIX.w, 2);
}

/**
 * Teppich. `cx`/`yBase` sind Mitte und **Vorderkante** — auch der Teppich hält sich an die
 * Sortierregel, obwohl er flach liegt: er wird im Hintergrunddurchgang gezeichnet, aber seine
 * Vorderkante ist der Punkt, an dem eine davorstehende Figur ihn verdecken muss.
 */
export function drawRug(ctx: Ctx, cx: number, yBase: number, w: number, h: number, pal: Pal): void {
  const x = cx - (w >> 1);
  const y = yBase - h;
  fill(ctx, pal, "rug", x, y, w, h);
  // Umlaufende Borte, innen ein zweiter Streifen — zwei Linien genügen, damit ein Rechteck
  // als Teppich liest und nicht als Farbfleck.
  fill(ctx, pal, "rugLo", x, y, w, 1);
  fill(ctx, pal, "rugLo", x, y + h - 1, w, 1);
  fill(ctx, pal, "rugLo", x, y, 1, h);
  fill(ctx, pal, "rugLo", x + w - 1, y, 1, h);
  fill(ctx, pal, "rugLo", x + 3, y + 3, w - 6, 1);
  fill(ctx, pal, "rugLo", x + 3, y + h - 4, w - 6, 1);
  fill(ctx, pal, "rugLo", x + 3, y + 3, 1, h - 6);
  fill(ctx, pal, "rugLo", x + w - 4, y + 3, 1, h - 6);
  // Die Vorderkante fängt Licht, der Rest liegt im Raumschatten.
  fillA(ctx, pal, "wallHi", 0.10, x, y + h - 1, w, 1);
}

/**
 * Der Lichtteppich unter einem Fenster. `cx` ist die Fenstermitte, `yBase` das vordere Ende
 * des Lichtflecks; `w` ist die Breite an der Wand, `h` die Tiefe.
 *
 * Ein Fensterlicht ist perspektivisch ein Trapez: es wird zum Betrachter hin breiter und
 * blasser. Beides entsteht hier zeilenweise — pro Bodenzeile ein Rechteck mit eigener Breite
 * und eigener Deckkraft. Die Lücke in der Mitte ist der Schatten des Kämpfers; ohne sie sieht
 * der Fleck aus wie eine Lampe, nicht wie ein Fenster.
 *
 * Gehört ins Tagbild. Nachts liegt draußen keine Sonne — dann zeichnet die Szene ihn nicht.
 */
export function drawWindowLight(
  ctx: Ctx, cx: number, yBase: number, w: number, h: number, pal: Pal,
): void {
  const y0 = yBase - h;
  for (let i = 0; i < h; i++) {
    const t = i / h;
    const rowW = w + Math.round(t * w * 0.55);
    const alpha = 0.13 * (1 - t) + 0.02;
    // Der Kämpferschatten läuft nach vorn aus: eine bis zum Ende durchgezogene Lücke sieht
    // aus wie ein Zeichenfehler, kein Schatten.
    const gap = t > 0.62 ? 0 : 2 + Math.round(t * 3);
    const x = cx - (rowW >> 1);
    const half = (rowW - gap) >> 1;
    fillA(ctx, pal, "lamp", alpha, x, y0 + i, half, 1);
    fillA(ctx, pal, "lamp", alpha, x + half + gap, y0 + i, rowW - half - gap, 1);
  }
}
