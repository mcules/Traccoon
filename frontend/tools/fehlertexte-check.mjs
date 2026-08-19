// Do the error texts of the API exist in both catalogs?
//
// The server sends the key of its error text along, the browser looks it up. A key without a
// catalog entry is not an outage (the English sentence from the server is still shown), but
// it is a German screen with one English line in it, and nobody notices that in review.
//
// Run: node --experimental-strip-types tools/fehlertexte-check.mjs
// The backend has to be reachable at ../backend, in the container as /backend.
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const BACKEND = existsSync("/backend") ? "/backend" : "../backend";
const API = join(BACKEND, "app/api");

const schluessel = new Set();
for (const datei of readdirSync(API).filter((d) => d.endsWith(".py"))) {
  const quelle = readFileSync(join(API, datei), "utf8");
  for (const treffer of quelle.matchAll(/\bFehler\(\s*[^,]+,\s*"(err\.[a-z0-9_]+)"/g)) {
    schluessel.add(treffer[1]);
  }
}

const de = JSON.parse(readFileSync("src/i18n/de.json", "utf8"));
const en = JSON.parse(readFileSync("src/i18n/en.json", "utf8"));

const fehlt = (katalog) => [...schluessel].filter((k) => !(k in katalog)).sort();
const ueberzaehlig = Object.keys(de)
  .filter((k) => k.startsWith("err.") && !schluessel.has(k)).sort();

const platzhalter = (text) => new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
const abweichend = [...schluessel].filter((k) => {
  if (!(k in de) || !(k in en)) return false;
  const a = platzhalter(de[k]);
  const b = platzhalter(en[k]);
  return a.size !== b.size || [...a].some((x) => !b.has(x));
}).sort();

let schlecht = 0;
const melde = (was, liste) => {
  if (!liste.length) return console.log(`OK   ${was}`);
  schlecht += liste.length;
  console.log(`FEHL ${was}: ${liste.join(", ")}`);
};

console.log(`${schluessel.size} Fehlertexte in der API`);
melde("jeder Schlüssel steht in de.json", fehlt(de));
melde("jeder Schlüssel steht in en.json", fehlt(en));
melde("keine verwaisten err.*-Einträge", ueberzaehlig);
melde("Platzhalter stimmen zwischen de und en überein", abweichend);
process.exit(schlecht ? 1 : 0);
