// Do the error texts of the API exist in both catalogs?
//
// The server sends the key of its error text along, the browser looks it up. A key without a
// catalog entry is not an outage (the English sentence from the server is still shown), but
// it is a German screen with one English line in it, and nobody notices that in review.
//
// Run: node --experimental-strip-types tools/error-texts-check.mjs
// The backend has to be reachable at ../backend, in the container as /backend.
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const BACKEND = existsSync("/backend") ? "/backend" : "../backend";
// Not only `app/api`: a rule that several endpoints share lives in `app/services`, and its
// refusal carries a key just the same. Reading the door alone declared exactly those keys
// orphaned the moment they moved one floor down.
const ROOTS = ["app/api", "app/services", "app/worker", "app/core"];

const keys = new Set();
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) { walk(path); continue; }
    if (!entry.name.endsWith(".py")) continue;
    const source = readFileSync(path, "utf8");
    for (const hit of source.matchAll(/\bError\(\s*[^,]+,\s*"(err\.[a-z0-9_]+)"/g)) {
      keys.add(hit[1]);
    }
  }
};
for (const root of ROOTS) walk(join(BACKEND, root));
// `core/error.py` shows the shape of a call in its own docstring. That example is not a key.
keys.delete("err.x");

const de = JSON.parse(readFileSync("src/i18n/de.json", "utf8"));
const en = JSON.parse(readFileSync("src/i18n/en.json", "utf8"));

const missing = (catalog) => [...keys].filter((k) => !(k in catalog)).sort();
const orphaned = Object.keys(de)
  .filter((k) => k.startsWith("err.") && !keys.has(k)).sort();

const placeholders = (text) => new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
const differing = [...keys].filter((k) => {
  if (!(k in de) || !(k in en)) return false;
  const a = placeholders(de[k]);
  const b = placeholders(en[k]);
  return a.size !== b.size || [...a].some((x) => !b.has(x));
}).sort();

let bad = 0;
const report = (what, list) => {
  if (!list.length) return console.log(`OK   ${what}`);
  bad += list.length;
  console.log(`FAIL ${what}: ${list.join(", ")}`);
};

console.log(`${keys.size} error texts in the API`);
report("every key stands in de.json", missing(de));
report("every key stands in en.json", missing(en));
report("no orphaned err.* entries", orphaned);
report("the placeholders match between de and en", differing);
process.exit(bad ? 1 : 0);
