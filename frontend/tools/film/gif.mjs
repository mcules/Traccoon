// tools/film/gif.mjs: GIF89a by hand, without a single dependency.
//
// Why GIF and not ffmpeg: this encoder is **pure** (the same bytes in, the same bytes out) and
// therefore byte-golden checkable. An ffmpeg pipeline is not: it depends on a binary version in
// the base image, and every update silently shifts a few bytes, whereupon every golden hash
// turns red and nobody knows any more whether the picture changed or the encoder. Zero
// dependencies also fit a house that sets a 3x5 bitmap font by hand. The way out, should it
// have to be MP4 after all: `mp4.mjs` behind the same signature; only this file is swapped,
// while the rasteriser and the cut stay untouched.
//
// The module does not know the office sources. It sees `Uint8Array` buffers and delivers bytes.
//
// Vier Teile: Farbzensus + Median-Cut · LZW · GIF89a-Container · Differenz-Rechtecke.

// ── Kleiner wachsender Byte-Puffer ───────────────────────────────────────────
// An array of numbers would be noticeable at about 40 KiB per frame with 300 frames, and so
// would `Buffer.concat` over thousands of pieces. Hence this two-liner instead of a dependency.

function senke(n) {
  return { b: new Uint8Array(n > 16 ? n : 16), n: 0 };
}
function schreib(s, v) {
  if (s.n === s.b.length) {
    const nb = new Uint8Array(s.b.length * 2);
    nb.set(s.b);
    s.b = nb;
  }
  s.b[s.n++] = v;
}

// ── Teil 1: Farbzensus + Median-Cut ──────────────────────────────────────────
//
// A fixed 256 colour palette demonstrably does not work out: `globalAlpha` mixes, and over
// eight fixture frames up to 344 different RGB values from 102 different alpha values arise
// with *one* figure. So the palette is counted per film. If it stays at 256 colours or fewer,
// the result is **lossless**; `gemergt: 0` is the target, not the exception.

const kanal = (k, c) => (c === 0 ? (k >> 16) & 0xff : c === 1 ? (k >> 8) & 0xff : k & 0xff);

/** Statistics of a bucket: the longest axis (on a tie R before G before B, a fixed order so
 *  that the cut stays reproducible) and the pixel weight. */
function stats(e, ord, keys, cnt) {
  const min = [255, 255, 255], max = [0, 0, 0];
  let sum = 0;
  for (let i = e.s; i < e.e; i++) {
    const k = keys[ord[i]];
    for (let c = 0; c < 3; c++) {
      const v = kanal(k, c);
      if (v < min[c]) min[c] = v;
      if (v > max[c]) max[c] = v;
    }
    sum += cnt[ord[i]];
  }
  let axis = 0, span = max[0] - min[0];
  for (let c = 1; c < 3; c++) if (max[c] - min[c] > span) { span = max[c] - min[c]; axis = c; }
  e.axis = axis;
  e.span = span;
  e.sum = sum;
}

/** Median cut on the list of *distinct* colours (not on the pixels, which would be 39 million
 *  instead of a few thousand). Returns: buckets in a fixed order, each carrying its
 *  weighted average tone. */
function medianCut(keys, cnt, target, ord) {
  const buckets = [{ s: 0, e: keys.length }];
  stats(buckets[0], ord, keys, cnt);
  while (buckets.length < target) {
    let choice = -1;
    for (let i = 0; i < buckets.length; i++) {
      const e = buckets[i];
      if (e.e - e.s < 2 || e.span === 0) continue;
      const b = choice < 0 ? null : buckets[choice];
      if (!b || e.span > b.span || (e.span === b.span && e.sum > b.sum)) choice = i;
    }
    if (choice < 0) break;                       // nothing left to split: fewer colours than wanted
    const e = buckets[choice];
    const ach = e.axis;
    // A total order (channel, then the full key): then the result is independent of the
    // stability of the sort, and the bytes are the same across Node versions.
    const teil = ord.subarray(e.s, e.e);
    teil.sort((i, j) => kanal(keys[i], ach) - kanal(keys[j], ach) || keys[i] - keys[j]);
    let cum = 0, m = e.s;
    const halb = e.sum / 2;
    while (m < e.e - 1) { cum += cnt[ord[m]]; m++; if (cum >= halb) break; }
    const rechts = { s: m, e: e.e };
    e.e = m;
    stats(e, ord, keys, cnt);
    stats(rechts, ord, keys, cnt);
    buckets.splice(choice + 1, 0, rechts);
  }
  for (const e of buckets) {
    let sr = 0, sg = 0, sb = 0, tot = 0;
    for (let i = e.s; i < e.e; i++) {
      const k = keys[ord[i]], c = cnt[ord[i]];
      sr += ((k >> 16) & 0xff) * c; sg += ((k >> 8) & 0xff) * c; sb += (k & 0xff) * c;
      tot += c;
    }
    e.rgb = [Math.round(sr / tot), Math.round(sg / tot), Math.round(sb / tot)];
  }
  return buckets;
}

