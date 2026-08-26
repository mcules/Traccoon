import { useCallback, useRef, useState } from "react";
import { Issue } from "../../api";

/**
 * Which tickets are ticked, across as many tables as a page happens to draw.
 *
 * The backlog is one table per sprint, so the selection cannot live in a table: ticking two
 * rows in two sprints has to end up in ONE bulk action. What a range needs (the rows, the
 * anchor) travels through a box that keeps its identity, not through the closure, otherwise
 * the handle would carry the list of the render its row was drawn in.
 */
export function useSelection() {
  const [chosen, setChosen] = useState<string[]>([]);
  const [anchor, setAnchor] = useState<string | null>(null);
  const now = useRef({ chosen, anchor });
  now.current = { chosen, anchor };

  const tick = useCallback((rows: Issue[], key: string, index: number, shift: boolean) => {
    const { chosen: had, anchor: from_key } = now.current;
    const set = new Set(had);
    if (shift && from_key !== null) {
      const from = rows.findIndex((i) => i.key === from_key);
      if (from >= 0) {
        const [a, b] = from < index ? [from, index] : [index, from];
        // The range follows what the anchor did: ticking ticks, unticking unticks.
        const add = !set.has(key);
        rows.slice(a, b + 1).forEach((i) => (add ? set.add(i.key) : set.delete(i.key)));
        setChosen([...set]);
        return;
      }
    }
    set.has(key) ? set.delete(key) : set.add(key);
    setAnchor(key);
    setChosen([...set]);
  }, []);

  const setMany = useCallback((keys: string[], on: boolean) => {
    const set = new Set(now.current.chosen);
    keys.forEach((k) => (on ? set.add(k) : set.delete(k)));
    setChosen([...set]);
    setAnchor(on && keys.length ? keys[0] : null);
  }, []);

  const clear = useCallback(() => { setChosen([]); setAnchor(null); }, []);

  return { chosen, ticked: new Set(chosen), tick, setMany, clear };
}
