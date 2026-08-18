import { useEffect, useState } from "react";
import { beiSprachwechsel, setzeSprache, sprache } from ".";

/**
 * Keeps a component tied to the current language.
 *
 * The counter is deliberate: `tr()` reads from module state, and React would otherwise never
 * hear about a language change. A piece of state that changes is the smallest way to make
 * every view redraw.
 */
export function useSprache(): string {
  const [, tick] = useState(0);
  useEffect(() => beiSprachwechsel(() => tick((n) => n + 1)), []);
  return sprache();
}

/** Take over the language of the signed in person (profile, else browser, else German). */
export function useSpracheVonNutzer(locale: string | undefined): void {
  useEffect(() => {
    const gewuenscht = locale || navigator.language?.slice(0, 2) || "de";
    if (gewuenscht !== sprache()) void setzeSprache(gewuenscht);
  }, [locale]);
}

export { setzeSprache };
