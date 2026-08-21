import { api } from "../api";
import { setDateLocale } from "../lib/formatTime";
import de from "./de.json";
import en from "./en.json";

/**
 * Translations of the interface.
 *
 * Three sources, in this order: what an admin changed in the browser, the shipped catalog of
 * that language, and finally the English catalog — English is the source language of this
 * house, every other one is a translation of it. Only when all three stay silent does the key
 * itself appear, visible enough to be noticed but never an empty area.
 *
 * The shipped catalogs live in the repository, because a text belongs to the source: it
 * changes with the code it describes and belongs in the same review. What a person changes at
 * runtime lives in the database, because nobody should need a deployment for a typo in a
 * label.
 */
type Catalog = Record<string, string>;

const SHIPPED: Record<string, Catalog> = { de: de as Catalog, en: en as Catalog };
export const SOURCELANGUAGE = "en";

let current = SOURCELANGUAGE;
let overrides: Catalog = {};
const listener = new Set<() => void>();

/** Whoever shows a text has to redraw when the language changes. */
export function atLanguageswitch(fn: () => void): () => void {
  listener.add(fn);
  return () => listener.delete(fn);
}

export function language(): string {
  return current;
}

/**
 * One text for its key. `vars` fills placeholders of the form `{name}`.
 *
 * When a translation is missing the English text appears, which keeps a half translated
 * interface usable instead of ending in raw keys.
 */
export function tr(key: string, vars?: Record<string, string | number>): string {
  const text = overrides[key]
    ?? SHIPPED[current]?.[key]
    ?? SHIPPED[SOURCELANGUAGE]?.[key]
    ?? key;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    vars[name] !== undefined ? String(vars[name]) : whole);
}

/**
 * One text, but only when this language really knows the key.
 *
 * The fallback chain of `tr` ends in German, which is right for the interface: a half
 * translated screen stays usable. For a server error it would be wrong. The server already
 * sends the sentence in English, and an English sentence beats a German one for somebody
 * who reads neither the catalog nor German. So: the override, the catalog of the chosen
 * language, otherwise nothing, and the caller keeps what the server wrote.
 */
export function trKnown(key: string, vars?: Record<string, string | number>): string | null {
  const text = overrides[key] ?? SHIPPED[current]?.[key];
  if (text === undefined) return null;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    vars[name] !== undefined ? String(vars[name]) : whole);
}

/** Set the language and pull in what the admin changed. */
export async function setLanguage(locale: string): Promise<void> {
  current = locale && locale in SHIPPED ? locale : (locale || SOURCELANGUAGE);
  // Dates travel with the language: an English interface that writes 21.08.2026 reads like a
  // half-finished translation.
  setDateLocale(current);
  overrides = {};
  try {
    const answer = await api.get<{ locale: string; texts: Catalog }>(
      `/i18n/${encodeURIComponent(current)}`);
    overrides = answer.texts || {};
  } catch {
    // Without a connection the shipped catalog remains, so the interface keeps working.
  }
  listener.forEach((fn) => fn());
}

/** Every key with its English text, the basis of the admin translation view. */
export function allKey(): Catalog {
  return SHIPPED[SOURCELANGUAGE];
}

export function shipped(locale: string): Catalog {
  return SHIPPED[locale] || {};
}

export function builtinLanguages(): string[] {
  return Object.keys(SHIPPED);
}
