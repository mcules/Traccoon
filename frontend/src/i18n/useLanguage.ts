import { useEffect, useState } from "react";
import { atLanguageswitch, setLanguage, language } from ".";

/**
 * Keeps a component tied to the current language.
 *
 * The counter is deliberate: `tr()` reads from module state, and React would otherwise never
 * hear about a language change. A piece of state that changes is the smallest way to make
 * every view redraw.
 */
export function useLanguage(): string {
  const [, tick] = useState(0);
  useEffect(() => atLanguageswitch(() => tick((n) => n + 1)), []);
  return language();
}

/** Take over the language of the signed in person (profile, else browser, else English). */
export function useLanguageFromUser(locale: string | undefined): void {
  useEffect(() => {
    const wanted = locale || navigator.language?.slice(0, 2) || "en";
    if (wanted !== language()) void setLanguage(wanted);
  }, [locale]);
}

export { setLanguage };
