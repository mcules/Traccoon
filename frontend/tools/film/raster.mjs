// tools/film/raster.mjs: a `Ctx` without a browser.
//
// Why at all: the after-work film comes into being in a sidecar without Chromium. The pixel
// layer of the office knows only three channels though (`fillStyle`, `globalAlpha`, `fillRect`,
// PIXEL-CONTRACT rule 2.1), just few enough for a two page rasteriser to serve them completely.
// That keeps the renderer the same as in the browser; what is compared is real office code.
//
// This module does **not** know the office sources and imports nothing from
// `src/components/office`. It takes drawing commands and hands out an RGB buffer. That is why
// the chain rasteriser to encoder is exchangeable as a whole against another output (MP4).

/** `#rrggbb` (and, tolerated, `#rgb`) to three channels.
 *  A rebuild of `parse()` in `pixel/palette.ts`, which is deliberately not exported there, and
 *  an import from `office/` would give up the independence of this module. */
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
  // An unknown form (a gradient object, `rgba(…)`) would not exist in the contract at all:
  // black instead of a throw, so that a film does not die of a single exotic colour.
  return [0, 0, 0];
}

/** A `Ctx` in the shape of `office/types.ts` (only `fillStyle`, `globalAlpha`, `fillRect`) that
 *  writes into an RGB buffer.
 *  @param {number} w
 *  @param {number} h
 *  @returns {{ctx: object, buf: Uint8Array, reset: () => void}} `buf` = w*h*3, zeilenweise */
export function rasterCtx(w, h) {
  if (!Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0) {
    throw new Error(`rasterCtx: invalid size ${w}×${h}`);
  }
  const buf = new Uint8Array(w * h * 3);

  // Memoise the hex parsing: 3000 to 5000 `fillStyle` assignments per frame times 300 frames
  // would be 1.5 million `parseInt` calls; over a map there are as many as there are tones.
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
      if (!(a > 0) || !bw || !bh) return;   // also catches NaN and zero sizes

      // The canvas specification normalises negative dimensions (the rectangle grows to the
      // left and upwards); this does not: it is an error, because otherwise the film drifts away from the browser.
      let x0 = bw < 0 ? x + bw : x, x1 = x0 + (bw < 0 ? -bw : bw);
      let y0 = bh < 0 ? y + bh : y, y1 = y0 + (bh < 0 ? -bh : bh);
      x0 = Math.round(x0); x1 = Math.round(x1);
      y0 = Math.round(y0); y1 = Math.round(y1);

      // Clamping is not caution but duty: in the eight fixture frames alone 368 `fillRect` lie
      // outside 480x270 (speech bubbles and name tags stick out over the edge). The browser
      // clips; without this the loop would run over line ends and paint diagonal stripes.
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
      // A single mixing formula in the whole module: `(src*a + dst*(1-a) + 0.5)|0`. `Math.round`
      // and `|0` mixed would give different values per branch, and the encoder counts colours.
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

  // `renderFrame` covers the buffer completely, so there is no `clearRect`. `reset()` exists
  // regardless, so that a caller can reuse the same buffer over 300 frames without having to
  // rely on that assurance.
  const reset = () => { buf.fill(0); };

  return { ctx, buf, reset };
}
