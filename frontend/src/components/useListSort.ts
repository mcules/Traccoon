import { useCallback, useMemo } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

export type Dir = "asc" | "desc";
/** What is compared for one field. `null` counts as "has none" and goes to the end. */
export type Value = string | number | null | undefined;

/**
 * Sort order of a list — held in the profile of the person, not in the page.
 *
 * Whoever sorts a list once means it: the order is a way of working, not a mood of this one
 * visit. It therefore lives beside the other view settings of the person (`users.list_sort`)
 * and applies on the next device as well.
 *
 * `values` says how a row answers for each field. What is not in it cannot be sorted by, and
 * the backend keeps its own list of the same fields — a value out of the browser lands in the
 * profile, where a typo would stay for good.
 *
 * Saving happens quietly: a failed request costs the memory, not the sorting. The list stands
 * sorted either way, because the state comes from the freshly read profile.
 */
export function useListSort<T>(
  list: string,
  fallback: { by: string; dir: Dir },
  values: Record<string, (row: T) => Value>,
) {
  const { user, refresh } = useAuth();
  const stored = user?.list_sort?.[list];
  const by = stored?.by && stored.by in values ? stored.by : fallback.by;
  const dir: Dir = stored?.dir === "desc" ? "desc" : stored?.dir === "asc" ? "asc" : fallback.dir;

  const toggle = useCallback((key: string) => {
    const next: Dir = key === by && dir === "asc" ? "desc" : "asc";
    api.put("/me/list-sort", { list, by: key, dir: next })
      .then(() => refresh())
      .catch(() => { /* quietly: the sorting is worth no error message */ });
  }, [by, dir, list, refresh]);

  /** Sorted copy. Stable, so rows that compare equal keep the order the server sent. */
  const sorted = useCallback((rows: readonly T[] | undefined): T[] => {
    const read = values[by];
    if (!rows || !read) return [...(rows || [])];
    const sign = dir === "desc" ? -1 : 1;
    return rows.map((row, i) => ({ row, i })).sort((a, b) => {
      const x = read(a.row), y = read(b.row);
      // Empty goes to the end, in BOTH directions: a run without an end is not the oldest
      // one, it simply has none — and it must not push itself to the top by turning around.
      const nx = x === null || x === undefined || x === "";
      const ny = y === null || y === undefined || y === "";
      if (nx || ny) return nx && ny ? a.i - b.i : nx ? 1 : -1;
      const d = typeof x === "number" && typeof y === "number"
        ? x - y
        : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
      return d !== 0 ? d * sign : a.i - b.i;
    }).map((e) => e.row);
  }, [by, dir, values]);

  return useMemo(() => ({ by, dir, toggle, sorted }), [by, dir, toggle, sorted]);
}
