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

// Nicht jeder Schlüssel steht direkt in einem tr(): Tabellen halten ihn als Wert
// (`{ label: "inbox.status_new" }`), und manche werden zusammengesetzt
// (tr(`preferences_panel.flag_${key}`)). Erfasst wird deshalb jede Zeichenkette, die wie ein
// Schlüssel aussieht, plus jedes Präfix eines zusammengesetzten Aufrufs.
const benutzt = new Map();     // direkt: tr("…"), das ist die Quelle für „fehlt"
const bekannt = new Map();     // zusätzlich indirekt, das ist die Quelle für „verwaist"
const praefixe = [];
for (const datei of dateien) {
  const text = readFileSync(datei, "utf8");
  for (const treffer of text.matchAll(/\btr\(\s*"([^"]+)"/g)) {
    if (!benutzt.has(treffer[1])) benutzt.set(treffer[1], datei);
    bekannt.set(treffer[1], datei);
  }
  // Ein Datenpfad („data.location.name") sieht aus wie ein Schlüssel. Deshalb zählt hier nur,
  // was auch im Katalog steht: alles andere ist eine Zeichenkette, die zufällig einen Punkt hat.
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
