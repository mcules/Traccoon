// tools/film/raster.mjs — ein `Ctx` ohne Browser.
//
// Warum überhaupt: der Feierabendfilm entsteht in einem Sidecar ohne Chromium. Die Pixel-Schicht
// des Büros kennt aber nur drei Kanäle (`fillStyle`, `globalAlpha`, `fillRect`, PIXEL-CONTRACT
// Regel 2.1) — genau wenig genug, dass ein zweiseitiger Rasterer sie vollständig bedient. Damit
// bleibt der Renderer derselbe wie im Browser; verglichen wird echter Bürocode, keine Nachbildung.
//
// Dieses Modul kennt die Büro-Quellen **nicht** und importiert nichts aus `src/components/office`.
// Es nimmt Zeichenbefehle entgegen und gibt einen RGB-Puffer heraus. Deshalb ist die Kette
// Rasterer→Encoder als Ganzes gegen einen anderen Ausgang (MP4) austauschbar.

/** `#rrggbb` (und, geduldet, `#rgb`) → drei Kanäle.
 *  Nachbau von `parse()` in `pixel/palette.ts` — dort bewusst nicht exportiert, und ein Import
 *  aus `office/` würde die Unabhängigkeit dieses Moduls aufgeben. */
function zerlege(s) {
  if (typeof s === "string" && s.charCodeAt(0) === 35 /* # */) {
    if (s.length === 7) {
      const n = parseInt(s.slice(1), 16);
      if (Number.isFinite(n)) return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
    } else if (s.length === 4) {
      const n = parseInt(s.slice(1), 16);
      if (Number.isFinite(n)) {
        const r = (n >> 8) & 0xf, g = (n >> 4) & 0xf, b = n & 0xf;
        return [r * 17, g * 17, b * 17];
      }
    }
  }
  // Unbekannte Form (Verlauf-Objekt, `rgba(…)`) gäbe es im Vertrag gar nicht — schwarz statt Wurf,
  // damit ein Film nicht an einer einzigen exotischen Farbe stirbt.
  return [0, 0, 0];
}

/** Ein `Ctx` nach der Form in `office/types.ts` (nur `fillStyle`, `globalAlpha`, `fillRect`),
 *  der in einen RGB-Puffer schreibt.
 *  @param {number} w
 *  @param {number} h
 *  @returns {{ctx: object, buf: Uint8Array, reset: () => void}} `buf` = w*h*3, zeilenweise */
export function rasterCtx(w, h) {
  if (!Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0) {
    throw new Error(`rasterCtx: ungültige Maße ${w}×${h}`);
  }
  const buf = new Uint8Array(w * h * 3);

  // Hexparsen memoisieren: 3000–5000 `fillStyle`-Zuweisungen je Bild × 300 Bilder wären 1,5 Mio.
  // `parseInt`-Aufrufe; über eine Map sind es so viele wie es Farbtöne gibt (Größenordnung 50).
  const cache = new Map();
  let stil = "#000000";
  let r = 0, g = 0, b = 0;

  const ctx = {
    globalAlpha: 1,
    get fillStyle() { return stil; },
    set fillStyle(v) {
      if (v === stil) return;          // derselbe Ton hintereinander: gar kein Nachschlagen
      stil = v;
      const key = typeof v === "string" ? v : "";
      let c = cache.get(key);
      if (c === undefined) { c = zerlege(key); cache.set(key, c); }
      r = c[0]; g = c[1]; b = c[2];
    },
    fillRect(x, y, bw, bh) {
      const a = ctx.globalAlpha;
      if (!(a > 0) || !bw || !bh) return;   // fängt auch NaN und 0-Maße ab

      // Negative Maße normalisiert die Canvas-Spezifikation (das Rechteck wächst nach links/oben),
      // nicht: sie ist ein Fehler. Genauso hier, sonst driftet der Film vom Browser weg.
      let x0 = bw < 0 ? x + bw : x, x1 = x0 + (bw < 0 ? -bw : bw);
      let y0 = bh < 0 ? y + bh : y, y1 = y0 + (bh < 0 ? -bh : bh);
      x0 = Math.round(x0); x1 = Math.round(x1);
      y0 = Math.round(y0); y1 = Math.round(y1);

      // Klemmen ist keine Vorsicht, sondern Pflicht: allein in den acht Fixture-Bildern liegen
      // 368 `fillRect` außerhalb von 480×270 (Sprechblasen und Namensschilder ragen über den
      // Rand). Der Browser klippt; ohne das hier liefe die Schleife über Zeilenenden hinweg und
      // malte diagonale Streifen quer durchs Bild.
      if (x0 < 0) x0 = 0;
      if (y0 < 0) y0 = 0;
      if (x1 > w) x1 = w;
      if (y1 > h) y1 = h;
      if (x0 >= x1 || y0 >= y1) return;

      if (a >= 1) {
        for (let yy = y0; yy < y1; yy++) {
          let p = (yy * w + x0) * 3;
          for (let xx = x0; xx < x1; xx++) { buf[p++] = r; buf[p++] = g; buf[p++] = b; }
        }
        return;
      }
      // Eine einzige Mischformel im ganzen Modul: `(src*a + dst*(1-a) + 0.5)|0`. `Math.round` und
      // `|0` gemischt ergäben je nach Zweig andere Werte — und der Encoder zählt hinterher Farben.
      const ia = 1 - a, sr = r * a, sg = g * a, sb = b * a;
      for (let yy = y0; yy < y1; yy++) {
        let p = (yy * w + x0) * 3;
        for (let xx = x0; xx < x1; xx++) {
          buf[p] = (sr + buf[p] * ia + 0.5) | 0; p++;
          buf[p] = (sg + buf[p] * ia + 0.5) | 0; p++;
          buf[p] = (sb + buf[p] * ia + 0.5) | 0; p++;
        }
      }
    },
  };

  // `renderFrame` deckt den Puffer vollständig ab, ein `clearRect` gibt es also nicht. `reset()`
  // existiert trotzdem, damit ein Aufrufer denselben Puffer über 300 Bilder wiederverwenden kann,
  // ohne sich auf diese Zusicherung verlassen zu müssen.
  const reset = () => { buf.fill(0); };

  return { ctx, buf, reset };
}
