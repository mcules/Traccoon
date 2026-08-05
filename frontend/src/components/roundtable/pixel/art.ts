// Schicht 1 — Sprites sind Quellcode.
//
// Ein `Art` ist ein Bild als Zeichenkettenfeld: ein Zeichen je Pixel, `.` und Leerzeichen sind
// durchsichtig, jedes andere Zeichen zeigt über `map` auf einen Palettenschlüssel. Keine
// Bilddateien, kein `drawImage`, kein Ladezustand, kein Netzwerk — ein Sprite ist im Diff
// lesbar und in der Codeprüfung kommentierbar.
//
// Die sieben reservierten Zeichen (`S H T P h s t`) stehen in den Personen-Arts und werden
// **erst beim Zeichnen** aus der übergebenen Palette bedient. Dieselben 19 Sprite-Teile ergeben
// damit zwölf verschiedene Leute, ohne dass ein einziges Pixel doppelt im Quelltext steht.

import type { Ctx } from "../types.ts";
import type { Pal, PalKey } from "./palette.ts";

/** Ein Sprite. `rows` sind gleich lange Zeilen, `map` bildet jedes benutzte Zeichen ab. */
export type Art = { rows: readonly string[]; map: Readonly<Record<string, PalKey>> };

/** Durchsichtig. Zwei Zeichen, weil `.` das Raster lesbar hält und ein Leerzeichen beim
 *  Umbauen eines Sprites schneller getippt ist. */
function transparent(ch: string): boolean {
  return ch === "." || ch === " ";
}

/**
 * Baut ein `Art` und prüft es dabei.
 *
 * Die Prüfung ist die halbe Miete dieser Datei: ein Sprite ist ein Block gleich langer
 * Zeichenketten, und ein einziges vergessenes Zeichen verschiebt alles darunter um eine Spalte.
 * Im fertigen Bild sieht das aus wie ein Zeichenfehler irgendwo im Renderer — man sucht dann
 * stundenlang an der falschen Stelle. Ein fehlendes `map`-Zeichen ist noch heimtückischer:
 * das Pixel fehlt einfach lautlos.
 *
 * Deshalb wird beim Laden des Moduls geworfen, nicht später gemeldet. Das ist gefahrlos:
 * Arts sind statischer Quelltext, die Prüfung hängt an keiner Eingabe. Was einmal lädt,
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

/** Höhe in Pufferpixeln. */
export function artH(art: Art): number {
  return art.rows.length;
}

/** Linke Kante eines Arts, das bei `cx` zentriert steht. Bei gerader Breite fällt die Mitte
 *  eine halbe Spalte nach links — konsequent für alle Arts, damit zwei nebeneinander gesetzte
 *  Objekte nicht um ein Pixel gegeneinander springen, je nachdem ob ihre Breite gerade ist. */
export function artLeft(art: Art, cx: number): number {
  return cx - (artW(art) >> 1);
}

export interface DrawOpts {
  /** Waagerecht gespiegelt — Blickrichtung nach links. */
  flip?: boolean;
  /** Deckkraft. Wird nach dem Zeichnen **immer** auf 1 zurückgesetzt (Regel 2.1: es gibt
   *  kein `restore`, das Zurücksetzen ist Pflicht des Zeichners). */
  alpha?: number;
  /** Alle sichtbaren Pixel in einer Farbe — für Schatten- und Umrissdurchgänge.
   *  Durchsichtige Pixel bleiben durchsichtig. */
  tint?: PalKey;
}

/**
 * Zeichnet ein `Art` mit Mitte `cx` und Fußpunkt `yBase`.
 *
 * `yBase` ist die erste Zeile **unter** dem Sprite (Regel 2.2): ein 24 Pixel hohes Sprite
 * belegt `yBase-24 … yBase-1`. Die Szene sortiert nach genau diesem Wert, ein Objekt das seine
 * Oberkante übergibt sortiert falsch und verschwindet hinter Möbeln, vor denen es steht.
 *
 * **Warum die Läufe**: naiv wäre ein `fillRect(1×1)` je Pixel — eine 16×24-Figur wären 384
 * Aufrufe, zwölf Figuren über 4600, und die Bildrate stürbe an der Aufrufmenge, nicht an der
 * Füllfläche. Gleichfarbige waagerechte Nachbarn werden deshalb zu **einem** `fillRect`
 * zusammengefasst; damit landet eine Figur bei 60–120 Aufrufen. `fillStyle` wird nur beim
 * Farbwechsel gesetzt — auch das ist im Canvas nicht gratis.
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
    // Lauf-Zustand: `key === ""` heißt „gerade durchsichtig".
    let key: PalKey | "" = "";
    let start = 0;
    // Ein Schritt über das Zeilenende hinaus schließt den letzten Lauf — spart die
    // Wiederholung des Abschlusscodes hinter der Schleife.
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

/** Ein Rechteck in einer Palettenfarbe. Die einzige Stelle, an der prozedurale Teile
 *  (Wand, Boden, Blasen) `fillStyle` anfassen — das hält den Vertrag an einem Ort. */
export function fill(
  ctx: Ctx, pal: Pal, key: PalKey, x: number, y: number, w: number, h: number,
): void {
  if (w <= 0 || h <= 0) return;
  ctx.fillStyle = pal[key];
  ctx.fillRect(x, y, w, h);
}

/** Dasselbe mit Deckkraft — und mit dem verpflichtenden Zurücksetzen auf 1. Ein vergessenes
 *  `globalAlpha` färbt den kompletten Rest des Bildes blass; das ist der häufigste Fehler
 *  in einer Zeichenschicht ohne `save`/`restore`. */
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
