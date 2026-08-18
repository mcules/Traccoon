// Zieht deutsche Oberflächentexte aus einer Datei und ersetzt sie durch tr("schlüssel").
//
// Bewusst kein Alleskönner: erfasst werden JSX-Textknoten und die Attribute, die ein Mensch
// liest (placeholder, title, label, aria-label, alt). Alles andere — Klassennamen, Pfade,
// Schlüssel von Objekten — bleibt unangetastet, weil dort ein falscher Treffer nicht
// auffällt, sondern etwas kaputt macht.
//
// Der Schlüssel entsteht aus Dateiname und Text: `processes.eigene_prozesse`. Wo das schief
// aussieht, wird er hinterher von Hand geradegezogen — die Ersetzung ist der langweilige
// Teil, die Benennung nicht.
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";

const datei = process.argv[2];
const katalogPfad = process.argv[3];
const trockenlauf = process.argv.includes("--trocken");

const bereich = basename(datei).replace(/\.(tsx|ts)$/, "")
  .replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();

const quelle = readFileSync(datei, "utf8");
const katalog = JSON.parse(readFileSync(katalogPfad, "utf8"));

const DEUTSCH = /[äöüßÄÖÜ]|(^|\s)(der|die|das|den|dem|des|ein|eine|einen|und|oder|nicht|kein|keine|mit|ohne|für|von|zu|zum|zur|auf|aus|bei|ist|sind|wird|werden|kann|soll|muss|nur|noch|schon|hier|dort|wie|was|wer|wenn|dann|als|auch|sich|nach|über|unter|vor|beim|im|am)(\s|$)/i;

function istText(s) {
  const t = s.trim();
  if (t.length < 2 || t.length > 300) return false;
  if (/^[\d\s.,:%/-]+$/.test(t)) return false;         // reine Zahlen/Zeichen
  if (/^[a-z0-9_.-]+$/.test(t)) return false;          // Schlüssel, Pfade, Klassen
  if (/^(https?:|\/|#|\.|@)/.test(t)) return false;
  if (/[{}<>]/.test(t)) return false;
  if (!/[A-Za-zÄÖÜäöüß]/.test(t)) return false;
  // Ein Wort, das mit Großbuchstabe beginnt, ist in der Oberfläche fast immer eine
  // Beschriftung („Profil", „Abmelden"). Reine Großschreibung (TRA-31, JSON) nicht.
  if (/^[A-ZÄÖÜ][a-zäöüß]/.test(t)) return true;
  return DEUTSCH.test(t);
}

function schluessel(text) {
  const kern = text.trim().toLowerCase()
    .replace(/[äöüß]/g, (c) => ({ "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss" }[c]))
    .replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40);
  let k = `${bereich}.${kern || "text"}`;
  let n = 2;
  while (katalog[k] !== undefined && katalog[k] !== text.trim()) k = `${bereich}.${kern}_${n++}`;
  return k;
}

let neu = quelle;
const gefunden = [];

// 1) Attribute: placeholder="…", title="…", label="…"
neu = neu.replace(/\b(placeholder|title|label|aria-label|alt)="([^"{}]+)"/g, (ganz, attr, text) => {
  if (!istText(text)) return ganz;
  const k = schluessel(text);
  katalog[k] = text.trim();
  gefunden.push([k, text.trim()]);
  return `${attr}={tr("${k}")}`;
});

// 2) JSX-Textknoten, aber NUR wenn der Text das ganze Element füllt (`>Text</`). Ein Satz,
// der um ein <b> oder <code> herumläuft, zerfiele sonst in Bruchstücke — und Bruchstücke
// lassen sich nicht übersetzen, weil in anderen Sprachen die Satzstellung anders ist.
neu = neu.replace(/>([^<>{}\n][^<>{}]*)<\//g, (ganz, text) => {
  if (!istText(text)) return ganz;
  if (/^[).,;:!?—–-]/.test(text.trim())) return ganz;   // Bruchstück eines Satzes
  if (/^[a-zäöü]/.test(text.trim())) return ganz;       // beginnt klein: mitten im Satz
  const roh = text.trim();
  const k = schluessel(roh);
  katalog[k] = roh;
  gefunden.push([k, roh]);
  const vorn = text.match(/^\s*/)[0];
  const hinten = text.match(/\s*$/)[0];
  return `>${vorn}{tr("${k}")}${hinten}</`;
});

if (gefunden.length && !/from "[./]*i18n"/.test(neu)) {
  // Die Tiefe kommt aus dem Pfad, nicht aus einer Annahme: unter components/workflow/config
  // liegen drei Ebenen zwischen Datei und src/i18n.
  const ebenen = datei.replace(/^.*?frontend\/src\//, "").split("/").length - 1;
  const tiefe = ebenen ? "../".repeat(ebenen) + "i18n" : "./i18n";
  neu = neu.replace(/^(import[^\n]*\n)/, `$1import { tr } from "${tiefe}";\n`);
}

if (!trockenlauf && gefunden.length) {
  writeFileSync(datei, neu);
  writeFileSync(katalogPfad, JSON.stringify(Object.fromEntries(
    Object.entries(katalog).sort(([a], [b]) => a.localeCompare(b))), null, 2) + "\n");
}
console.log(`${datei}: ${gefunden.length} Texte`);
gefunden.slice(0, 8).forEach(([k, v]) => console.log(`  ${k} = ${v}`));
