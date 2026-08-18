import { useEffect, useState } from "react";
import { beiSprachwechsel, setzeSprache, sprache } from ".";

/**
 * Hält eine Komponente an der aktuellen Sprache.
 *
 * Der Zähler ist Absicht: `t()` liest aus einem Modul-Zustand, und React erfährt von einem
 * Sprachwechsel sonst nichts. Ein Zustand, der sich ändert, ist das kleinste Mittel, alle
 * Ansichten neu zeichnen zu lassen.
 */
export function useSprache(): string {
  const [, tick] = useState(0);
  useEffect(() => beiSprachwechsel(() => tick((n) => n + 1)), []);
  return sprache();
}

/** Sprache des angemeldeten Menschen übernehmen (Profil, sonst Browser, sonst Deutsch). */
export function useSpracheVonNutzer(locale: string | undefined): void {
  useEffect(() => {
    const gewuenscht = locale || navigator.language?.slice(0, 2) || "de";
    if (gewuenscht !== sprache()) void setzeSprache(gewuenscht);
  }, [locale]);
}

export { setzeSprache };
