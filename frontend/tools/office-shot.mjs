// tools/office-shot.mjs: render office frames to PNG files, without a browser.
//
// Why: the office is pixel art, and pixel art is judged by looking at it. `office-check`
// proves that the picture is *the same as last time*; it says nothing about whether it looks
// good. This renders the same fixture moments the checker uses and writes them out, so a
// change to the art can be seen before it is blessed.
//
// It uses the film rasteriser (the `Ctx` of the pixel contract, three channels) and therefore
// real office code, not a second renderer. The PNG writer below is the whole dependency: node
// brings zlib along.
//
//   docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
//     node --experimental-strip-types tools/office-shot.mjs [--out DIR] [--at MS,MS] [--grade day|night]

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

import { EVENTS, GOLDEN_OFFSETS, ROSTER, T_FROM } from "./fixture.mjs";
import { Recorder } from "../src/components/office/recorder.ts";
import { frameAt } from "../src/components/office/replay.ts";
import { CAM_FULL, renderFrame } from "../src/components/office/pixel/scene.ts";
import { PIX } from "../src/components/office/const.ts";
import { rasterCtx } from "./film/raster.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

// ── arguments ───────────────────────────────────────────────────────────────

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const OUT = join(HERE, arg("--out", "shots"));
const GRADES = arg("--grade", "day,night").split(",");
const SCALE = Number(arg("--scale", "2"));
const AT = arg("--at", GOLDEN_OFFSETS.join(",")).split(",").map(Number);
/** `--crop x,y,w,h` in buffer pixels. Without it the whole picture. */
const CROP = process.argv.includes("--crop")
  ? arg("--crop", "0,0,0,0").split(",").map(Number) : undefined;

// ── PNG ─────────────────────────────────────────────────────────────────────

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}

/** RGB buffer (w*h*3, row by row) to a PNG, nearest-neighbour scaled by `z`.
 *  `crop` is `[x, y, w, h]` in buffer pixels, for looking at a single figure closely. */
function png(buf, w, h, z, crop) {
  const [cx, cy, cw, ch] = crop ?? [0, 0, w, h];
  const W = cw * z, H = ch * z;
  const raw = Buffer.alloc(H * (W * 3 + 1));
  let o = 0;
  for (let y = 0; y < H; y++) {
    raw[o++] = 0;                                   // filter: none
    const sy = cy + ((y / z) | 0);
    for (let x = 0; x < W; x++) {
      const si = (sy * w + cx + ((x / z) | 0)) * 3;
      raw[o++] = buf[si]; raw[o++] = buf[si + 1]; raw[o++] = buf[si + 2];
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(H, 4);
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ── render ──────────────────────────────────────────────────────────────────

const rec = new Recorder();
rec.setRoster(ROSTER);
for (const ev of EVENTS) rec.push(ev);
const LOG = rec.entries();

mkdirSync(OUT, { recursive: true });

for (const grade of GRADES) {
  for (const off of AT) {
    const frame = frameAt(LOG, T_FROM + off);
    const { ctx, buf, reset } = rasterCtx(PIX.w, PIX.h);
    reset();
    renderFrame(ctx, frame, CAM_FULL, grade);
    const name = `office-${grade}-${String(off).padStart(5, "0")}.png`;
    writeFileSync(join(OUT, name), png(buf, PIX.w, PIX.h, SCALE, CROP));
    console.log(`${name}  ${PIX.w * SCALE}x${PIX.h * SCALE}  ${frame.actors.length} actors`);
  }
}