// ── Part 2: LZW with a variable code width ───────────────────────────────────
//
// The order below is not arbitrary: first output the code, then extend the table, and check
// the code width **before** entering. The encoder is always one entry ahead of the decoder; if
// it grows one step too early, every decoder reads nonsense from the 512th code on.

function lzw(px, minCodeSize) {
  const clear = 1 << minCodeSize, eoi = clear + 1;
  const out = senke(px.length >> 2);
  let cur = 0, bits = 0;
  const gib = (code, size) => {
    cur |= code << bits;
    bits += size;
    while (bits >= 8) { schreib(out, cur & 0xff); cur >>>= 8; bits -= 8; }
  };

  let codeSize = minCodeSize + 1, next = eoi + 1;
  let dict = new Map();
  gib(clear, codeSize);
  let prev = px[0];
  for (let i = 1; i < px.length; i++) {
    const k = px[i], key = (prev << 8) | k;
    const treffer = dict.get(key);
    if (treffer !== undefined) { prev = treffer; continue; }
    gib(prev, codeSize);
    if (next === 4096) {
      // The table is full, so back to the start. Restart proof, because the decoder sees the same reset.
      gib(clear, codeSize);
      dict = new Map();
      next = eoi + 1;
      codeSize = minCodeSize + 1;
    } else {
      if (next >= (1 << codeSize)) codeSize++;
      dict.set(key, next++);
    }
    prev = k;
  }
  gib(prev, codeSize);
  gib(eoi, codeSize);
  if (bits > 0) schreib(out, cur & 0xff);
  return out.b.subarray(0, out.n);
}

/** Code stream to GIF sub-blocks (each <= 255 bytes, a length prefix, 0x00 as the terminator). */
function subBloecke(bytes) {
  const teile = Math.ceil(bytes.length / 255);
  const out = new Uint8Array(bytes.length + teile + 1);
  let p = 0;
  for (let i = 0; i < bytes.length; i += 255) {
    const c = Math.min(255, bytes.length - i);
    out[p++] = c;
    out.set(bytes.subarray(i, i + c), p);
    p += c;
  }
  out[p] = 0;
  return out;
}

// ── Parts 3+4: container and difference rectangles ───────────────────────────

const u16 = (v) => [v & 0xff, (v >> 8) & 0xff];

/** Builds a whole film. **All frames have to be present at once**: the colour census goes over
 *  the complete film once before the first byte comes into being. With 300 frames of 480x270
 *  that is 116 MiB of input plus a 64 MiB counting array, measured at 195 MiB RSS, so a caller
 *  must **not** reuse a single raster buffer over `reset()`.
 *
 *  @param {Uint8Array[]} images  RGB, je w*h*3, zeilenweise
 *  @param {{w:number,h:number,delaysMs:number[],loop?:boolean}} opt
 *  @returns {{bytes:Buffer, colours:number, gemergt:number, proBild:number[]}}
 *    `colours` = entries of the palette · `gemergt` = how many different input colours were
 *    lost in the process (**0 = lossless**, which always holds at 256 colours or fewer in the
 *    whole film) · `proBild` = bytes per frame, additive to the promised shape and only for
 *    diagnosis (the checker should be able to prove "5 KiB per frame" instead of claiming it). */
