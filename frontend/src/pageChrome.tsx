import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type ChromeTab = { key: string; label: string; to: string; icon?: string };
/** `active` is the key of the active tab. The page knows it exactly; from the address it
 *  could only be guessed (`/settings` shows the same content as `/settings/secrets`, and
 *  then no tab looked active). */
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

// For the layout: reads and sets the current chrome state.
export function useChrome(): ChromeCtx {
  return useContext(Ctx);
}

// For pages: sets the title and the tabs of the current page.
// Assumption: tabs are taken into the effect deps over JSON.stringify so that an array
// reference created anew on every render does not trigger an endless loop.
export function usePageChrome(title: string, tabs: ChromeTab[], active?: string): void {
  const { setChrome } = useChrome();
  const tabsKey = JSON.stringify(tabs);
  useEffect(() => {
    setChrome({ title, tabs, active });
    // Reset on leaving. Formerly the assumption stood here that the next page overwrites the
    // state anyway, but that only holds for pages that use the hook. On the start page, in
    // the inbox and in the editor the sub-menu of the last visited page therefore stayed.
    // React cleans up before the effect of the new page, so a switch between two pages with
    // a sub-menu does not flicker.
    return () => setChrome({ title: "", tabs: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, tabsKey, active]);
}
