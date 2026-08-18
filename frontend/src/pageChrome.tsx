import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type ChromeTab = { key: string; label: string; to: string; icon?: string };
/** `active` ist der Schlüssel des aktiven Reiters. Die Seite weiß ihn genau; aus der Adresse
 *  ließe er sich nur raten (`/settings` zeigt denselben Inhalt wie `/settings/secrets`, und
 *  dann sah kein Reiter aktiv aus). */
type Chrome = { title: string; tabs: ChromeTab[]; active?: string };

interface ChromeCtx {
  chrome: Chrome;
  setChrome: (c: Chrome) => void;
}

const Ctx = createContext<ChromeCtx>(null!);

export function PageChromeProvider({ children }: { children: ReactNode }): JSX.Element {
  const [chrome, setChrome] = useState<Chrome>({ title: "", tabs: [] });
  return <Ctx.Provider value={{ chrome, setChrome }}>{children}</Ctx.Provider>;
}

// Für Layout: liest/setzt den aktuellen Chrome-State.
export function useChrome(): ChromeCtx {
  return useContext(Ctx);
}

// Für Seiten: setzt Titel + Tabs der aktuellen Seite.
// Annahme: tabs werden über JSON.stringify in die Effekt-Deps aufgenommen, damit
// eine bei jedem Render neu erstellte Array-Referenz keine Endlos-Schleife auslöst.
export function usePageChrome(title: string, tabs: ChromeTab[], active?: string): void {
  const { setChrome } = useChrome();
  const tabsKey = JSON.stringify(tabs);
  useEffect(() => {
    setChrome({ title, tabs, active });
    // Beim Verlassen zurücksetzen. Früher stand hier die Annahme, die nächste Seite
    // überschreibe den Zustand ohnehin — das gilt aber nur für Seiten, die den Hook
    // benutzen. Auf Startseite, Inbox und im Editor blieb dadurch das Untermenü der
    // zuletzt besuchten Seite stehen. React räumt vor dem Effekt der neuen Seite auf,
    // ein Wechsel zwischen zwei Seiten mit Untermenü flackert also nicht.
    return () => setChrome({ title: "", tabs: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, tabsKey, active]);
}
