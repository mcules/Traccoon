// Checks that every tr("…") key exists in the English catalog, and the other way round.
//
// The English catalog is the source language and the truth about which texts exist: the admin
// area reads it, and what is missing there nobody can translate. A forgotten entry hardly
// shows in the interface (the key is displayed, after all), but very much so in daily use.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(process.argv[2] || "frontend/src");
const de = JSON.parse(readFileSync(join(root, "i18n/de.json"), "utf8"));
const en = JSON.parse(readFileSync(join(root, "i18n/en.json"), "utf8"));

const files = [];
(function collect(path_) {
  for (const entry of readdirSync(path_)) {
    const full = join(path_, entry);
    if (statSync(full).isDirectory()) collect(full);
    else if (/\.(tsx?|ts)$/.test(entry) && !full.includes("/i18n/")) files.push(full);
  }
})(root);

// Not every key sits inside a tr(): tables hold it as a value
// (`{ label: "inbox.status_new" }`), and some are composed
// (tr(`preferences_panel.flag_${key}`)). So every string that looks like a key is collected,
// plus the prefix of every composed call.
const used = new Map();     // direct: tr("…"), the source for "missing"
const known = new Map();     // also indirect, the source for "orphaned"
const prefixes = [];
for (const file of files) {
  const text = readFileSync(file, "utf8");
  for (const hit of text.matchAll(/\btr\(\s*"([^"]+)"/g)) {
    if (!used.has(hit[1])) used.set(hit[1], file);
    known.set(hit[1], file);
  }
  // A data path ("data.location.name") looks like a key. So only what is in the catalog
  // counts here: everything else is a string that happens to contain a dot.
  for (const hit of text.matchAll(/"([a-z][a-z0-9_]*\.[a-z0-9_.]+)"/g)) {
    if (hit[1] in en) known.set(hit[1], file);
  }
  for (const hit of text.matchAll(/\btr\(\s*`([a-z][a-z0-9_]*\.[a-z0-9_]*)\$\{/g)) {
    prefixes.push(hit[1]);
  }
}
const composed = (k) => prefixes.some((p) => k.startsWith(p));

const missing = [...used.keys()].filter((k) => !(k in en)).sort();
const orphaned = Object.keys(en).filter((k) => !known.has(k) && !composed(k)).sort();
const withoutGerman = Object.keys(en).filter((k) => !de[k]).sort();

console.log(`keys in the code: ${used.size}`);
console.log(`English catalog: ${Object.keys(en).length}`);
console.log(`translated into German: ${Object.keys(en).length - withoutGerman.length}`);
if (missing.length) {
  console.log(`\nMISSING from the catalog (${missing.length}):`);
  missing.slice(0, 40).forEach((k) => console.log(`  ${k}  (${used.get(k)})`));
}
if (orphaned.length) {
  console.log(`\norphaned, not used in the code (${orphaned.length}):`);
  orphaned.slice(0, 20).forEach((k) => console.log(`  ${k}`));
}
if (withoutGerman.length) {
  console.log(`\nwithout a German version (${withoutGerman.length}):`);
  withoutGerman.slice(0, 20).forEach((k) => console.log(`  ${k}`));
}
process.exit(missing.length ? 1 : 0);
