import { useEffect, useState } from "react";

/**
 * Ist der Bildschirm schmal (Handy)?
 *
 * Manches lässt sich nicht mit CSS lösen: der Ablauf-Editor zeigt am Handy immer nur eine
 * seiner drei Spalten, und welche das ist, entscheidet ein Zustand in React — nicht eine
 * Klasse. Die Grenze ist dieselbe wie Tailwinds `md`, damit Verhalten und Aussehen an
 * derselben Stelle umschalten.
 */
export function useSchmal(grenze = 768): boolean {
  const [schmal, setSchmal] = useState(
    () => typeof window !== "undefined" && window.innerWidth < grenze);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${grenze - 1}px)`);
    const auf = () => setSchmal(mq.matches);
    auf();
    mq.addEventListener("change", auf);
    return () => mq.removeEventListener("change", auf);
  }, [grenze]);
  return schmal;
}
