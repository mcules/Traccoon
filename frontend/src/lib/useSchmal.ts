import { useEffect, useState } from "react";

/**
 * Is the screen narrow (a phone)?
 *
 * Some things cannot be solved with CSS: on a phone the flow editor shows only one of its
 * three columns, and which one is decided by state in React, not by a class. The breakpoint
 * is the same as Tailwind's `md`, so behaviour and appearance switch at the same place.
 */
export function useSchmal(limit = 768): boolean {
  const [schmal, setSchmal] = useState(
    () => typeof window !== "undefined" && window.innerWidth < limit);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${limit - 1}px)`);
    const auf = () => setSchmal(mq.matches);
    auf();
    mq.addEventListener("change", auf);
    return () => mq.removeEventListener("change", auf);
  }, [limit]);
  return schmal;
}
