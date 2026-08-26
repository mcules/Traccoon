import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type ChromeTab = { key: string; label: string; to: string; icon?: string };
/** How the sub-menu is drawn. "side" = a narrow list beside the content — the normal case,
 *  and what every page with tabs uses: one form for one movement. "oben" = the wrapping bar
 *  over the content, kept for a page that really needs its full width. Both fall back to one
 *  bar on a narrow screen. */
export type ChromeLayout = "top" | "side";
/** `active` is the key of the active tab. The page knows it exactly; from the address it
 *  could only be guessed (`/settings` shows the same content as `/settings/secrets`, and
 *  then no tab looked active). */
/**
 * Two decisions, and they are not the same one.
 *
 * `wide` gives the page the whole window instead of the 1400px reading column. `frame` makes
 * it a box of a fixed height that ends at the lower edge and holds the scrolling itself, in
 * the columns it is made of.
 *
 * The mailbox wants both: three columns beside each other, and none of them should drag the
 * other two along. A board wants only the first: it is wide, and it grows downwards like
 * every list one reads through. They used to be one flag, and a page that wanted the width
 * got a frame with it, which cuts off everything that does not scroll inside on its own.
 */
export type ChromeShape = { wide?: boolean; frame?: boolean };
type Chrome = { title: string; tabs: ChromeTab[]; active?: string; layout?: ChromeLayout }
              & ChromeShape;

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
export function usePageChrome(title: string, tabs: ChromeTab[], active?: string,
                              layout: ChromeLayout = "top", shape: ChromeShape = {}): void {
  const { setChrome } = useChrome();
  const tabsKey = JSON.stringify(tabs);
  const { wide = false, frame = false } = shape;
  useEffect(() => {
    setChrome({ title, tabs, active, layout, wide, frame });
    // Reset on leaving. Formerly the assumption stood here that the next page overwrites the
    // state anyway, but that only holds for pages that use the hook. On the start page, in
    // the inbox and in the editor the sub-menu of the last visited page therefore stayed.
    // React cleans up before the effect of the new page, so a switch between two pages with
    // a sub-menu does not flicker.
    return () => setChrome({ title: "", tabs: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, tabsKey, active, layout, wide, frame]);
}
