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
const API = join(BACKEND, "app/api");

const keys = new Set();
for (const file of readdirSync(API).filter((d) => d.endsWith(".py"))) {
  const source = readFileSync(join(API, file), "utf8");
  for (const hit of source.matchAll(/\bError\(\s*[^,]+,\s*"(err\.[a-z0-9_]+)"/g)) {
    keys.add(hit[1]);
  }
}

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
