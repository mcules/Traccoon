// Layer 2: day or evening office.
//
// `data-theme="dark"` gives `"night"` (lamps on, monitors as the main light source),
// everything else gives `"day"`. **Not** the real time of day: that would break determinism,
// because the same log would give a different picture at 23:00 than at 9:00
// (PIXEL-CONTRACT.md rule 3.1). The grade is a palette swap, not a computation; layer 1 sees it only as a `Grade`.
//
// **There is no theme context in Traccoon.** The attribute is set imperatively on
// `document.documentElement`: in `auth.tsx:30` while loading `/me`, in `Profile.tsx:171` on
// switching. So exactly there is where we listen, with a `MutationObserver` on this one
// attribute. No `localStorage` polling (the attribute is the truth, the storage only the
// origin) and no interval (a theme change is an event, not a state one has to poll).
// abfragen müsste).

import { useEffect, useState } from "react";
import type { Grade } from "./types.ts";

const ATTR = "data-theme";

/** Reads the grade from the DOM. The only source of truth; everything else would be a copy
 *  that can drift. */
function readGrade(): Grade {
  return document.documentElement.getAttribute(ATTR) === "dark" ? "night" : "day";
}

export function useTheme(): Grade {
  const [grade, setGrade] = useState<Grade>(readGrade);

  useEffect(() => {
    const root = document.documentElement;
    // Between the first render and this effect `auth.tsx` can have set the attribute (it
    // only comes with the answer of `/me`). Reading once costs nothing and saves a wrongly
    // lit first frame.
    setGrade(readGrade());
    const obs = new MutationObserver(() => {
      // `setGrade` with the same value is a no-op in React, so an attribute change to the
      // same grade triggers no redraw.
      setGrade(readGrade());
    });
    obs.observe(root, { attributes: true, attributeFilter: [ATTR] });
    return () => obs.disconnect();
  }, []);

  return grade;
}
