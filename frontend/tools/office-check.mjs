// Prüfer für das „Büro" (src/components/office). Null Abhängigkeiten.
//
//   docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
//     node --experimental-strip-types tools/office-check.mjs
//
// bzw. `npm run check:office` (aus `frontend/`, dann liegt das Backend unter `../backend`).
// Bewusst **nicht** Teil von `npm run build`: der Docker-Bau darf nicht davon abhängen, und
// eine devDependency scheidet aus, weil das Dockerfile bei jedem Bau `npm install` ohne
// Lockfile macht.
//
// **Warum auch `backend/` eingehängt wird**: die Vollständigkeitsprüfung der Werkzeug-Tabelle
// zieht ihre Sollliste aus `backend/app/worker/*.py`. Eine abgeschriebene Liste prüfte nur, ob
// die Abschrift zu sich selbst passt — genau die Drift, die sie verhindern soll, sähe sie nie.
// Ohne eingehängtes Backend bricht der Prüfer deshalb, statt still weniger zu prüfen.
//
// `--experimental-strip-types` ist nötig, weil der Prüfer die Schichten 0 und 1 direkt lädt.
//
//   --bless   schreibt tools/golden.json neu. Siehe die Warnung dort — das Erneuern eines
//             goldenen Bildes ist eine Entscheidung, keine Reparatur.
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
import { ROOM } from "../src/components/office/room.ts";
import { NATIVE_TOOLS, TOOL_ACT, toolAct } from "../src/components/office/toolAct.ts";
import { CAM_FULL, RACK_PX, SEATS_PX, renderFrame } from "../src/components/office/pixel/scene.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(HERE);
const OFFICE_DIR = join(FRONTEND, "src", "components", "office");
const GOLDEN_FILE = join(HERE, "golden.json");
const BLESS = process.argv.includes("--bless");

// ── Schichten (PIXEL-CONTRACT.md Regel 4) ────────────────────────────────────

/** Schicht 2, obwohl `.ts`: die React-nahen Bausteine ohne JSX. */
const LAYER2_TS = new Set(["api", "useOfficeFeed", "useTheme"]);

/** Was Schicht 1 aus Schicht 0 sehen darf — und sonst nichts. */
const LAYER1_MAY_IMPORT_FROM_LAYER0 = new Set(["types", "ids", "const"]);

/**
 * Ordnet eine Datei ihrer Schicht zu. Fail-closed: eine unbekannte `.ts` direkt in
 * `office/` gilt als Schicht 0 und muss also rein sein. Wer bewusst Schicht 2 baut,
 * nimmt `.tsx` oder trägt den Namen in LAYER2_TS ein.
 * @param {string} rel POSIX-Pfad relativ zu OFFICE_DIR, z. B. "pixel/art.ts"
 * @returns {0|1|2}
 */
function layerOf(rel) {
  if (rel.endsWith(".tsx")) return 2;
  const parts = rel.split("/");
  if (parts.length > 1 && parts[0] === "pixel") return 1;
  if (parts.length > 1) return 2; // andere Unterordner sind Schicht 2
  const base = parts[0].replace(/\.[^.]+$/, "");
  return LAYER2_TS.has(base) ? 2 : 0;
}

// ── Dateien einsammeln ───────────────────────────────────────────────────────

/** @returns {string[]} POSIX-Pfade relativ zu OFFICE_DIR */
function collect(dir, out = [], base = dir) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out; // Verzeichnis existiert noch nicht — das ist kein Fehler
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
 * Ersetzt Kommentarinhalte durch Leerzeichen und lässt Zeilenumbrüche stehen, damit
 * Zeilennummern erhalten bleiben. Nötig, weil die Kommentare hier deutsch sind und die
 * verbotenen Bezeichner (`Date.now`, `Math.random`, …) darin **erklärt** werden.
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

