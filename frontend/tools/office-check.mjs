// Prüfer für das „Büro" (src/components/office). Null Abhängigkeiten.
//
//   docker run --rm -v "$PWD/frontend":/w -w /w node:22-alpine \
//     node --experimental-strip-types tools/office-check.mjs
//
// bzw. `npm run check:office`. Bewusst **nicht** Teil von `npm run build`: der Docker-Bau darf
// nicht davon abhängen, und eine devDependency scheidet aus, weil das Dockerfile bei jedem
// Bau `npm install` ohne Lockfile macht.
//
// `--experimental-strip-types` ist nötig, sobald der Prüfer die Schichten 0 und 1 direkt lädt
// (die goldenen Prüfungen der Welle M). Für die zwei bereits gebauten Prüfungen genügt Lesen.
//
// Regelwerk: src/components/office/PIXEL-CONTRACT.md
//
// Stand: Welle A′ liefert das Gerüst mit zwei fertigen Prüfungen. Die übrigen sind unten als
// ausgeschaltete, benannte Platzhalter eingetragen — Welle M füllt sie.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, posix, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(HERE);
const OFFICE_DIR = join(FRONTEND, "src", "components", "office");

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

/** Noch nicht gebaute Prüfung. Bricht nicht — Welle M ersetzt den Aufruf durch echten Code. */
function pending(name, detail) {
  console.log(`  offen   ${name.padEnd(34)} ${detail ?? ""}`.trimEnd());
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

// ═══ Platzhalter — Welle M ═══════════════════════════════════════════════════

// TODO(Welle M): `frameAt(FIXTURE, ts)` an 8 Zeitpunkten gegen tools/golden.json vergleichen.
//   Fixture ist ein festes Ev[]-Log (echte Session, einmal exportiert, danach eingefroren).
//   Golden neu schreiben nur mit `--update` und begründetem Commit.
function checkGoldenFrames() { pending("goldenes Bild (8 Zeitpunkte)", "Welle M · tools/golden.json"); }

// TODO(Welle M): seek(t) zweimal hintereinander muss denselben Frame liefern wie einmal.
//   Fängt Engine-Zustand, der das Zurücksetzen überlebt.
function checkSeekIdempotent() { pending("Seek-Idempotenz", "Welle M"); }

// TODO(Welle M): seek(t) ≡ von t0 aus vorwärts advance()n bis t. Sichert außerdem die
//   abgeleiteten Checkpoints ab, falls sie je gebraucht werden (seekMitCheckpoints ≡ seekVonNull).
function checkSeekEqualsAdvance() { pending("seek ≡ advance", "Welle M"); }

// TODO(Welle M): tick(200) ≡ tick(25)×8. Die Regel, die Live-Betrieb und Replay gleichsetzt
//   (PIXEL-CONTRACT.md 3.4). Bricht bei jeder Phase, die aus einem Tick-Zähler kommt.
function checkDtSplit() { pending("dt-Split-Invarianz", "Welle M"); }

// TODO(Welle M): ctx-Proxy, der alles außer fillStyle/globalAlpha/fillRect wirft, durch
//   pixel/scene.ts jagen. Der Pixel-Vertrag als ausführbarer Test (Regel 2.1).
function checkCtxProxy() { pending("Pixel-Vertrag (ctx-Proxy)", "Welle M"); }

// TODO(Welle M): Ops-Folge der Zeichenschicht hashen und gegen golden.json halten —
//   fängt stille Verschiebungen, die der ctx-Proxy nicht sieht.
function checkPixelOpHashes() { pending("goldene Pixel-Ops-Hashes", "Welle M"); }

// TODO(Welle M): jedes native Traccoon-Werkzeug muss in toolAct.ts stehen (fs_*, open_tasks,
//   erinnere_dich/vergiss/gedaechtnis_suchen, alle traccoon_*). Nur MCP (server__tool) darf
//   in die Präfix-Heuristik fallen. Sollliste aus dem Backend ziehen, nicht abschreiben.
function checkToolTable() { pending("Werkzeug-Tabelle vollständig", "Welle M"); }

// ── Lauf ─────────────────────────────────────────────────────────────────────

console.log(`office-check — ${FILES.length} Dateien unter src/components/office`);
console.log(`  Schicht 0: ${FILES.filter((f) => f.layer === 0).length} · ` +
  `Schicht 1: ${FILES.filter((f) => f.layer === 1).length} · ` +
  `Schicht 2: ${FILES.filter((f) => f.layer === 2).length}`);

checkPurity();
checkLayers();
checkGoldenFrames();
checkSeekIdempotent();
checkSeekEqualsAdvance();
checkDtSplit();
checkCtxProxy();
checkPixelOpHashes();
checkToolTable();

if (failed > 0) {
  console.log(`\n${failed} Prüfung(en) fehlgeschlagen — siehe PIXEL-CONTRACT.md`);
  process.exit(1);
}
console.log("\nalles grün");
