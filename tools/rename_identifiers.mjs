/**
 * A tool from the switch to English: it renamed the German identifiers of the frontend over
 * the syntax tree. Kept because the same job comes up again with every larger rename, not
 * because anything still has to be moved.
 *
 * Rename German identifiers to English in TypeScript/TSX — over the syntax tree.
 *
 * Text replacement is not an option here: the i18n keys are German by construction
 * (`tr("standorte.geraete")`), and so are CSS class names and user-facing strings. Only
 * identifiers may move, and only the compiler knows which token is one.
 *
 *   node tools/rename_identifiers.mjs --check frontend/src
 *   node tools/rename_identifiers.mjs --write frontend/src
 */
import fs from "node:fs";
import path from "node:path";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";

const woerterbuch = JSON.parse(
  fs.readFileSync(new URL("./eindeutschen.json", import.meta.url), "utf8")).stamm;

// Names that belong to the platform or to libraries — never renamed.
const TABU = new Set([
  "window", "document", "location", "history", "navigator", "console", "fetch", "Map", "Set",
  "Array", "Object", "String", "Number", "Boolean", "Date", "JSON", "Promise", "Error",
  "React", "useState", "useEffect", "useMemo", "useRef", "useCallback", "props", "children",
  "key", "value", "type", "name", "id", "data", "className", "style", "ref", "list",
]);

// Reserved words of the language. A rename onto one of them does not fail with a clear
// message but with a parse error three lines further down: `neu` became `new`.
const RESERVIERT = new Set([
  "new", "class", "function", "return", "var", "let", "const", "if", "else", "for", "while",
  "do", "switch", "case", "break", "continue", "delete", "in", "of", "typeof", "instanceof",
  "this", "super", "extends", "import", "export", "default", "try", "catch", "finally",
  "throw", "void", "with", "yield", "await", "async", "static", "null", "true", "false",
  "undefined", "enum", "interface", "implements", "package", "private", "protected",
  "public", "arguments", "eval",
]);
// Where the natural translation is a reserved word, a second choice steps in.
const AUSWEICHE = { new: "fresh", class: "cssClass", case: "branchCase", delete: "remove",
                    in: "inside", of: "from", this: "self", default: "fallback" };

function neuerName(name) {
  if (TABU.has(name) || name.startsWith("__")) return null;
  const fuehrend = name.length - name.replace(/^_+/, "").length;
  const kern = name.slice(fuehrend);
  if (!kern) return null;

  if (kern.includes("_") || kern === kern.toLowerCase() || kern === kern.toUpperCase()) {
    const teile = kern.split("_");
    const neu = teile.map((t) => woerterbuch[t.toLowerCase()] ?? t);
    if (teile.every((t, i) => t.toLowerCase() === neu[i].toLowerCase())) return null;
    let gebaut = neu.join("_");
    if (kern === kern.toUpperCase()) gebaut = gebaut.toUpperCase();
    if (RESERVIERT.has(gebaut)) gebaut = AUSWEICHE[gebaut] ?? null;
    if (!gebaut) return null;
    return "_".repeat(fuehrend) + gebaut;
  }
  const stuecke = kern.match(/[A-Z][a-z0-9]*|[a-z0-9]+/g) || [];
  const neu = stuecke.map((s) => woerterbuch[s.toLowerCase()] ?? s);
  if (stuecke.every((s, i) => s.toLowerCase() === neu[i].toLowerCase())) return null;
  const gross = kern[0] === kern[0].toUpperCase();
  let gebaut = neu.map((w, i) => (i === 0 && !gross ? w : w[0].toUpperCase() + w.slice(1)))
                  .join("");
  if (RESERVIERT.has(gebaut)) gebaut = AUSWEICHE[gebaut] ?? null;
  if (!gebaut) return null;
  return "_".repeat(fuehrend) + gebaut;
}

function bearbeite(datei, schreiben) {
  const quelle = fs.readFileSync(datei, "utf8");
  const sf = ts.createSourceFile(datei, quelle, ts.ScriptTarget.Latest, true,
    datei.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

  const stellen = [];
  const karte = new Map();
  const besuche = (n) => {
    // Only real identifiers. Property names in object literals stay: `{ pfad: … }` is a
    // key others read, not a local name.
    if (ts.isIdentifier(n)) {
      const eltern = n.parent;
      const neu = neuerName(n.text);

      // A shorthand carries two roles in one word: `{ feld }` binds a local name AND names
      // the key. Renaming the word alone would rename the key with it — and that key is
      // read by someone else. So it is written out: `{ feld: field }`.
      if (neu && ts.isShorthandPropertyAssignment(eltern) && eltern.name === n) {
        stellen.push([n.getStart(sf), n.getEnd(), `${n.text}: ${neu}`]);
        karte.set(n.text, neu);
      } else if (neu && ts.isBindingElement(eltern) && eltern.name === n && !eltern.propertyName
                 && ts.isObjectBindingPattern(eltern.parent)) {
        stellen.push([n.getStart(sf), n.getEnd(), `${n.text}: ${neu}`]);
        karte.set(n.text, neu);
      } else {
        const istSchluessel =
          (ts.isPropertyAssignment(eltern) && eltern.name === n) ||
          (ts.isPropertySignature(eltern) && eltern.name === n) ||
          (ts.isPropertyAccessExpression(eltern) && eltern.name === n) ||
          (ts.isBindingElement(eltern) && eltern.propertyName === n) ||
          (ts.isJsxAttribute(eltern) && eltern.name === n) ||
          (ts.isEnumMember(eltern) && eltern.name === n) ||
          (ts.isMethodSignature(eltern) && eltern.name === n);
        if (!istSchluessel && neu) {
          stellen.push([n.getStart(sf), n.getEnd(), neu]); karte.set(n.text, neu);
        }
      }
    }
    ts.forEachChild(n, besuche);
  };
  besuche(sf);

  if (!stellen.length) return [0, karte];
  if (!schreiben) return [stellen.length, karte];

  let aus = quelle;
  for (const [s, e, neu] of stellen.sort((a, b) => b[0] - a[0])) {
    aus = aus.slice(0, s) + neu + aus.slice(e);
  }
  fs.writeFileSync(datei, aus);
  return [stellen.length, karte];
}

const args = process.argv.slice(2);
const schreiben = args.includes("--write");
const wurzeln = args.filter((a) => !a.startsWith("--"));
let gesamt = 0;
const alle = new Map();
const gehe = (p) => {
  for (const e of fs.readdirSync(p, { withFileTypes: true })) {
    const voll = path.join(p, e.name);
    if (e.isDirectory()) { if (e.name !== "node_modules") gehe(voll); }
    else if (/\.tsx?$/.test(e.name) && !e.name.endsWith(".d.ts")) {
      const [n, karte] = bearbeite(voll, schreiben);
      if (n) { gesamt += n; for (const [a, b] of karte) alle.set(a, b); console.log(`  ${String(n).padStart(4)} ${voll}`); }
    }
  }
};
for (const w of wurzeln) gehe(w);
console.log(`\n${gesamt} Vorkommen, ${alle.size} verschiedene Namen`);
if (args.includes("--check")) {
  [...alle.entries()].sort().slice(0, 40).forEach(([a, b]) => console.log(`    ${a} -> ${b}`));
}
