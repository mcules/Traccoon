import { useEffect, useState } from "react";

/**
 * Is the screen narrow (a phone)?
 *
 * Some things cannot be solved with CSS: on a phone the flow editor shows only one of its
 * three columns, and which one is decided by state in React, not by a class. The breakpoint
 * is the same as Tailwind's `md`, so behaviour and appearance switch at the same place.
 */
export function useNarrow(limit = 768): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.innerWidth < limit);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${limit - 1}px)`);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [limit]);
  return narrow;
}
