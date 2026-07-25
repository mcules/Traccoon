import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type ChromeTab = { key: string; label: string; to: string; icon?: string };
type Chrome = { title: string; tabs: ChromeTab[] };

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
export function usePageChrome(title: string, tabs: ChromeTab[]): void {
  const { setChrome } = useChrome();
  const tabsKey = JSON.stringify(tabs);
  useEffect(() => {
    setChrome({ title, tabs });
    // Kein Reset beim Unmount: die nächste Seite überschreibt den Chrome-State ohnehin.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, tabsKey]);
}
