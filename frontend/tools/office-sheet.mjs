// tools/office-sheet.mjs: a character sheet of the office people, without a browser.
//
// The room shot shows whether a figure works **in** the room; it does not show whether the
// sprite itself is right. At twelve pixels of face, a mistake in the art (an eye row covered by
// the fringe, a hand that ends in the sleeve) is invisible in the room and obvious here.
//
// Draws one figure per pose and per hairstyle on a plain ground, big.
//
//   docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
//     node --experimental-strip-types tools/office-sheet.mjs [--scale 6] [--grade day]

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

import { drawActor } from "../src/components/office/pixel/person.ts";
import { GRADES, lookOf, palFor, rolesSeed } from "../src/components/office/pixel/palette.ts";
import { hash32 } from "../src/components/office/ids.ts";
import { POS_SCALE } from "../src/components/office/const.ts";
import { rasterCtx } from "./film/raster.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const SCALE = Number(arg("--scale", "6"));
const GRADE = arg("--grade", "day");
const OUT = join(HERE, "shots");

// ── PNG (the same writer as office-shot.mjs, kept local so both stay standalone) ─────────────

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

function png(buf, w, h, z) {
  const W = w * z, H = h * z;
  const raw = Buffer.alloc(H * (W * 3 + 1));
  let o = 0;
  for (let y = 0; y < H; y++) {
    raw[o++] = 0;
    const sy = (y / z) | 0;
    for (let x = 0; x < W; x++) {
      const si = (sy * w + ((x / z) | 0)) * 3;
      raw[o++] = buf[si]; raw[o++] = buf[si + 1]; raw[o++] = buf[si + 2];
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(H, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ── the sheet ───────────────────────────────────────────────────────────────
//
// One column per figure, one row per situation. The situations are built as `ActorState`
// literals, because that is what `drawActor` takes; it is the same call the scene makes.

const CELL_W = 34;
const CELL_H = 56;
const ROLES = ["developer", "architect", "assistant", "project_manager", "code_reviewer", "news"];

/** The situations, as the fields of `ActorState` that `actOf`/`stanceOf` read. */
const SITUATIONS = [
  { name: "stand", pose: "stand" },
  { name: "walk", pose: "walk" },
  { name: "talk", pose: "stand", say: "hi" },
  { name: "type", pose: "sit", act: "write", busy: 1 },
  { name: "read", pose: "sit", act: "read", busy: 1 },
  { name: "wait", pose: "sit", waiting: true },
  { name: "back", pose: "sit", deskIndex: -1, act: "write", busy: 1 },
];

const COLS = ROLES.length;
const ROWS = SITUATIONS.length;
const W = CELL_W * COLS;
const H = CELL_H * ROWS;

const { ctx, buf, reset } = rasterCtx(W, H);
reset();

const pal0 = GRADES[GRADE];
// A plain ground in floor colour: a figure is judged against the surface it stands on.
ctx.fillStyle = pal0.floor;
ctx.fillRect(0, 0, W, H);
// A row separator, so the cells can be told apart.
ctx.fillStyle = pal0.floorLo;
for (let r = 1; r < ROWS; r++) ctx.fillRect(0, r * CELL_H, W, 1);

for (let c = 0; c < COLS; c++) {
  const role = ROLES[c];
  const seed = hash32(`run:${1000 + c * 7}`);
  const look = lookOf(seed, rolesSeed(role, seed));
  const pal = palFor(GRADE, look);
  for (let r = 0; r < ROWS; r++) {
    const sit = SITUATIONS[r];
    /** Everything `drawActor` reads. Positions come back out of `POS_SCALE`, so the figure
     *  lands in the middle of its cell. */
    const a = {
      id: `run:${c}-${r}`, seed, role,
      x: (c * CELL_W + CELL_W / 2) / POS_SCALE / 2,
      y: ((r + 1) * CELL_H - 6) / POS_SCALE / 2,
      sub: { x: 0, y: 0 },
      pose: sit.pose, act: sit.act, busy: sit.busy ?? 0,
      waiting: sit.waiting === true, say: sit.say,
      deskIndex: sit.deskIndex ?? 0, flip: false, retired: false,
    };
    drawActor(ctx, a, 3000 + r * 137, pal);
  }
}

mkdirSync(OUT, { recursive: true });
const name = `sheet-${GRADE}.png`;
writeFileSync(join(OUT, name), png(buf, W, H, SCALE));
console.log(`${name}  ${W * SCALE}x${H * SCALE}  ${COLS} figures x ${ROWS} situations`);
