// Prüft, ob jeder tr("…")-Schlüssel im deutschen Katalog steht — und umgekehrt.
//
// Der deutsche Katalog ist die Wahrheit darüber, welche Texte es gibt: die Verwaltung im
// Admin liest ihn, und was dort fehlt, kann niemand übersetzen. Ein vergessener Eintrag
// fällt in der Oberfläche kaum auf (es steht ja der Schlüssel da), im Betrieb aber sehr.
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

const benutzt = new Map();
for (const datei of dateien) {
  const text = readFileSync(datei, "utf8");
  for (const treffer of text.matchAll(/\btr\(\s*"([^"]+)"/g)) {
    if (!benutzt.has(treffer[1])) benutzt.set(treffer[1], datei);
  }
}

const fehlend = [...benutzt.keys()].filter((k) => !(k in de)).sort();
const verwaist = Object.keys(de).filter((k) => !benutzt.has(k)).sort();
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
