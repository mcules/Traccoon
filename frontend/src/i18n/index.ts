import { api } from "../api";
import de from "./de.json";
import en from "./en.json";

/**
 * Translations of the interface.
 *
 * Three sources, in this order: what an admin changed in the browser, the shipped catalog of
 * that language, and finally the German catalog. Only when all three stay silent does the key
 * itself appear, visible enough to be noticed but never an empty area.
 *
 * The shipped catalogs live in the repository, because a text belongs to the source: it
 * changes with the code it describes and belongs in the same review. What a person changes at
 * runtime lives in the database, because nobody should need a deployment for a typo in a
 * label.
 */
type Katalog = Record<string, string>;

const AUSGELIEFERT: Record<string, Katalog> = { de: de as Katalog, en: en as Katalog };
export const QUELLSPRACHE = "de";

let aktuell = QUELLSPRACHE;
let overrides: Katalog = {};
const horcher = new Set<() => void>();

/** Whoever shows a text has to redraw when the language changes. */
export function beiSprachwechsel(fn: () => void): () => void {
  horcher.add(fn);
  return () => horcher.delete(fn);
}

export function sprache(): string {
  return aktuell;
}

/**
 * One text for its key. `vars` fills placeholders of the form `{name}`.
 *
 * When a translation is missing the German text appears, which keeps a half translated
 * interface usable instead of ending in raw keys.
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

/** Set the language and pull in what the admin changed. */
export async function setzeSprache(locale: string): Promise<void> {
  aktuell = locale && locale in AUSGELIEFERT ? locale : (locale || QUELLSPRACHE);
  overrides = {};
  try {
    const antwort = await api.get<{ locale: string; texte: Katalog }>(
      `/i18n/${encodeURIComponent(aktuell)}`);
    overrides = antwort.texte || {};
  } catch {
    // Without a connection the shipped catalog remains, so the interface keeps working.
  }
  horcher.forEach((fn) => fn());
}

/** Every key with its German text, the basis of the admin translation view. */
export function alleSchluessel(): Katalog {
  return AUSGELIEFERT[QUELLSPRACHE];
}

export function ausgeliefert(locale: string): Katalog {
  return AUSGELIEFERT[locale] || {};
}

export function eingebauteSprachen(): string[] {
  return Object.keys(AUSGELIEFERT);
}