export function gif(images, opt) {
  const w = opt.w | 0, h = opt.h | 0, n = images.length;
  if (!Number.isInteger(opt.w) || !Number.isInteger(opt.h) || w <= 0 || h <= 0) {
    throw new Error(`gif: invalid size ${opt.w}×${opt.h}`);
  }
  if (n === 0) throw new Error("gif: no images");
  if (!Array.isArray(opt.delaysMs) || opt.delaysMs.length !== n) {
    throw new Error(`gif: delaysMs has ${opt.delaysMs ? opt.delaysMs.length : 0} entries, `
      + `${n} images`);
  }
  const px = w * h;
  for (let i = 0; i < n; i++) {
    if (images[i].length !== px * 3) {
      throw new Error(`gif: image ${i} has ${images[i].length} bytes, expected ${px * 3}`);
    }
  }

  // A census over the whole film. 2^24 counters are 64 MiB and one line; a `Map` over 39
  // million pixels would be orders of magnitude slower. The same array is repurposed as the
  // colour-to-index lookup table right away: nobody needs the counts afterwards.
  const tabelle = new Uint32Array(1 << 24);
  for (const image of images) {
    for (let p = 0; p < image.length; p += 3) {
      tabelle[(image[p] << 16) | (image[p + 1] << 8) | image[p + 2]]++;
    }
  }
  const keys = [];
  for (let k = 0; k < 1 << 24; k++) if (tabelle[k] !== 0) keys.push(k);
  const verschieden = keys.length;
  const cnt = new Float64Array(verschieden);
  for (let i = 0; i < verschieden; i++) cnt[i] = tabelle[keys[i]];

  /** @type {number[][]} */
  const palette = [];
  const ord = new Int32Array(verschieden);
  for (let i = 0; i < verschieden; i++) ord[i] = i;
  if (verschieden <= 256) {
    // The lossless case: every occurring colour gets an entry of its own.
    for (let i = 0; i < verschieden; i++) {
      const k = keys[i];
      palette.push([(k >> 16) & 0xff, (k >> 8) & 0xff, k & 0xff]);
      tabelle[k] = i;
    }
  } else {
    const buckets = medianCut(keys, cnt, 256, ord);
    for (let bi = 0; bi < buckets.length; bi++) {
      const e = buckets[bi];
      palette.push(e.rgb);
      for (let i = e.s; i < e.e; i++) tabelle[keys[ord[i]]] = bi;
    }
  }
  const colours = palette.length;
  const gemergt = verschieden - colours;

  // The global colour table has to be a power of two (2…256). It is padded with black, and no
  // pixel references those entries.
  let bitsGct = 1;
  while (1 << bitsGct < colours) bitsGct++;
  const gctN = 1 << bitsGct;
  const minCodeSize = Math.max(2, bitsGct);

  const teile = [];
  teile.push(Buffer.from("GIF89a", "ascii"));
  teile.push(Buffer.from([
    ...u16(w), ...u16(h),
    0x80 | ((bitsGct - 1) << 4) | (bitsGct - 1),  // GCT present, colour depth, GCT size
    0,                                            // Hintergrundfarbe
    0,                                            // pixel aspect ratio
  ]));
  const gct = Buffer.alloc(gctN * 3);
  for (let i = 0; i < colours; i++) {
    gct[i * 3] = palette[i][0]; gct[i * 3 + 1] = palette[i][1]; gct[i * 3 + 2] = palette[i][2];
  }
  teile.push(gct);
  if (opt.loop !== false) {
    // NETSCAPE2.0: the only way to write an endless loop into a GIF.
    teile.push(Buffer.from([
      0x21, 0xff, 0x0b, ...Buffer.from("NETSCAPE2.0", "ascii"),
      0x03, 0x01, 0x00, 0x00, 0x00,
    ]));
  }

  const proBild = [];
  let vorher = null;
  for (let i = 0; i < n; i++) {
    const image = images[i];
    const idx = new Uint8Array(px);
    for (let j = 0, p = 0; j < px; j++, p += 3) {
      idx[j] = tabelle[(image[p] << 16) | (image[p + 1] << 8) | image[p + 2]];
    }

    // Difference rectangle: the stage is largely static, so only the enclosing box of the
    // changed pixels is redrawn. `disposal = 1` (reset nothing) leaves everything else
    // standing, so the box is enough and no transparency is needed.
    let x0 = 0, y0 = 0, x1 = w, y1 = h;
    if (vorher) {
      x0 = w; y0 = h; x1 = -1; y1 = -1;
      for (let y = 0; y < h; y++) {
        const line = y * w;
        for (let x = 0; x < w; x++) {
          if (idx[line + x] !== vorher[line + x]) {
            if (x < x0) x0 = x;
            if (x > x1) x1 = x;
            if (y < y0) y0 = y;
            y1 = y;
          }
        }
      }
      if (x1 < 0) {
        // Bit identical to the predecessor. A frame still has to come out, because otherwise
        // the time would be lost: a 1x1 pixel with the value already there costs about 20 bytes.
        x0 = 0; y0 = 0; x1 = 0; y1 = 0;
      }
      x1 += 1; y1 += 1;
    }
    const bw = x1 - x0, bh = y1 - y0;
    let teilbild = idx;
    if (bw !== w || bh !== h) {
      teilbild = new Uint8Array(bw * bh);
      for (let y = 0; y < bh; y++) {
        teilbild.set(idx.subarray((y0 + y) * w + x0, (y0 + y) * w + x0 + bw), y * bw);
      }
    }

    // The delay in hundredths of a second. A lower bound of 2: browsers silently replace 0 and
    // 1 by 10, which would brake a 12 fps film to 10 fps; with realistic frame distances
    // (about 83 ms) the clamp never takes hold.
    const cs = Math.max(2, Math.round(opt.delaysMs[i] / 10));
    teile.push(Buffer.from([0x21, 0xf9, 0x04, 0x04, ...u16(cs), 0x00, 0x00]));
    teile.push(Buffer.from([0x2c, ...u16(x0), ...u16(y0), ...u16(bw), ...u16(bh), 0x00]));
    const daten = subBloecke(lzw(teilbild, minCodeSize));
    teile.push(Buffer.from([minCodeSize]));
    teile.push(Buffer.from(daten.buffer, daten.byteOffset, daten.length));
    proBild.push(daten.length + 19);
    vorher = idx;
  }
  teile.push(Buffer.from([0x3b]));

  return { bytes: Buffer.concat(teile), colours, gemergt, proBild };
}
