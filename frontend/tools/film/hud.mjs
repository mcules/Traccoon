// Die zwei Einblendungen des Films: die dauernde Zeile unten und die Trennkarte.
//
// Beide zeichnen mit denselben Mitteln wie die Bühne — `drawText`/`fill`/`fillA` aus Schicht 1
// — und halten damit den `Ctx`-Vertrag (nur `fillStyle`, `globalAlpha`, `fillRect`) ohne eine
// zweite Zeichenwelt. Eine eigene Schriftroutine wäre die zweite Wahrheit über die 3×5-Schrift,
// und die erste Abweichung fiele erst im fertigen GIF auf.
//
// **Kein Wort entsteht hier.** Uhrzeit, Titel und Beschriftung kommen fertig herein: die
// Zeitrechnung als ganzzahlige Arithmetik aus `film.mjs`, alles Sprachliche aus Python. Ein
// `toLocaleTimeString` an dieser Stelle machte das Bild von der ICU-Version des Basis-Images
// abhängig — und damit den goldenen Hash von einem Bibliotheks-Update.

import { ART } from "../../src/components/office/const.ts";
import { fill, fillA } from "../../src/components/office/pixel/art.ts";
import { GRADES } from "../../src/components/office/pixel/palette.ts";
import { drawText, textW } from "../../src/components/office/pixel/props.ts";

/** Höhe des Balkens: 5 Pixel Schrift plus je 3 Luft. Schmaler und die Unterlängen kleben am
 *  Bildrand, breiter und der Balken frisst die vordere Schreibtischreihe. */
const BAND_H = 11;

/**
 * Die Zeile am unteren Rand. Steht **dauerhaft** — sie ist der einzige Ort, an dem der
 * Zuschauer sieht, dass zwischen zwei Kapiteln eine Stunde vergangen ist. Ohne sie wirkte der
 * Zeitraffer wie eine durchgehende Aufnahme, und genau das wäre gelogen.
 */
export function hudZeile(ctx, grade, text) {
  const pal = GRADES[grade];
  const y = ART.h - BAND_H;
  fillA(ctx, pal, "shadow", 0.62, 0, y, ART.w, BAND_H);
  // Eine Kante nach oben, sonst schwimmt der Balken über dem Boden statt auf ihm zu liegen.
  fill(ctx, pal, "ink", 0, y, ART.w, 1);
  drawText(ctx, pal, "paper", 4, y + 3, text);
}

/**
 * Die Trennkarte zwischen zwei Kapiteln.
 *
 * Sie verdeckt die Szene nur, sie ersetzt sie nicht: darunter läuft das Bild des Zeitpunkts
 * weiter, auf den die Karte zeigt. Ein schwarzes Vollbild wäre billiger und wäre der Moment,
 * in dem der Film aufhört, ein Fenster in den Raum zu sein.
 *
 * `k` (0..1) ist Ein- und Ausblendung. Der Aufrufer bestimmt den Verlauf; hier wird er nur
 * angewandt, damit die Karte keine eigene Zeitvorstellung hat.
 */
export function kapitelKarte(ctx, grade, titel, zeit, k) {
  const a = k <= 0 ? 0 : k >= 1 ? 1 : k;
  if (a <= 0) return;
  const pal = GRADES[grade];

  fillA(ctx, pal, "shadow", 0.82 * a, 0, 0, ART.w, ART.h);

  // Ein Kasten hinter der Schrift, nicht nur ein Schleier: der Raum hat helle Monitore und
  // einen gemusterten Teppich, und eine 3×5-Schrift darüber liest sich aus drei Metern nicht.
  const breite = Math.max(textW(titel), textW(zeit)) + 22;
  const hoehe = 26;
  const x0 = (ART.w - breite) >> 1;
  const y0 = (ART.h - hoehe) >> 1;
  fillA(ctx, pal, "ink", 0.92 * a, x0, y0, breite, hoehe);
  fillA(ctx, pal, "acc", 0.9 * a, x0, y0, breite, 1);
  fillA(ctx, pal, "acc", 0.9 * a, x0, y0 + hoehe - 1, breite, 1);

  drawText(ctx, pal, "paper", (ART.w - textW(titel)) >> 1, y0 + 6, titel, a);
  drawText(ctx, pal, "acc", (ART.w - textW(zeit)) >> 1, y0 + 15, zeit, a);
}
