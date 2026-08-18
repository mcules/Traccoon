// Checks that every tr("…") key exists in the German catalog, and the other way round.
//
// The German catalog is the truth about which texts exist: the admin area reads it, and what
// is missing there nobody can translate. A forgotten entry hardly shows in the interface (the
// key is displayed, after all), but very much so in daily use.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const wurzel = resolve(process.argv[2] || "frontend/src");
const de = JSON.parse(readFileSync(join(wurzel, "i18n/de.json"), "utf8"));
const en = JSON.parse(readFileSync(join(wurzel, "i18n/en.json"), "utf8"));

const dateien = [];
(function sammle(pfad) {
  for (const eintrag of readdirSync(pfad)) {
    const voll = join(pfad, eintrag);
    if (statSync(voll).isDirectory()) sammle(voll);
    else if (/\.(tsx?|ts)$/.test(eintrag) && !voll.includes("/i18n/")) dateien.push(voll);
  }
})(wurzel);

// Not every key sits inside a tr(): tables hold it as a value
// (`{ label: "inbox.status_new" }`), and some are composed
// (tr(`preferences_panel.flag_${key}`)). So every string that looks like a key is collected,
// plus the prefix of every composed call.
const benutzt = new Map();     // direct: tr("…"), the source for "missing"
const bekannt = new Map();     // also indirect, the source for "orphaned"
const praefixe = [];
for (const datei of dateien) {
  const text = readFileSync(datei, "utf8");
  for (const treffer of text.matchAll(/\btr\(\s*"([^"]+)"/g)) {
    if (!benutzt.has(treffer[1])) benutzt.set(treffer[1], datei);
    bekannt.set(treffer[1], datei);
  }
  // A data path ("data.location.name") looks like a key. So only what is in the catalog
  // counts here: everything else is a string that happens to contain a dot.
  for (const treffer of text.matchAll(/"([a-z][a-z0-9_]*\.[a-z0-9_.]+)"/g)) {
    if (treffer[1] in de) bekannt.set(treffer[1], datei);
  }
  for (const treffer of text.matchAll(/\btr\(\s*`([a-z][a-z0-9_]*\.[a-z0-9_]*)\$\{/g)) {
    praefixe.push(treffer[1]);
  }
}
const zusammengesetzt = (k) => praefixe.some((p) => k.startsWith(p));

const fehlend = [...benutzt.keys()].filter((k) => !(k in de)).sort();
const verwaist = Object.keys(de).filter((k) => !bekannt.has(k) && !zusammengesetzt(k)).sort();
const ohneEnglisch = Object.keys(de).filter((k) => !en[k]).sort();

console.log(`Schlüssel im Code: ${benutzt.size}`);
console.log(`Deutscher Katalog: ${Object.keys(de).length}`);
console.log(`Englisch übersetzt: ${Object.keys(de).length - ohneEnglisch.length}`);
if (fehlend.length) {
  console.log(`\nFEHLT im Katalog (${fehlend.length}):`);
  fehlend.slice(0, 40).forEach((k) => console.log(`  ${k}  (${benutzt.get(k)})`));
}
if (verwaist.length) {
  console.log(`\nverwaist, im Code nicht benutzt (${verwaist.length}):`);
  verwaist.slice(0, 20).forEach((k) => console.log(`  ${k}`));
}
if (ohneEnglisch.length) {
  console.log(`\nohne englische Fassung (${ohneEnglisch.length}):`);
  ohneEnglisch.slice(0, 20).forEach((k) => console.log(`  ${k}`));
}
process.exit(fehlend.length ? 1 : 0);
