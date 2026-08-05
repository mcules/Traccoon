// Schicht 2 — Tag- oder Abendbüro.
//
// `data-theme="dark"` → `"night"` (Lampen an, Monitore als Hauptlichtquelle),
// alles andere → `"day"`. **Nicht** die echte Uhrzeit: die bräche den Determinismus, weil
// dasselbe Log um 23 Uhr ein anderes Bild ergäbe als um 9 Uhr (PIXEL-CONTRACT.md Regel 3.1).
// Der Grad ist ein Palettentausch, kein Rechenweg — Schicht 1 sieht ihn nur als `Grade`.
//
// **Es gibt in Traccoon keinen Theme-Context.** Das Attribut wird imperativ auf
// `document.documentElement` gesetzt: `auth.tsx:30` beim Laden von `/me`, `Profile.tsx:171`
// beim Umschalten. Also wird genau dort gelauscht — ein `MutationObserver` auf dieses eine
// Attribut. Kein `localStorage`-Polling (das Attribut ist die Wahrheit, der Speicher nur die
// Herkunft) und kein Intervall (ein Themenwechsel ist ein Ereignis, kein Zustand, den man
// abfragen müsste).

import { useEffect, useState } from "react";
import type { Grade } from "./types.ts";

const ATTR = "data-theme";

/** Liest den Grad aus dem DOM. Einzige Wahrheitsquelle; alles andere wäre eine Kopie,
 *  die driften kann. */
function readGrade(): Grade {
  return document.documentElement.getAttribute(ATTR) === "dark" ? "night" : "day";
}

export function useTheme(): Grade {
  const [grade, setGrade] = useState<Grade>(readGrade);

  useEffect(() => {
    const root = document.documentElement;
    // Zwischen dem ersten Render und diesem Effekt kann `auth.tsx` das Attribut gesetzt
    // haben (es kommt erst mit der Antwort von `/me`). Einmal nachlesen kostet nichts und
    // spart ein falsch beleuchtetes erstes Bild.
    setGrade(readGrade());
    const obs = new MutationObserver(() => {
      // `setGrade` mit gleichem Wert ist ein No-op in React — ein Attributwechsel auf
      // denselben Grad löst also kein Neuzeichnen aus.
      setGrade(readGrade());
    });
    obs.observe(root, { attributes: true, attributeFilter: [ATTR] });
    return () => obs.disconnect();
  }, []);

  return grade;
}
