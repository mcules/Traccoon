// Checker for the "office" (src/components/office). No dependencies.
//
//   docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
//     node --experimental-strip-types tools/office-check.mjs
//
// respectively `npm run check:office` (from `frontend/`, and then the backend lies under `../backend`).
// Deliberately **not** part of `npm run build`: the Docker build must not depend on it, and a
// devDependency is out because the Dockerfile runs `npm install` without a lockfile on every
// build.
//
// **Why `backend/` is mounted as well**: the completeness check of the tool table draws its
// target list from `backend/app/worker/*.py`. A copied list would only check whether the copy
// matches itself, and exactly the drift it is meant to prevent it would never see. Without a
// mounted backend the checker therefore breaks instead of silently checking less.
//
// `--experimental-strip-types` is needed because the checker loads layers 0 and 1 directly.
//
//   --bless   rewrites tools/golden.json. See the warning there: renewing a golden picture is
//             a decision, not a repair.
//
// Regelwerk: src/components/office/PIXEL-CONTRACT.md

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, posix, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { EVENTS, GOLDEN_OFFSETS, ROSTER, T_FROM, T_TO } from "./fixture.mjs";
import { hash32 } from "../src/components/office/ids.ts";
import { MAX_GAP_MS, POS_SCALE } from "../src/components/office/const.ts";
import { Engine } from "../src/components/office/engine.ts";
import { Recorder } from "../src/components/office/recorder.ts";
import { Replay, frameAt } from "../src/components/office/replay.ts";
import { BLOCKED, ROOM, route } from "../src/components/office/room.ts";
import { NATIVE_TOOLS, TOOL_ACT, toolAct } from "../src/components/office/toolAct.ts";
import { CAM_FULL, RACK_PX, SEATS_PX, renderFrame } from "../src/components/office/pixel/scene.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(HERE);
const OFFICE_DIR = join(FRONTEND, "src", "components", "office");
const GOLDEN_FILE = join(HERE, "golden.json");
const BLESS = process.argv.includes("--bless");

// ── Layers (PIXEL-CONTRACT.md rule 4) ────────────────────────────────────────

/** Layer 2 despite `.ts`: the React-near building blocks without JSX. */
const LAYER2_TS = new Set(["api", "useOfficeFeed", "useTheme"]);

/** What layer 1 may see of layer 0, and nothing else. */
const LAYER1_MAY_IMPORT_FROM_LAYER0 = new Set(["types", "ids", "const"]);

/**
 * Assigns a file to its layer. Fail-closed: an unknown `.ts` directly in `office/` counts as
 * layer 0 and therefore has to be pure. Whoever deliberately builds layer 2 takes `.tsx` or
 * enters the name in LAYER2_TS.
 * @param {string} rel POSIX-Pfad relativ zu OFFICE_DIR, z. B. "pixel/art.ts"
 * @returns {0|1|2}
 */
function layerOf(rel) {
  if (rel.endsWith(".tsx")) return 2;
  const parts = rel.split("/");
  if (parts.length > 1 && parts[0] === "pixel") return 1;
  if (parts.length > 1) return 2; // other subfolders are layer 2
  const base = parts[0].replace(/\.[^.]+$/, "");
  return LAYER2_TS.has(base) ? 2 : 0;
}

// ── Collect the files ────────────────────────────────────────────────────────

/** @returns {string[]} POSIX-Pfade relativ zu OFFICE_DIR */
function collect(dir, out = [], base = dir) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out; // the directory does not exist yet — that is no error
  }
  for (const e of entries.sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const abs = join(dir, e.name);
    if (e.isDirectory()) collect(abs, out, base);
    else if (/\.tsx?$/.test(e.name)) out.push(relative(base, abs).split(/[\\/]/).join("/"));
  }
  return out;
}

const FILES = collect(OFFICE_DIR).map((rel) => {
  const src = readFileSync(join(OFFICE_DIR, rel), "utf8");
  return { rel, src, layer: layerOf(rel), code: stripComments(src) };
});

/**
 * Replaces comment contents by spaces and leaves the line breaks standing, so that line
 * numbers are kept. Necessary because the forbidden identifiers (`Date.now`, `Math.random`, …)
 * are **explained** in the comments.
 */
function stripComments(src) {
  let out = "";
  let i = 0;
  let mode = "code"; // code | line | block | sq | dq | tpl
  while (i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (mode === "code") {
      if (c === "/" && n === "/") { mode = "line"; out += "  "; i += 2; continue; }
      if (c === "/" && n === "*") { mode = "block"; out += "  "; i += 2; continue; }
      if (c === "'") mode = "sq";
      else if (c === '"') mode = "dq";
      else if (c === "`") mode = "tpl";
      out += c; i++; continue;
    }
    if (mode === "line") {
      if (c === "\n") { mode = "code"; out += "\n"; } else out += " ";
      i++; continue;
    }
    if (mode === "block") {
      if (c === "*" && n === "/") { mode = "code"; out += "  "; i += 2; continue; }
      out += c === "\n" ? "\n" : " "; i++; continue;
    }
    // in einem String-Literal
    if (c === "\\") { out += "  "; i += 2; continue; }
    if ((mode === "sq" && c === "'") || (mode === "dq" && c === '"') || (mode === "tpl" && c === "`")) mode = "code";
    out += c; i++;
  }
  return out;
}

