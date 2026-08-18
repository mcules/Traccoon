import { api } from "../api";
import de from "./de.json";
import en from "./en.json";

/**
 * Übersetzungen der Oberfläche.
 *
 * Drei Quellen, in dieser Reihenfolge: was ein Admin im Browser geändert hat, der
 * ausgelieferte Katalog der Sprache, und zuletzt der deutsche Katalog. Erst wenn alle drei
 * schweigen, steht der Schlüssel selbst da — sichtbar genug, dass es auffällt, aber nie
 * eine leere Fläche.
 *
 * Die ausgelieferten Kataloge liegen im Repository, weil ein Text zum Quelltext gehört: er
 * ändert sich mit dem Code, den er beschreibt, und gehört in dasselbe Review. Was ein
 * Mensch zur Laufzeit ändert, liegt in der Datenbank — für einen Tippfehler in einer
 * Beschriftung soll niemand ein Deployment brauchen.
 */
type Katalog = Record<string, string>;

const AUSGELIEFERT: Record<string, Katalog> = { de: de as Katalog, en: en as Katalog };
export const QUELLSPRACHE = "de";

let aktuell = QUELLSPRACHE;
let overrides: Katalog = {};
const horcher = new Set<() => void>();

/** Wer den Text anzeigt, muss beim Sprachwechsel neu zeichnen. */
export function beiSprachwechsel(fn: () => void): () => void {
  horcher.add(fn);
  return () => horcher.delete(fn);
}

export function sprache(): string {
  return aktuell;
}

/**
 * Ein Text zu seinem Schlüssel. `vars` füllt Platzhalter der Form `{name}`.
 *
 * Fehlt eine Übersetzung, kommt der deutsche Text — eine halb übersetzte Oberfläche bleibt
 * damit benutzbar, statt in rohen Schlüsseln zu enden.
 */
export function tr(key: string, vars?: Record<string, string | number>): string {
  const text = overrides[key]
    ?? AUSGELIEFERT[aktuell]?.[key]
    ?? AUSGELIEFERT[QUELLSPRACHE]?.[key]
    ?? key;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (ganz, name) =>
    vars[name] !== undefined ? String(vars[name]) : ganz);
}

/** Sprache setzen und die Änderungen des Admins dazuholen. */
export async function setzeSprache(locale: string): Promise<void> {
  aktuell = locale && locale in AUSGELIEFERT ? locale : (locale || QUELLSPRACHE);
  overrides = {};
  try {
    const antwort = await api.get<{ locale: string; texte: Katalog }>(
      `/i18n/${encodeURIComponent(aktuell)}`);
    overrides = antwort.texte || {};
  } catch {
    // Ohne Verbindung bleibt der ausgelieferte Katalog — die Oberfläche funktioniert.
  }
  horcher.forEach((fn) => fn());
}

/** Alle Schlüssel mit ihrem deutschen Text — Grundlage der Verwaltung im Admin. */
export function alleSchluessel(): Katalog {
  return AUSGELIEFERT[QUELLSPRACHE];
}

export function ausgeliefert(locale: string): Katalog {
  return AUSGELIEFERT[locale] || {};
}

export function eingebauteSprachen(): string[] {
  return Object.keys(AUSGELIEFERT);
}