// ═══ Prüfung 1 — Reinheits-Grep (Regel 3.1) ══════════════════════════════════
//
// Schicht 0 und 1 dürfen keine Uhr, keinen Würfel und keine Browser-Umgebung anfassen.
// Sonst zeigt dasselbe Log beim zweiten Abspielen ein anderes Bild.

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
  report("Reinheit (Schicht 0+1)", bad.length === 0,
    `${scanned.length} Dateien, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 2 — Schicht-Import-Regel (Regel 4 und 5) ════════════════════════
//
// Prüft drei Dinge in einem Durchgang:
//   · Schicht 0 importiert nur Schicht 0; Schicht 1 nur Schicht 1 + types/ids/const.
//   · Schicht 0 und 1 importieren gar keine Pakete (sonst nicht ohne Bundler ladbar).
//   · Relative Importe tragen die `.ts`-Endung (Nodes ESM-Auflösung kennt keine Ergänzung).

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
        bad.push(`${where}  Paket-Import "${spec}" — Schicht ${f.layer} lädt nichts aus node_modules`);
        continue;
      }
      if (!spec.endsWith(".ts")) {
        bad.push(`${where}  "${spec}" ohne .ts-Endung — Node löst das in ESM nicht auf`);
        continue;
      }
      const target = posix.normalize(posix.join(posix.dirname(f.rel), spec));
      if (target.startsWith("..")) {
        bad.push(`${where}  "${spec}" verlässt office/ — Schicht ${f.layer} bleibt drinnen`);
        continue;
      }
      const tl = layerOf(target);
      const base = target.split("/").pop().replace(/\.[^.]+$/, "");
      if (f.layer === 0 && tl !== 0) {
        bad.push(`${where}  Schicht 0 → Schicht ${tl} ("${spec}")`);
      } else if (f.layer === 1 && tl === 2) {
        bad.push(`${where}  Schicht 1 → Schicht 2 ("${spec}")`);
      } else if (f.layer === 1 && tl === 0 && !LAYER1_MAY_IMPORT_FROM_LAYER0.has(base)) {
        bad.push(`${where}  Schicht 1 → "${base}" — erlaubt sind nur types/ids/const, nie die Engine`);
      }
    }
  }
  report("Schicht-Import-Regel", bad.length === 0,
    `${scanned.length} Dateien, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Werkzeug: tiefer Vergleich, der sagt WO ═══════════════════════════════════
//
// „ungleich" ist als Fehlermeldung wertlos: ein `Frame` hat drei Aktoren zu je dreißig
// Feldern. Der Vergleich unten liefert deshalb den **ersten** Unterschied als Pfad
// (`actors[1:run:8872].pose`) samt erwartet/bekommen — das ist der Unterschied zwischen
// „irgendwas an der Engine" und „die Pose kippt einen Tick zu früh".

/** Kanonische Form: Schlüssel sortiert, `undefined` weg. Nötig, damit ein Feld, das mal
 *  gesetzt und mal weggelassen wird, nicht allein durch die Schlüsselreihenfolge auffällt. */
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

/** Der erste Unterschied als lesbarer Satz, oder `null`. */
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

/** FNV-1a über eine Zeichenkette, als 8-stelliges Hex. `hash32` kommt aus `ids.ts` —
 *  dieselbe Funktion, die auch die Figuren streut, und damit eine weniger im Repo. */
function hex(s) {
  return hash32(s).toString(16).padStart(8, "0");
}

// ═══ Das Log der Fixture ═══════════════════════════════════════════════════════
//
// Bewusst durch den **echten** `Recorder` gedreht statt von Hand zu `LogEntry` gebaut: so
// laufen `mapEvent`, die Dedup über `seq` und die Zeitstempel-Umrechnung mit im Test. Ein
// selbstgebautes Log prüfte die Engine gegen eine zweite Übersetzung derselben Ereignisse.

function buildLog() {
  const rec = new Recorder();
  rec.setRoster(ROSTER);
  for (const ev of EVENTS) rec.push(ev);
  return rec.entries();
}

const LOG = buildLog();
const AT = GOLDEN_OFFSETS.map((off) => T_FROM + off);

/** Fingerabdruck der Fixture. Ändert er sich, ist jedes goldene Bild darunter hinfällig —
 *  und die Meldung soll das sagen, statt acht Bild-Unterschiede zu melden. */
const FIXTURE_HASH = hex(JSON.stringify(EVENTS) + JSON.stringify(ROSTER));

// ═══ Prüfung 3 — goldenes Bild an 8 Zeitpunkten (Regel 3) ═════════════════════
//
// Die eine Aussage, in der der ganze Determinismus steckt: dasselbe Log, derselbe Zeitpunkt,
// dasselbe Bild — auf jedem Rechner, in jeder Zeitzone, bei jedem Lauf.

function goldenFrames() {
  return AT.map((ts, i) => ({ at: GOLDEN_OFFSETS[i], frame: canon(frameAt(LOG, ts)) }));
}

function checkGoldenFrames(golden) {
  if (!golden) return; // Meldung kam schon beim Laden
  const got = goldenFrames();
  const want = golden.frames;
  if (!Array.isArray(want) || want.length !== got.length) {
    report("goldenes Bild (8 Zeitpunkte)", false,
      `tools/golden.json: ${Array.isArray(want) ? want.length : "kein"} Bild(er), erwartet ${got.length}`);
    return;
  }
  if (golden.fixture !== FIXTURE_HASH) {
    report("goldenes Bild (8 Zeitpunkte)", false,
      `tools/fixture.mjs hat sich geändert (${golden.fixture} → ${FIXTURE_HASH})`);
    console.log("            Die goldenen Bilder gehören zur alten Fixture. Erneuern ist eine");
    console.log("            Entscheidung: `node … tools/office-check.mjs --bless`.");
    return;
  }
  const bad = [];
  for (let i = 0; i < got.length; i++) {
    const d = firstDiff(got[i].frame, want[i].frame);
    if (d) bad.push(`tools/golden.json frames[${i}] (t0+${got[i].at} ms)  ${d}`);
  }
  report("goldenes Bild (8 Zeitpunkte)", bad.length === 0,
    `${got.length} Zeitpunkte, ${bad.length} abweichend`);
  for (const b of bad) console.log(`            ${b}`);
  if (bad.length > 0) {
    console.log("            Ist die Änderung gewollt? Dann `--bless` — das ist eine Entscheidung,");
    console.log("            keine Reparatur, und sie gehört in die Commit-Nachricht.");
  }
}

// ═══ Prüfung 4 — Seek-Idempotenz (Regel 3) ════════════════════════════════════
//
// `seek(t)` baut die Engine neu auf. Bleibt dabei irgendwo Zustand am `Replay` hängen (ein
// nicht zurückgesetzter Logzeiger, ein Anker, eine verbrauchte Zeitspanne), liefert der
// zweite Sprung auf denselben Zeitpunkt ein anderes Bild als der erste. Das fällt im Betrieb
// erst auf, wenn jemand zweimal dieselbe Stelle anklickt.

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

    // Und derselbe Zeitpunkt über einen frischen Replay: `frameAt` ist der Einstieg, den
    // die goldenen Bilder benutzen — er muss dasselbe liefern wie ein wiederverwendeter.
    const d2 = firstDiff(canon(frameAt(LOG, AT[i])), erst);
    if (d2) bad.push(`frameAt(t0+${GOLDEN_OFFSETS[i]} ms) ≠ Replay.seek  ${d2}`);
  }
  report("Seek-Idempotenz", bad.length === 0, `${AT.length} Zeitpunkte, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 5 — seek ≡ advance (Regel 3.4) ═══════════════════════════════════
//
// Zurückspulen springt in `REPLAY_STEP_MS`, der Livebetrieb läuft in rAF-Abständen vorwärts.
// Zeigten die beiden Wege denselben Zeitpunkt verschieden, wäre die Zeitleiste eine Lüge über
// die Bühne. Die krummen Schrittweiten sind Absicht: 250 und 100 sind genau die Zahlen, für
// die der Code gebaut ist, 37 und 313 sind es nicht — und ein Fehler, der nur bei einem
// „schönen" Teiler verschwindet, ist kein behobener Fehler.

const STEPS = [250, 100, 37, 313, 1000];

function checkSeekEqualsAdvance() {
  const bad = [];
  for (const step of STEPS) {
    // Der Vorwärtsläufer wird zuerst auf `t0` gesetzt. Nicht aus Bequemlichkeit: `advance(dt)`
    // lässt **Zeit vergehen**, es ist kein Einstiegspunkt. Ein frisch gebauter `Replay` hat noch
    // nichts angewandt, und `advance(0)` steigt sofort wieder aus — „von t0 aus vorwärts, aber
    // null Millisekunden weit" wäre also ein leerer Raum und verglichen mit `seek(t0)`
    // ein Unterschied ohne Aussage. In Schicht 2 stellt sich die Frage nicht: die Bühne ruft
    // immer entweder `seek` oder ein `advance` mit echtem `dt`.
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
    `${STEPS.length} Schrittweiten × ${AT.length} Zeitpunkte, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 6 — dt-Split-Invarianz (Regel 3.4) ═══════════════════════════════
//
// `tick(200)` muss denselben Zustand ergeben wie `tick(25)` achtmal. Bricht bei jeder Phase,
// die aus einem Tick-Zähler statt aus `engine.t` kommt, und bei jedem Übergang, der zum
// Tick-Zeitpunkt statt zu seinem eigenen Zeitpunkt wirkt.
//
// Zweimal geprüft: nackt (ein paar Kommandos, dann Zeit) und über das ganze Kommando-Skript
// der Fixture. Das nackte findet den Fehler, das Skript findet ihn im Zusammenspiel.

function tickBy(eng, total, step) {
  let left = total;
  while (left > 0) {
    const s = left < step ? left : step;
    eng.tick(s);
    left -= s;
  }
}

/** Ein Aufwärmskript aus echten Kommandos — ohne Aktoren tickt eine leere Engine ins Leere
 *  und die Invarianz wäre trivial erfüllt. */
const NACKT = [
  { k: "ensureActor", id: "run:1", role: "exec_agent", issue: "TRA-1", phase: "execute", model: "sonnet" },
  { k: "ensureActor", id: "run:2", role: "review_agent", issue: "TRA-1", phase: "execute", model: "sonnet", parent: "run:1" },
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

/** Das Kommando-Skript der Fixture: je Zeitstempel alle Kommandos, danach die geklemmte
 *  Lücke bis zum nächsten — genau die Zerlegung, die `Replay.run` fährt. */
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

  // Die Regel wörtlich: 200 in einem Stück gegen 25 achtmal, über mehrere Gesamtdauern —
  // sonst träfe man nur zufällig einen Übergang.
  for (const total of [200, 1000, 4200, 12_000]) {
    const grob = nacktesBild(200, total);
    const fein = nacktesBild(25, total);
    const d = firstDiff(fein, grob);
    if (d) bad.push(`nackt, ${total} ms: tick(25)×${total / 25} ≠ tick(200)×${total / 200}  ${d}`);
  }

  // Und dasselbe über das ganze Skript, mit einer krummen Schrittweite als drittem Zeugen.
  const grob = skriptBild(200);
  for (const step of [25, 7, 1000]) {
    const d = firstDiff(skriptBild(step), grob);
    if (d) bad.push(`Skript (${SKRIPT.length} Kommandostellen), tick(${step}) ≠ tick(200)  ${d}`);
  }

  report("dt-Split-Invarianz", bad.length === 0, `${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Der ctx-Stub — Regel 2.1 als ausführbarer Test ═══════════════════════════
//
// Ein `Proxy`, der genau drei Namen durchlässt und bei **jedem** anderen Zugriff wirft. Damit
// ist der Pixel-Vertrag kein Dokument mehr, sondern eine Zusicherung: ein `ctx.beginPath()`
// in Schicht 1 bricht diesen Lauf, nicht erst die Bildqualität in einem fremden Browser.
//
// Zusätzlich wird auf ganzzahlige Koordinaten bestanden (Regel 2.3). Ein `fillRect` mit
// x = 12.4 läuft im Browser über zwei Spalten mit halber Deckkraft — bei einer laufenden
// Figur flimmert das sichtbar, und niemand sucht den Fehler in der Engine.

const CTX_ERLAUBT = new Set(["fillStyle", "globalAlpha", "fillRect"]);

function strictCtx() {
  const ops = [];
  const ziel = {
    fillStyle: "#000000",
    globalAlpha: 1,
    fillRect(x, y, w, h) {
      for (const [name, v] of [["x", x], ["y", y], ["w", w], ["h", h]]) {
        if (!Number.isInteger(v)) {
          throw new Error(`fillRect(${x}, ${y}, ${w}, ${h}): ${name} ist nicht ganzzahlig `
            + "(PIXEL-CONTRACT.md Regel 2.3)");
        }
      }
      ops.push(`${x},${y},${w},${h},${String(ziel.fillStyle)},${ziel.globalAlpha}`);
    },
  };
  const wirf = (was, prop) => {
    throw new Error(`ctx.${String(prop)} ${was} — Regel 2.1 erlaubt nur `
      + "fillStyle, globalAlpha und fillRect");
  };
  const proxy = new Proxy(ziel, {
    get(t, prop) {
      if (typeof prop !== "string" || !CTX_ERLAUBT.has(prop)) wirf("gelesen", prop);
      return t[prop];
    },
    set(t, prop, value) {
      if (typeof prop !== "string" || !CTX_ERLAUBT.has(prop)) wirf("geschrieben", prop);
      t[prop] = value;
      return true;
    },
    has(t, prop) { wirf("mit `in` geprüft", prop); },
    deleteProperty(t, prop) { wirf("gelöscht", prop); },
    ownKeys() { throw new Error("ctx wurde aufgezählt — Regel 2.1"); },
  });
  return { ctx: proxy, ops };
}

const GRADES_ZU_PRUEFEN = ["day", "night"];

// ═══ Prüfung 7 — Pixel-Vertrag (ctx-Proxy) ════════════════════════════════════

function checkCtxProxy() {
  const bad = [];
  let bilder = 0;
  for (let i = 0; i < AT.length; i++) {
    const frame = frameAt(LOG, AT[i]);
    for (const grade of GRADES_ZU_PRUEFEN) {
      // Einmal mit der Vollbildkamera (der Kontext wird unverändert durchgereicht) und einmal
      // gezoomt (die `viewOf`-Hülle rechnet jedes Rechteck um) — beide müssen den Vertrag
      // halten, und nur der zweite Fall prüft die Hülle.
      for (const cam of [CAM_FULL, { x: 160, y: 120, zoom: 4 }]) {
        // Der Sitzungsfilter dimmt über eine zweite `Ctx`-Hülle — die muss den Vertrag
        // genauso halten wie der nackte Kontext, sonst wäre der Filter das eine Loch darin.
        for (const dimmed of [undefined, new Set(["run:8872", "run:8873"])]) {
          const { ctx } = strictCtx();
          try {
            renderFrame(ctx, frame, cam, grade,
              { selected: "run:8871", hover: "run:8872", dimmed });
            bilder++;
          } catch (e) {
            bad.push(`pixel/scene.ts · t0+${GOLDEN_OFFSETS[i]} ms · ${grade} · zoom ${cam.zoom}`
              + ` · ${dimmed ? "gedimmt" : "ungedimmt"}  ${e && e.message ? e.message : e}`);
          }
        }
      }
    }
  }
  report("Pixel-Vertrag (ctx-Proxy)", bad.length === 0, `${bilder} Bilder, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 8 — goldene Pixel-Ops (Regel 2) ══════════════════════════════════
//
// Kopfloses Bildvergleichen ohne Browser und ohne Abhängigkeit: derselbe Stub sammelt die
// `fillRect`-Folge samt Farbe und Deckkraft, daraus wird ein Hex je Bild. Der ctx-Proxy sagt
// „nichts Verbotenes benutzt", diese Prüfung sagt „und es kam dasselbe heraus". Eine um einen
// Pixel verrutschte Möbelzeile sieht der Proxy nicht, dieser Hash schon.

function opsHashes() {
  const out = [];
  for (let i = 0; i < AT.length; i++) {
    const frame = frameAt(LOG, AT[i]);
    const eintrag = { at: GOLDEN_OFFSETS[i] };
    for (const grade of GRADES_ZU_PRUEFEN) {
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
    report("goldene Pixel-Ops-Hashes", false,
      `pixel/scene.ts wirft: ${e && e.message ? e.message : e}`);
    return;
  }
  const want = golden.ops;
  if (!Array.isArray(want) || want.length !== got.length) {
    report("goldene Pixel-Ops-Hashes", false,
      `tools/golden.json: ${Array.isArray(want) ? want.length : "kein"} Eintrag/Einträge, erwartet ${got.length}`);
    return;
  }
  const bad = [];
  for (let i = 0; i < got.length; i++) {
    for (const grade of GRADES_ZU_PRUEFEN) {
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
  report("goldene Pixel-Ops-Hashes", bad.length === 0,
    `${got.length} Bilder × ${GRADES_ZU_PRUEFEN.length} Tageszeiten, ${bad.length} abweichend`);
  for (const b of bad) console.log(`            ${b}`);
  if (bad.length > 0) {
    console.log("            Gewollt? Dann `--bless`. Ein neues goldenes Bild ist eine Entscheidung.");
  }
}

// ═══ Prüfung 9 — Werkzeug-Tabelle vollständig ═════════════════════════════════
//
// Die höchstwertige Prüfung, weil sie die einzige ist, die über die Sprachgrenze schaut. Die
// Sollliste wird aus `backend/app/worker/*.py` **gezogen**, nicht abgeschrieben: eine Abschrift
// prüfte nur, ob die Abschrift zu sich selbst passt, und ein neu gebautes Werkzeug fiele
// stillschweigend in die MCP-Heuristik — der Zuschauer sähe dann ein Bild, das niemand
// vergeben hat.
//
// Vier Aussagen in einem Durchgang (Logik übernommen aus der Wegwerf-Prüfung der Welle F):
//   1. `NATIVE_TOOLS` ≡ Schlüsselmenge von `TOOL_ACT`,
//   2. jedes native Werkzeug löst über die **Tabelle** auf und nie über die Heuristik,
//   3. die Heuristik feuert ausschließlich bei MCP-Namen (`server__tool`),
//   4. die Sollliste des Backends ≡ `NATIVE_TOOLS`.

/** Wo `backend/app/worker` liegen kann. Erster Treffer gewinnt. */
const BACKEND_ORTE = [
  join(FRONTEND, "..", "backend", "app", "worker"),  // Repo im Ganzen (npm run, Repo-Mount)
  "/backend/app/worker",                             // backend/ separat eingehängt
];

function backendDir() {
  for (const dir of BACKEND_ORTE) {
    try {
      readFileSync(join(dir, "runtime.py"), "utf8");
      return dir;
    } catch { /* nächster Ort */ }
  }
  return null;
}

/** Die Sollliste, aus dem Quelltext des Workers gelesen.
 *
 *  `runtime.py` schreibt Werkzeugdefinitionen als `"name": "<wort>"`; ein Parameter heißt
 *  `"name": {…}` und fällt damit durch dasselbe Muster heraus. `tools_memory.py` und
 *  `tools_traccoon.py` bauen ihre Definitionen über `_def("<name>", …)`. */
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

/** Die Heuristik darf **nur** MCP sehen und nur `read`/`write`/`run` liefern. */
const HEURISTIK_FAELLE = [
  ["get_something", "other"],          // kein "__" → keine Heuristik, auch wenn „get" passte
  ["list_orders", "other"],
  ["", "other"],
  ["srv__get_thing", "read"],
  ["srv__create_thing", "write"],
  ["srv__trigger_sync", "run"],
  ["obsidian__obsidian_get_note", "read"],
  ["homeassistant__call_service", "run"],
  ["srv__zzz_qqq", "other"],           // nichts erkannt → ehrlich „other"
  ["srv__fetch_web_page", "read"],     // „web" im Namen darf NIE browse ergeben
  ["srv__open_web_browser", "other"],  // browse/delegate sind aus der Heuristik unerreichbar
  ["proj__fs_read", "read"],           // MCP-Präfix vor einem Tabellennamen
  ["srv__constructor", "other"],       // Prototypenkette darf kein Bild liefern
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

  // 2 — jedes native Werkzeug über die Tabelle, nie über die Heuristik
  for (const n of nat) {
    if (!has(TOOL_ACT, n)) continue; // schon gemeldet
    if (n.includes("__")) {
      bad.push(`toolAct.ts: "${n}" enthält "__" und sähe damit wie ein MCP-Name aus`);
      continue;
    }
    if (toolAct(n) !== TOOL_ACT[n]) {
      bad.push(`toolAct.ts: toolAct("${n}") = ${toolAct(n)} — Tabelle sagt ${TOOL_ACT[n]}`);
    }
  }

  // 3 — Heuristik nur für MCP
  for (const [name, want] of HEURISTIK_FAELLE) {
    const got = toolAct(name);
    if (got !== want) bad.push(`toolAct.ts: toolAct("${name}") = ${got} — erwartet ${want}`);
  }

  // 4 — Sollliste aus dem Backend
  const dir = backendDir();
  if (dir === null) {
    bad.push("Backend nicht erreichbar — gesucht in: " + [...new Set(BACKEND_ORTE)].join(", "));
    bad.push('Abhilfe: docker run … -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w …');
    bad.push("Die Sollliste kommt aus dem Backend; sie hier abzuschreiben hieße, die Drift zu");
    bad.push("verstecken, die diese Prüfung finden soll.");
  } else {
    const soll = backendSoll(dir);
    const natSet = new Set(nat);
    for (const n of [...soll].sort()) {
      if (!natSet.has(n)) bad.push(`${relative(FRONTEND, dir) || dir}: "${n}" kennt das Backend, toolAct.ts nicht`);
    }
    for (const n of nat) {
      if (!soll.has(n)) bad.push(`toolAct.ts: "${n}" kennt das Frontend, das Backend nicht (mehr?)`);
    }
    report("Werkzeug-Tabelle vollständig", bad.length === 0,
      `${nat.length} native Werkzeuge, Backend-Soll ${soll.size}, ${bad.length} Verstöße`);
    for (const b of bad) console.log(`            ${b}`);
    return;
  }

  report("Werkzeug-Tabelle vollständig", false, `${nat.length} native Werkzeuge, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 10 — die doppelte Sitzgeometrie (Regel 4) ════════════════════════
//
// `pixel/scene.ts` hält die Plätze ein zweites Mal, weil Schicht 1 `room.ts` nicht sehen darf.
// Genau dafür exportiert sie `SEATS_PX`: die Doppelung ist damit keine stille Gefahr mehr,
// sondern eine geprüfte Zusage. Läuft sie auseinander, sitzen die Figuren neben ihren Stühlen —
// und das sieht aus wie ein Renderfehler, ist aber eine Zahl in einer von zwei Dateien.

function checkSeatGeometry() {
  const bad = [];
  const soll = ROOM.seats;
  if (soll.length !== SEATS_PX.length) {
    bad.push(`room.ts hat ${soll.length} Sitze, pixel/scene.ts ${SEATS_PX.length}`);
  }
  const n = Math.min(soll.length, SEATS_PX.length);
  for (let i = 0; i < n; i++) {
    const wx = Math.round(soll[i].sit.x * POS_SCALE);
    const wy = Math.round(soll[i].sit.y * POS_SCALE);
    const g = SEATS_PX[i].sit;
    if (g.x !== wx || g.y !== wy) {
      bad.push(`pixel/scene.ts SEATS_PX[${i}].sit = (${g.x}, ${g.y}) — `
        + `room.ts ROOM.seats[${i}].sit × POS_SCALE = (${wx}, ${wy})`);
    }
    if (SEATS_PX[i].flip !== soll[i].flip) {
      bad.push(`pixel/scene.ts SEATS_PX[${i}].flip = ${SEATS_PX[i].flip} — `
        + `room.ts sagt ${soll[i].flip}`);
    }
  }
  report("Sitzgeometrie room ≡ scene", bad.length === 0, `${n} Sitze, ${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 11 — Rack-Geometrie (Regel 4) ════════════════════════════════════
//
// Dieselbe Doppelung wie bei den Sitzen und derselbe Zwang dahinter: `pixel/scene.ts` darf
// `room.ts` nicht sehen und hält den Standplatz vor dem Serverschrank deshalb ein zweites Mal.
// Läuft er auseinander, geht die auslösende Figur an eine Stelle, an der kein Schrank steht,
// und das Urteil („✓"/„✗") schwebt daneben in der Luft. Beides sieht nach einem Zeichenfehler
// aus und ist eine Zahl in einer von zwei Dateien.

function checkRackGeometry() {
  const bad = [];
  const soll = ROOM.rack;
  if (!soll) {
    bad.push("room.ts: ROOM.rack fehlt");
  } else {
    const wx = Math.round(soll.x * POS_SCALE);
    const wy = Math.round(soll.y * POS_SCALE);
    if (RACK_PX.x !== wx || RACK_PX.y !== wy) {
      bad.push(`pixel/scene.ts RACK_PX = (${RACK_PX.x}, ${RACK_PX.y}) — `
        + `room.ts ROOM.rack × POS_SCALE = (${wx}, ${wy})`);
    }
  }
  report("Rack-Geometrie room ≡ scene", bad.length === 0,
    soll ? `ROOM.rack = (${soll.x}, ${soll.y}), ${bad.length} Verstöße` : `${bad.length} Verstöße`);
  for (const b of bad) console.log(`            ${b}`);
}

// ═══ Prüfung 12 — das Rack leuchtet in der Fixture wirklich ═══════════════════
//
// Die Ops-Hashes (Prüfung 8) sind blind dafür, ob sie den neuen Zeichenzweig je betreten haben:
// sie melden nur „dieselben Aufrufe wie beim letzten Bless". Ohne einen goldenen Rahmen mit
// leuchtendem Rack wäre die ganze LED-Zeichnung ungeprüft und der Bless-Diff bedeutungslos.
// Diese Prüfung sagt, dass die Fixture den Code auch wirklich ausführt.

function checkRackImFrame() {
  const gesehen = new Set();
  for (const ts of AT) gesehen.add(frameAt(LOG, ts).rack.state);
  const leuchtend = [...gesehen].filter((s) => s !== "idle").sort();
  const ok = leuchtend.length >= 2;
  report("Rack leuchtet in der Fixture", ok,
    `Zustände über 8 Bilder: ${[...gesehen].sort().join(", ")}`);
  if (!ok) {
    console.log("            tools/fixture.mjs braucht `deploy`-Ereignisse VOR einem goldenen");
    console.log("            Zeitpunkt — sonst prüfen die Ops-Hashes die neue Zeichnung nie.");
  }
}

// ── golden.json ──────────────────────────────────────────────────────────────

const GOLDEN_WARNUNG = [
  "ERZEUGT von tools/office-check.mjs --bless. NICHT von Hand ändern.",
  "Dies ist das eingefrorene Bild, das tools/fixture.mjs ergeben MUSS. Weicht ein Lauf ab,",
  "hat sich das Verhalten geändert — erneuern ist eine Entscheidung, keine Reparatur, und die",
  "Begründung gehört in die Commit-Nachricht.",
];

function ladeGolden() {
  try {
    return JSON.parse(readFileSync(GOLDEN_FILE, "utf8"));
  } catch (e) {
    report("goldenes Bild (8 Zeitpunkte)", false,
      `tools/golden.json nicht lesbar (${e && e.code ? e.code : e}) — `
      + "einmalig erzeugen mit `--bless`");
    return null;
  }
}

function bless() {
  const daten = {
    _warnung: GOLDEN_WARNUNG,
    fixture: FIXTURE_HASH,
    frames: goldenFrames(),
    ops: opsHashes(),
  };
  writeFileSync(GOLDEN_FILE, `${JSON.stringify(daten, null, 1)}\n`, "utf8");
  console.log("");
  console.log("  ██ --bless: tools/golden.json wurde NEU GESCHRIEBEN.");
  console.log("  ██ Das ist eine Entscheidung, keine Reparatur: ab jetzt gilt das neue Verhalten");
  console.log("  ██ als richtig. Sieh dir den Diff an und schreib in die Commit-Nachricht, WARUM");
  console.log(`  ██ sich das Bild ändern durfte. Fixture-Fingerabdruck: ${FIXTURE_HASH}`);
  console.log("");
}

// ── Lauf ─────────────────────────────────────────────────────────────────────

console.log(`office-check — ${FILES.length} Dateien unter src/components/office`);
console.log(`  Schicht 0: ${FILES.filter((f) => f.layer === 0).length} · ` +
  `Schicht 1: ${FILES.filter((f) => f.layer === 1).length} · ` +
  `Schicht 2: ${FILES.filter((f) => f.layer === 2).length}`);
console.log(`  Fixture: ${EVENTS.length} Ereignisse, ${LOG.length} Logzeilen, `
  + `${(T_TO - T_FROM) / 1000} s, Fingerabdruck ${FIXTURE_HASH}`);

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
checkSeatGeometry();
checkRackGeometry();
checkRackImFrame();

if (failed > 0) {
  console.log(`\n${failed} Prüfung(en) fehlgeschlagen — siehe PIXEL-CONTRACT.md`);
  process.exit(1);
}
console.log("\nalles grün");