// ── Ergebnis-Ausgabe ─────────────────────────────────────────────────────────

let failed = 0;

function report(name, ok, detail) {
  if (ok) console.log(`  OK      ${name.padEnd(34)} ${detail ?? ""}`.trimEnd());
  else { failed++; console.log(`  FEHLER  ${name.padEnd(34)} ${detail ?? ""}`.trimEnd()); }
}

function lineOf(src, index) {
  let line = 1;
  for (let i = 0; i < index && i < src.length; i++) if (src[i] === "\n") line++;
  return line;
}

// ═══ Check 1: purity grep (rule 3.1) ═════════════════════════════════════════
//
// Layers 0 and 1 must touch no clock, no dice and no browser environment. Otherwise the same
// log shows a different picture on the second replay.

const FORBIDDEN = [
  "Math.random", "Date.now", "performance.now", "new Date",
  "window.", "document.", "localStorage", "toLocale",
];

function checkPurity() {
  const scanned = FILES.filter((f) => f.layer <= 1);
  const bad = [];
  for (const f of scanned) {
    const lines = f.code.split("\n");
    lines.forEach((line, i) => {
      for (const needle of FORBIDDEN) {
        if (line.includes(needle)) bad.push(`${f.rel}:${i + 1}  ${needle}`);
      }
    });
  }
  report("purity (layers 0+1)", bad.length === 0,
    `${scanned.length} files, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 2: the layer import rule (rules 4 and 5) ══════════════════════════
//
// Checks three things in one pass:
//   · layer 0 imports only layer 0; layer 1 only layer 1 plus types/ids/const.
//   · layers 0 and 1 import no packages at all (otherwise they are not loadable without a bundler).
//   · relative imports carry the `.ts` extension (Node's ESM resolution knows no completion).

const IMPORT_RES = [
  /(?:^|\n)\s*import\s+(?:type\s+)?[^;'"]*?from\s*["']([^"']+)["']/g,
  /(?:^|\n)\s*export\s+(?:type\s+)?[^;'"]*?from\s*["']([^"']+)["']/g,
  /(?:^|\n)\s*import\s*["']([^"']+)["']/g,
  /import\s*\(\s*["']([^"']+)["']\s*\)/g,
];

function importsOf(code) {
  const found = [];
  for (const re of IMPORT_RES) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(code)) !== null) found.push({ spec: m[1], at: m.index });
  }
  return found;
}

function checkLayers() {
  const scanned = FILES.filter((f) => f.layer <= 1);
  const bad = [];
  for (const f of scanned) {
    for (const { spec, at } of importsOf(f.code)) {
      const where = `${f.rel}:${lineOf(f.code, at)}`;
      if (!spec.startsWith(".")) {
        bad.push(`${where}  package import "${spec}" — layer ${f.layer} loads nothing out of node_modules`);
        continue;
      }
      if (!spec.endsWith(".ts")) {
        bad.push(`${where}  "${spec}" without a .ts ending — Node does not resolve that in ESM`);
        continue;
      }
      const target = posix.normalize(posix.join(posix.dirname(f.rel), spec));
      if (target.startsWith("..")) {
        bad.push(`${where}  "${spec}" leaves office/ — layer ${f.layer} stays inside`);
        continue;
      }
      const tl = layerOf(target);
      const base = target.split("/").pop().replace(/\.[^.]+$/, "");
      if (f.layer === 0 && tl !== 0) {
        bad.push(`${where}  layer 0 → layer ${tl} ("${spec}")`);
      } else if (f.layer === 1 && tl === 2) {
        bad.push(`${where}  layer 1 → layer 2 ("${spec}")`);
      } else if (f.layer === 1 && tl === 0 && !LAYER1_MAY_IMPORT_FROM_LAYER0.has(base)) {
        bad.push(`${where}  layer 1 → "${base}" — allowed are only types/ids/const, never the engine`);
      }
    }
  }
  report("layer import rule", bad.length === 0,
    `${scanned.length} files, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Tool: a deep comparison that says WHERE ═══════════════════════════════════
//
// "unequal" is worthless as an error message: a `Frame` has three actors with thirty fields
// each. The comparison below therefore delivers the **first** difference as a path
// (`actors[1:run:8872].pose`) including expected and got, which is the difference between
// "something about the engine" and "the pose tips one tick too early".

/** Canonical form: keys sorted, `undefined` gone. Necessary so that a field that is sometimes
 *  set and sometimes left out does not stand out through the key order alone. */
function canon(v) {
  if (v === undefined) return null;
  if (v === null || typeof v !== "object") return v;
  if (Array.isArray(v)) return v.map(canon);
  const out = {};
  for (const k of Object.keys(v).sort()) {
    if (v[k] === undefined) continue;
    out[k] = canon(v[k]);
  }
  return out;
}

function typeOf(v) {
  return v === null ? "null" : Array.isArray(v) ? "array" : typeof v;
}

/** The first difference as a readable sentence, or `null`. */
function firstDiff(got, want, path = "") {
  if (got === want) return null;
  const here = path || "<Wurzel>";
  const tg = typeOf(got);
  const tw = typeOf(want);
  if (tg !== tw) return `${here}: ${tg} ${JSON.stringify(got)} — erwartet ${tw} ${JSON.stringify(want)}`;
  if (tg === "array") {
    if (got.length !== want.length) {
      return `${here}.length: ${got.length} — erwartet ${want.length}`;
    }
    for (let i = 0; i < got.length; i++) {
      const tag = got[i] && typeof got[i] === "object" && typeof got[i].id === "string"
        ? `[${i}:${got[i].id}]` : `[${i}]`;
      const d = firstDiff(got[i], want[i], `${path}${tag}`);
      if (d) return d;
    }
    return null;
  }
  if (tg === "object") {
    const keys = [...new Set([...Object.keys(got), ...Object.keys(want)])].sort();
    for (const k of keys) {
      const d = firstDiff(got[k], want[k], path ? `${path}.${k}` : k);
      if (d) return d;
    }
    return null;
  }
  return `${here}: ${JSON.stringify(got)} — erwartet ${JSON.stringify(want)}`;
}

/** FNV-1a over a string, as an 8 digit hex. `hash32` comes from `ids.ts`, the same function
 *  that spreads the figures, and therefore one function less in the repository. */
function hex(s) {
  return hash32(s).toString(16).padStart(8, "0");
}

// ═══ The log of the fixture ════════════════════════════════════════════════════
//
// Deliberately turned through the **real** `Recorder` instead of built to `LogEntry` by hand:
// that way `mapEvent`, the dedup over `seq` and the timestamp conversion run along in the test.
// A self-built log would check the engine against a second translation of the same events.

function buildLog() {
  const rec = new Recorder();
  rec.setRoster(ROSTER);
  for (const ev of EVENTS) rec.push(ev);
  return rec.entries();
}

const LOG = buildLog();
const AT = GOLDEN_OFFSETS.map((off) => T_FROM + off);

/** Fingerprint of the fixture. If it changes, every golden picture below it is void, and the
 *  message should say that instead of reporting eight picture differences. */
const FIXTURE_HASH = hex(JSON.stringify(EVENTS) + JSON.stringify(ROSTER));

// ═══ Check 3: the golden picture at 8 moments (rule 3) ════════════════════════
//
// The one statement the whole determinism sits in: the same log, the same moment, the same
// picture, on every machine, in every time zone, on every run.

function goldenFrames() {
  return AT.map((ts, i) => ({ at: GOLDEN_OFFSETS[i], frame: canon(frameAt(LOG, ts)) }));
}

function checkGoldenFrames(golden) {
  if (!golden) return; // the message came while loading already
  const got = goldenFrames();
  const want = golden.frames;
  if (!Array.isArray(want) || want.length !== got.length) {
    report("golden image (8 moments)", false,
      `tools/golden.json: ${Array.isArray(want) ? want.length : "kein"} Bild(er), erwartet ${got.length}`);
    return;
  }
  if (golden.fixture !== FIXTURE_HASH) {
    report("golden image (8 moments)", false,
      `tools/fixture.mjs has changed (${golden.fixture} → ${FIXTURE_HASH})`);
    console.log("            The golden images belong to the old fixture. Renewing them is a");
    console.log("            Entscheidung: `node … tools/office-check.mjs --bless`.");
    return;
  }
  const bad = [];
  for (let i = 0; i < got.length; i++) {
    const d = firstDiff(got[i].frame, want[i].frame);
    if (d) bad.push(`tools/golden.json frames[${i}] (t0+${got[i].at} ms)  ${d}`);
  }
  report("golden image (8 moments)", bad.length === 0,
    `${got.length} moments, ${bad.length} deviating`);
  for (const b of bad) console.log(`            ${b}`);
  if (bad.length > 0) {
    console.log("            Is the change wanted? Then `--bless` — that is a decision,");
    console.log("            not a repair, and it belongs in the commit message.");
  }
}

// ═══ Check 4: seek idempotency (rule 3) ═══════════════════════════════════════
//
// `seek(t)` rebuilds the engine. If state stays hanging on the `Replay` anywhere (a log pointer
// not reset, an anchor, a spent time span), the second jump to the same moment delivers a
// different picture from the first. In operation that only stands out when somebody clicks the
// same place twice.

function checkSeekIdempotent() {
  const bad = [];
  for (let i = 0; i < AT.length; i++) {
    const r = new Replay(LOG);
    r.seek(AT[i]);
    const erst = canon(r.frame());
    r.seek(AT[i]);
    const zweit = canon(r.frame());
    const d = firstDiff(zweit, erst);
    if (d) bad.push(`seek(t0+${GOLDEN_OFFSETS[i]} ms) zweimal  ${d}`);

    // And the same moment over a fresh replay: `frameAt` is the entry the golden pictures use,
    // and it has to deliver the same as a reused one.
    const d2 = firstDiff(canon(frameAt(LOG, AT[i])), erst);
    if (d2) bad.push(`frameAt(t0+${GOLDEN_OFFSETS[i]} ms) ≠ Replay.seek  ${d2}`);
  }
  report("seek idempotence", bad.length === 0, `${AT.length} moments, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 5: seek is advance (rule 3.4) ═════════════════════════════════════
//
// Rewinding jumps in `REPLAY_STEP_MS`, while live operation runs forward in rAF intervals. If
// the two ways showed the same moment differently, the timeline would be a lie about the
// stage. The odd step sizes are deliberate: 250 and 100 are exactly the numbers the code is
// built for, 37 and 313 are not, and an error that disappears only with a "nice" divisor is
// not a fixed error.

const STEPS = [250, 100, 37, 313, 1000];

function checkSeekEqualsAdvance() {
  const bad = [];
  for (const step of STEPS) {
    // The forward runner is set to `t0` first. Not out of convenience: `advance(dt)` lets
    // **time pass**, it is not an entry point. A freshly built `Replay` has applied nothing
    // yet, and `advance(0)` gets out again immediately, so "forward from t0, but zero
    // milliseconds far" would be an empty room and compared with `seek(t0)` a difference
    // without a statement. In layer 2 the question does not arise: the stage always calls
    // either `seek` or an `advance` with a real `dt`.
    const vor = new Replay(LOG);
    vor.seek(T_FROM);
    for (let i = 0; i < AT.length; i++) {
      const ziel = AT[i];
      let wache = 0;
      while (vor.position < ziel) {
        if (++wache > 100_000) break;
        vor.advance(Math.min(step, ziel - vor.position));
      }
      const gesprungen = new Replay(LOG);
      gesprungen.seek(ziel);
      const d = firstDiff(canon(vor.frame()), canon(gesprungen.frame()));
      if (d) bad.push(`advance(${step} ms) bis t0+${GOLDEN_OFFSETS[i]} ms ≠ seek  ${d}`);
    }
  }
  report("seek ≡ advance", bad.length === 0,
    `${STEPS.length} step sizes × ${AT.length} moments, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 6: dt split invariance (rule 3.4) ══════════════════════════════════
//
// `tick(200)` has to give the same state as `tick(25)` eight times. It breaks with every phase
// that comes from a tick counter instead of from `engine.t`, and with every transition that
// acts at the tick moment instead of at its own moment.
//
// Checked twice: bare (a few commands, then time) and over the whole command script of the
// fixture. The bare one finds the error, the script finds it in the interplay.

function tickBy(eng, total, step) {
  let left = total;
  while (left > 0) {
    const s = left < step ? left : step;
    eng.tick(s);
    left -= s;
  }
}

/** A warm-up script of real commands: without actors an empty engine ticks into nothing and
 *  the invariance would be trivially fulfilled. */
const NACKT = [
  { k: "ensureActor", id: "run:1", role: "exec_agent", issue: "ABC-1", phase: "execute", model: "sonnet" },
  { k: "ensureActor", id: "run:2", role: "review_agent", issue: "ABC-1", phase: "execute", model: "sonnet", parent: "run:1" },
  { k: "spawn", id: "run:2", parent: "run:1", role: "review_agent" },
  { k: "say", id: "run:1", text: "Ich lese das." },
  { k: "tool", id: "run:2", act: "read", tool: "fs_read", target: "auth.py" },
  { k: "deliver", id: "run:2", to: "run:1", text: "fertig" },
];

function nacktesBild(step, total) {
  const eng = new Engine();
  for (const c of NACKT) eng.apply(c);
  tickBy(eng, total, step);
  return canon(eng.frame());
}

/** The command script of the fixture: per timestamp all commands, then the clamped gap up to
 *  the next one, exactly the decomposition `Replay.run` drives. */
function script() {
  const out = [];
  let i = 0;
  while (i < LOG.length) {
    const at = LOG[i].ts;
    const cmds = [];
    while (i < LOG.length && LOG[i].ts === at) { cmds.push(...LOG[i].cmds); i++; }
    out.push({ at, cmds, dt: 0 });
  }
  for (let k = 0; k < out.length; k++) {
    const roh = k + 1 < out.length ? out[k + 1].at - out[k].at : 4000;
    out[k].dt = Math.max(0, Math.min(MAX_GAP_MS, roh));
  }
  return out;
}

const SKRIPT = script();

function skriptBild(step) {
  const eng = new Engine();
  for (const e of SKRIPT) {
    for (const c of e.cmds) eng.apply(c);
    tickBy(eng, e.dt, step);
  }
  return canon(eng.frame());
}

function checkDtSplit() {
  const bad = [];

  // The rule literally: 200 in one piece against 25 eight times, over several total durations;
  // otherwise one would hit a transition only by chance.
  for (const total of [200, 1000, 4200, 12_000]) {
    const grob = nacktesBild(200, total);
    const fein = nacktesBild(25, total);
    const d = firstDiff(fein, grob);
    if (d) bad.push(`nackt, ${total} ms: tick(25)×${total / 25} ≠ tick(200)×${total / 200}  ${d}`);
  }

  // And the same over the whole script, with an odd step size as a third witness.
  const grob = skriptBild(200);
  for (const step of [25, 7, 1000]) {
    const d = firstDiff(skriptBild(step), grob);
    if (d) bad.push(`Skript (${SKRIPT.length} Kommandostellen), tick(${step}) ≠ tick(200)  ${d}`);
  }

  report("dt split invariance", bad.length === 0, `${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ The ctx stub: rule 2.1 as an executable test ═════════════════════════════
//
// A `Proxy` that lets exactly three names through and raises on **every** other access. That
// makes the pixel contract no longer a document but an assurance: a `ctx.beginPath()` in layer
// 1 breaks this run, not only the picture quality in a foreign browser.
//
// In addition integer coordinates are insisted on (rule 2.3). A `fillRect` with x = 12.4 runs
// over two columns with half opacity in the browser; with a walking figure that flickers
// visibly, and nobody looks for the error in the engine.

const CTX_ERLAUBT = new Set(["fillStyle", "globalAlpha", "fillRect"]);

function strictCtx() {
  const ops = [];
  const ziel = {
    fillStyle: "#000000",
    globalAlpha: 1,
    fillRect(x, y, w, h) {
      for (const [name, v] of [["x", x], ["y", y], ["w", w], ["h", h]]) {
        if (!Number.isInteger(v)) {
          throw new Error(`fillRect(${x}, ${y}, ${w}, ${h}): ${name} is not a whole number `
            + "(PIXEL-CONTRACT.md rule 2.3)");
        }
      }
      ops.push(`${x},${y},${w},${h},${String(ziel.fillStyle)},${ziel.globalAlpha}`);
    },
  };
  const throw_ = (was, prop) => {
    throw new Error(`ctx.${String(prop)} ${was} — rule 2.1 allows only `
      + "fillStyle, globalAlpha und fillRect");
  };
  const proxy = new Proxy(ziel, {
    get(t, prop) {
      if (typeof prop !== "string" || !CTX_ERLAUBT.has(prop)) throw_("gelesen", prop);
      return t[prop];
    },
    set(t, prop, value) {
      if (typeof prop !== "string" || !CTX_ERLAUBT.has(prop)) throw_("geschrieben", prop);
      t[prop] = value;
      return true;
    },
    has(t, prop) { throw_("checked with `in`", prop); },
    deleteProperty(t, prop) { throw_("deleted", prop); },
    ownKeys() { throw new Error("ctx was enumerated — rule 2.1"); },
  });
  return { ctx: proxy, ops };
}

const GRADES_TO_CHECK = ["day", "night"];

// ═══ Check 7: the pixel contract (ctx proxy) ══════════════════════════════════

function checkCtxProxy() {
  const bad = [];
  let images = 0;
  for (let i = 0; i < AT.length; i++) {
    const frame = frameAt(LOG, AT[i]);
    for (const grade of GRADES_TO_CHECK) {
      // Once with the full screen camera (the context is passed through unchanged) and once
      // zoomed (the `viewOf` wrapper converts every rectangle): both have to keep the contract,
      // and only the second case checks the wrapper.
      for (const cam of [CAM_FULL, { x: 160, y: 120, zoom: 4 }]) {
        // The session filter dims over a second `Ctx` wrapper, and that has to keep the
        // contract just like the bare context; otherwise the filter would be the one hole in it.
        for (const dimmed of [undefined, new Set(["run:8872", "run:8873"])]) {
          const { ctx } = strictCtx();
          try {
            renderFrame(ctx, frame, cam, grade,
              { selected: "run:8871", hover: "run:8872", dimmed });
            images++;
          } catch (e) {
            bad.push(`pixel/scene.ts · t0+${GOLDEN_OFFSETS[i]} ms · ${grade} · zoom ${cam.zoom}`
              + ` · ${dimmed ? "gedimmt" : "ungedimmt"}  ${e && e.message ? e.message : e}`);
          }
        }
      }
    }
  }
  report("pixel contract (ctx proxy)", bad.length === 0, `${images} images, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 8: golden pixel ops (rule 2) ═══════════════════════════════════════
//
// Headless picture comparison without a browser and without a dependency: the same stub
// collects the `fillRect` sequence including colour and opacity, and a hex per picture comes
// out of it. The ctx proxy says "nothing forbidden used", this check says "and the same came
// out". A furniture row shifted by one pixel the proxy does not see, this hash does.

function opsHashes() {
  const out = [];
  for (let i = 0; i < AT.length; i++) {
    const frame = frameAt(LOG, AT[i]);
    const eintrag = { at: GOLDEN_OFFSETS[i] };
    for (const grade of GRADES_TO_CHECK) {
      const { ctx, ops } = strictCtx();
      renderFrame(ctx, frame, CAM_FULL, grade);
      eintrag[grade] = { n: ops.length, hash: hex(ops.join("\n")) };
    }
    out.push(eintrag);
  }
  return out;
}

function checkPixelOpHashes(golden) {
  if (!golden) return;
  let got;
  try {
    got = opsHashes();
  } catch (e) {
    report("golden pixel ops hashes", false,
      `pixel/scene.ts wirft: ${e && e.message ? e.message : e}`);
    return;
  }
  const want = golden.ops;
  if (!Array.isArray(want) || want.length !== got.length) {
    report("golden pixel ops hashes", false,
      `tools/golden.json: ${Array.isArray(want) ? want.length : "no"} entry/entries, expected ${got.length}`);
    return;
  }
  const bad = [];
  for (let i = 0; i < got.length; i++) {
    for (const grade of GRADES_TO_CHECK) {
      const g = got[i][grade];
      const w = want[i] ? want[i][grade] : undefined;
      if (!w) { bad.push(`tools/golden.json ops[${i}].${grade} fehlt`); continue; }
      if (g.hash === w.hash && g.n === w.n) continue;
      bad.push(`tools/golden.json ops[${i}].${grade} (t0+${got[i].at} ms)  `
        + `${g.n} Ops/${g.hash} — erwartet ${w.n} Ops/${w.hash}`
        + (g.n === w.n ? " (gleiche Anzahl: verschobene Koordinaten oder Farben)"
          : ` (${g.n > w.n ? "+" : ""}${g.n - w.n} Zeichenaufrufe)`));
    }
  }
  report("golden pixel ops hashes", bad.length === 0,
    `${got.length} images × ${GRADES_TO_CHECK.length} times of day, ${bad.length} deviating`);
  for (const b of bad) console.log(`            ${b}`);
  if (bad.length > 0) {
    console.log("            Wanted? Then `--bless`. A new golden image is a decision.");
  }
}

// ═══ Check 9: the tool table is complete ══════════════════════════════════════
//
// The most valuable check, because it is the only one that looks across the language boundary.
// The target list is **drawn** from `backend/app/worker/*.py`, not copied: a copy would only
// check whether the copy matches itself, and a newly built tool would fall silently into the
// MCP heuristic, so the viewer would see a picture nobody
// ever assigned.
//
// Four statements in one pass (the logic taken over from the throwaway check of an earlier wave):
//   1. `NATIVE_TOOLS` equals the key set of `TOOL_ACT`,
//   2. every native tool resolves over the **table** and never over the heuristic,
//   3. the heuristic fires exclusively on MCP names (`server__tool`),
//   4. the target list of the backend equals `NATIVE_TOOLS`.

/** Where `backend/app/worker` can lie. The first hit wins. */
const BACKEND_ORTE = [
  join(FRONTEND, "..", "backend", "app", "worker"),  // Repo im Ganzen (npm run, Repo-Mount)
  "/backend/app/worker",                             // backend/ mounted separately
];

function backendDir() {
  for (const dir of BACKEND_ORTE) {
    try {
      readFileSync(join(dir, "runtime.py"), "utf8");
      return dir;
    } catch { /* the next place */ }
  }
  return null;
}

/** The target list, read from the source of the worker.
 *
 *  `runtime.py` writes tool definitions as `"name": "<word>"`; a parameter is called
 *  `"name": {…}` and thereby falls out of the same pattern. `tools_memory.py` and
 *  `tools_traccoon.py` build their definitions over `_def("<name>", …)`. */
function backendSoll(dir) {
  const out = new Set();
  const rt = readFileSync(join(dir, "runtime.py"), "utf8");
  for (const m of rt.matchAll(/"name":\s*"([a-z_][a-z0-9_]*)"/g)) out.add(m[1]);
  for (const f of ["tools_memory.py", "tools_traccoon.py"]) {
    const src = readFileSync(join(dir, f), "utf8");
    for (const m of src.matchAll(/_def\(\s*"([a-z_][a-z0-9_]*)"/g)) out.add(m[1]);
  }
  return out;
}

/** The heuristic may see **only** MCP and deliver only `read`/`write`/`run`. */
const HEURISTIK_FAELLE = [
  ["get_something", "other"],          // no "__" → no heuristic, even though "get" would fit
  ["list_orders", "other"],
  ["", "other"],
  ["srv__get_thing", "read"],
  ["srv__create_thing", "write"],
  ["srv__trigger_sync", "run"],
  ["obsidian__obsidian_get_note", "read"],
  ["homeassistant__call_service", "run"],
  ["srv__zzz_qqq", "other"],           // nichts erkannt → ehrlich „other"
  ["srv__fetch_web_page", "read"],     // „web" im Namen darf NIE browse ergeben
  ["srv__open_web_browser", "other"],  // browse/delegate are out of reach for the heuristic
  ["proj__fs_read", "read"],           // an MCP prefix in front of a table name
  ["srv__constructor", "other"],       // the prototype chain must deliver no image
];

function checkToolTable() {
  const bad = [];
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

  // 1 — Tabelle ≡ gepflegte Liste
  const nat = [...NATIVE_TOOLS];
  const keys = Object.keys(TOOL_ACT);
  for (const n of nat) if (!has(TOOL_ACT, n)) bad.push(`toolAct.ts: "${n}" in NATIVE_TOOLS, nicht in TOOL_ACT`);
  for (const k of keys) if (nat.indexOf(k) < 0) bad.push(`toolAct.ts: "${k}" in TOOL_ACT, nicht in NATIVE_TOOLS`);
  for (let i = 0; i < nat.length; i++) {
    if (nat.indexOf(nat[i]) !== i) bad.push(`toolAct.ts: "${nat[i]}" steht doppelt in NATIVE_TOOLS`);
  }

  // 2: every native tool over the table, never over the heuristic
  for (const n of nat) {
    if (!has(TOOL_ACT, n)) continue; // schon gemeldet
    if (n.includes("__")) {
      bad.push(`toolAct.ts: "${n}" contains "__" and would therefore look like an MCP name`);
      continue;
    }
    if (toolAct(n) !== TOOL_ACT[n]) {
      bad.push(`toolAct.ts: toolAct("${n}") = ${toolAct(n)} — Tabelle sagt ${TOOL_ACT[n]}`);
    }
  }

  // 3: the heuristic only for MCP
  for (const [name, want] of HEURISTIK_FAELLE) {
    const got = toolAct(name);
    if (got !== want) bad.push(`toolAct.ts: toolAct("${name}") = ${got} — erwartet ${want}`);
  }

  // 4: the target list from the backend
  const dir = backendDir();
  if (dir === null) {
    bad.push("Backend nicht erreichbar — gesucht in: " + [...new Set(BACKEND_ORTE)].join(", "));
    bad.push('Abhilfe: docker run … -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w …');
    bad.push("The wanted list comes out of the backend; copying it here would mean hiding the");
    bad.push("very drift this check is meant to find.");
  } else {
    const wanted = backendSoll(dir);
    const nativeSet = new Set(nat);
    for (const n of [...wanted].sort()) {
      if (!nativeSet.has(n)) bad.push(`${relative(FRONTEND, dir) || dir}: "${n}" is known to the backend, not to toolAct.ts`);
    }
    for (const n of nat) {
      if (!wanted.has(n)) bad.push(`toolAct.ts: "${n}" is known to the frontend, not (any more?) to the backend`);
    }
    report("tool table complete", bad.length === 0,
      `${nat.length} native tools, backend wants ${wanted.size}, ${bad.length} violations`);
    for (const b of bad) console.log(`            ${b}`);
    return;
  }

  report("tool table complete", false, `${nat.length} native tools, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check: no route runs through the furniture ═══════════════════════════════
//
// The engine has **no** collision detection at tick time and is not supposed to have any: a
// figure is not an obstacle, and a route recomputed while walking would depend on the tick
// size and would take the replay with it (rule 3.4). What it does have is a route computed
// once, out of a fixed list of rectangles.
//
// So this is the check that has to hold: **every** route between two places anybody actually
// walks to stays out of every piece of furniture. Sampled densely along the polyline, because
// a route can enter a rectangle between two waypoints without either of them being inside it.
//
// Rectangles containing one of the two ends are left out: a seat stands in front of its own
// desk, and the coffee target is the machine itself. Whoever walks *to* something is allowed
// to reach it.

function insideBox(p, r, pad) {
  return Math.abs(p.x - r.x) <= r.w / 2 + pad && Math.abs(p.y - r.y) <= r.h / 2 + pad;
}

function checkRoutesFree() {
  const spots = [
    ["door", ROOM.door], ["coffee", ROOM.coffee], ["rack", ROOM.rack],
    ...ROOM.huddle.map((h, i) => [`huddle${i}`, h]),
    ...ROOM.seats.map((s, i) => [`seat${i}`, s.sit]),
  ];
  const bad = [];
  let legs = 0;
  for (const [an, a] of spots) {
    for (const [bn, b] of spots) {
      if (an === bn) continue;
      legs++;
      const path = route(a, b, BLOCKED);
      let prev = a;
      for (const q of path) {
        const n = Math.max(1, Math.round(Math.hypot(q.x - prev.x, q.y - prev.y) / 8));
        for (let i = 1; i <= n && bad.length < 6; i++) {
          const pt = { x: prev.x + (q.x - prev.x) * i / n, y: prev.y + (q.y - prev.y) * i / n };
          for (let k = 0; k < BLOCKED.length; k++) {
            const r = BLOCKED[k];
            if (insideBox(a, r, 0) || insideBox(b, r, 0)) continue;
            if (insideBox(pt, r, 0)) { bad.push(`${an} → ${bn} runs through furniture #${k}`); break; }
          }
        }
        prev = q;
      }
    }
  }
  report("routes avoid the furniture", bad.length === 0,
    `${legs} routes, ${BLOCKED.length} obstacles, ${bad.length} violations`);
  for (const line of bad) console.log(`            ${line}`);
}

// ═══ Check 10: the doubled seat geometry (rule 4) ═════════════════════════════
//
// `pixel/scene.ts` holds the seats a second time, because layer 1 must not see `room.ts`.
// Exactly for that it exports `SEATS_PX`: the doubling is thereby no longer a silent danger
// but a checked promise. If it drifts apart, the figures sit beside their chairs, and that
// looks like a rendering bug but is a number in one of two files.

function checkSeatGeometry() {
  const bad = [];
  const wanted = ROOM.seats;
  if (wanted.length !== SEATS_PX.length) {
    bad.push(`room.ts has ${wanted.length} seats, pixel/scene.ts ${SEATS_PX.length}`);
  }
  const n = Math.min(wanted.length, SEATS_PX.length);
  for (let i = 0; i < n; i++) {
    const wx = Math.round(wanted[i].sit.x * POS_SCALE);
    const wy = Math.round(wanted[i].sit.y * POS_SCALE);
    const g = SEATS_PX[i].sit;
    if (g.x !== wx || g.y !== wy) {
      bad.push(`pixel/scene.ts SEATS_PX[${i}].sit = (${g.x}, ${g.y}) — `
        + `room.ts ROOM.seats[${i}].sit × POS_SCALE = (${wx}, ${wy})`);
    }
    if (SEATS_PX[i].flip !== wanted[i].flip) {
      bad.push(`pixel/scene.ts SEATS_PX[${i}].flip = ${SEATS_PX[i].flip} — `
        + `room.ts sagt ${wanted[i].flip}`);
    }
  }
  report("seat geometry room ≡ scene", bad.length === 0, `${n} seats, ${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 11: rack geometry (rule 4) ═════════════════════════════════════════
//
// The same doubling as with the seats and the same compulsion behind it: `pixel/scene.ts` must
// not see `room.ts` and therefore holds the standing place in front of the server rack a second
// time. If it drifts apart, the triggering figure goes to a place where no rack stands, and
// the verdict ("✓"/"✗") floats beside it in the air. Both look like a drawing bug and are a
// number in one of two files.

function checkRackGeometry() {
  const bad = [];
  const wanted = ROOM.rack;
  if (!wanted) {
    bad.push("room.ts: ROOM.rack fehlt");
  } else {
    const wx = Math.round(wanted.x * POS_SCALE);
    const wy = Math.round(wanted.y * POS_SCALE);
    if (RACK_PX.x !== wx || RACK_PX.y !== wy) {
      bad.push(`pixel/scene.ts RACK_PX = (${RACK_PX.x}, ${RACK_PX.y}) — `
        + `room.ts ROOM.rack × POS_SCALE = (${wx}, ${wy})`);
    }
  }
  report("rack geometry room ≡ scene", bad.length === 0,
    wanted ? `ROOM.rack = (${wanted.x}, ${wanted.y}), ${bad.length} violations` : `${bad.length} violations`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Check 12: the rack really lights up in the fixture ═══════════════════════
//
// The ops hashes (check 8) are blind to whether they ever entered the new drawing branch: they
// only report "the same calls as at the last bless". Without a golden frame with a glowing
// rack the whole LED drawing would be unchecked and the bless diff meaningless. This check
// says that the fixture really executes the code.

function checkRackInFrame() {
  const seen = new Set();
  for (const ts of AT) seen.add(frameAt(LOG, ts).rack.state);
  const lit = [...seen].filter((s) => s !== "idle").sort();
  const ok = lit.length >= 2;
  report("the rack lights up in the fixture", ok,
    `states across 8 images: ${[...seen].sort().join(", ")}`);
  if (!ok) {
    console.log("            tools/fixture.mjs needs `deploy` events BEFORE a golden");
    console.log("            moment — otherwise the ops hashes never check the new drawing.");
  }
}

// ── golden.json ──────────────────────────────────────────────────────────────

const GOLDEN_WARNING = [
  "GENERATED by tools/office-check.mjs --bless. Do NOT change by hand.",
  "This is the frozen image that tools/fixture.mjs MUST produce. If a run deviates,",
  "the behaviour has changed — renewing is a decision, not a repair, and the",
  "reason belongs in the commit message.",
];

function ladeGolden() {
  try {
    return JSON.parse(readFileSync(GOLDEN_FILE, "utf8"));
  } catch (e) {
    report("golden image (8 moments)", false,
      `tools/golden.json nicht lesbar (${e && e.code ? e.code : e}) — `
      + "einmalig erzeugen mit `--bless`");
    return null;
  }
}

function bless() {
  const daten = {
    _warnung: GOLDEN_WARNING,
    fixture: FIXTURE_HASH,
    frames: goldenFrames(),
    ops: opsHashes(),
  };
  writeFileSync(GOLDEN_FILE, `${JSON.stringify(daten, null, 1)}\n`, "utf8");
  console.log("");
  console.log("  ██ --bless: tools/golden.json was REWRITTEN.");
  console.log("  ██ This is a decision, not a repair: from now on the new behaviour counts");
  console.log("  ██ as right. Look at the diff and write into the commit message WHY");
  console.log(`  ██ the image was allowed to change. Fixture fingerprint: ${FIXTURE_HASH}`);
  console.log("");
}

// ── Lauf ─────────────────────────────────────────────────────────────────────

console.log(`office-check — ${FILES.length} files under src/components/office`);
console.log(`  layer 0: ${FILES.filter((f) => f.layer === 0).length} · ` +
  `layer 1: ${FILES.filter((f) => f.layer === 1).length} · ` +
  `layer 2: ${FILES.filter((f) => f.layer === 2).length}`);
console.log(`  fixture: ${EVENTS.length} events, ${LOG.length} log rows, `
  + `${(T_TO - T_FROM) / 1000} s, fingerprint ${FIXTURE_HASH}`);

if (BLESS) bless();

const GOLDEN = ladeGolden();

checkPurity();
checkLayers();
checkGoldenFrames(GOLDEN);
checkSeekIdempotent();
checkSeekEqualsAdvance();
checkDtSplit();
checkCtxProxy();
checkPixelOpHashes(GOLDEN);
checkToolTable();
checkRoutesFree();
checkSeatGeometry();
checkRackGeometry();
checkRackInFrame();

if (failed > 0) {
  console.log(`\n${failed} check(s) failed — see PIXEL-CONTRACT.md`);
  process.exit(1);
}
console.log("\nall green");
