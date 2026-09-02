/**
 * Line-based diff and three-way merge.
 *
 * The vault has many writers — Syncthing carries edits from desktop and phone,
 * the calendar service and agents write on their own. So an open note must not
 * treat an external change as a question ("keep mine or theirs?"); it merges.
 * Only where both sides touched the same lines is there anything to decide.
 *
 * Deliberately hand-written rather than pulled in: the merge has to end up as
 * CodeMirror changes, not as a new string, or the caret jumps and undo breaks.
 * Producing the edit script here means the same code answers both questions.
 */

export interface Edit {
  /** First line of the replaced range (0-based, inclusive). */
  from: number;
  /** One past the last replaced line. */
  to: number;
  /** Lines that take their place. */
  insert: string[];
}

/** Longest common subsequence of two line arrays, as index pairs. */
function lcsPairs(a: string[], b: string[]): Array<[number, number]> {
  const n = a.length;
  const m = b.length;
  if (n === 0 || m === 0) return [];
  // Row-by-row table of common-prefix lengths. Both inputs are pre-trimmed by
  // their shared head and tail, so the table stays small in practice.
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const out: Array<[number, number]> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push([i, j]);
      i++;
      j++;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) i++;
    else j++;
  }
  return out;
}

/**
 * The changes that turn `a` into `b`, as replaced line ranges. Shared head and
 * tail are stripped first, which is what keeps the table above affordable for
 * real notes: a typed word changes one line out of hundreds.
 */
export function diffLines(a: string[], b: string[]): Edit[] {
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  )
    tail++;
  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);
  if (midA.length === 0 && midB.length === 0) return [];

  const edits: Edit[] = [];
  const pairs = lcsPairs(midA, midB);
  let ai = 0;
  let bi = 0;
  const flush = (untilA: number, untilB: number) => {
    if (untilA > ai || untilB > bi) {
      edits.push({ from: head + ai, to: head + untilA, insert: midB.slice(bi, untilB) });
    }
    ai = untilA;
    bi = untilB;
  };
  for (const [pa, pb] of pairs) {
    flush(pa, pb);
    ai = pa + 1;
    bi = pb + 1;
  }
  flush(midA.length, midB.length);
  return edits;
}

export interface MergeConflict {
  /** Line range in the merged result that holds the disputed text. */
  from: number;
  to: number;
  mine: string[];
  theirs: string[];
}

export interface MergeResult {
  lines: string[];
  conflicts: MergeConflict[];
  /** True when nothing had to be decided — the common, quiet case. */
  clean: boolean;
}

interface Hunk {
  from: number;
  to: number;
  insert: string[];
  side: 'mine' | 'theirs';
}

/**
 * Three-way merge of `mine` and `theirs` over their common `base`.
 *
 * Changes that sit in different places are both kept, silently. Where the two
 * sides changed overlapping lines, the result keeps our text and records the
 * conflict so the caller can show exactly that spot — everything else is already
 * merged by then.
 */
export function merge3(base: string[], mine: string[], theirs: string[]): MergeResult {
  const hunks: Hunk[] = [
    ...diffLines(base, mine).map((e) => ({ ...e, side: 'mine' as const })),
    ...diffLines(base, theirs).map((e) => ({ ...e, side: 'theirs' as const })),
  ].sort((x, y) => x.from - y.from || x.to - y.to);

  const out: string[] = [];
  const conflicts: MergeConflict[] = [];
  let pos = 0; // how far through `base` we have copied

  for (let i = 0; i < hunks.length; i++) {
    const h = hunks[i];
    // Gather everything that overlaps this hunk — a conflict is not necessarily
    // a pair, three hunks can pile onto the same lines.
    let to = h.to;
    let j = i + 1;
    const group: Hunk[] = [h];
    while (j < hunks.length && hunks[j].from < to) {
      to = Math.max(to, hunks[j].to);
      group.push(hunks[j]);
      j++;
    }
    const sides = new Set(group.map((g) => g.side));

    if (h.from > pos) out.push(...base.slice(pos, h.from));

    if (sides.size === 1) {
      // Only one side touched these lines: take it, no question asked. The hunk
      // range may have grown past h.to when several hunks of the same side sit
      // adjacent, so replay them in order over the base slice.
      const from = Math.min(...group.map((g) => g.from));
      let cursor = from;
      for (const g of group) {
        if (g.from > cursor) out.push(...base.slice(cursor, g.from));
        out.push(...g.insert);
        cursor = Math.max(cursor, g.to);
      }
      if (to > cursor) out.push(...base.slice(cursor, to));
    } else {
      const region = (side: 'mine' | 'theirs') => {
        const src = side === 'mine' ? mine : theirs;
        const own = group.filter((g) => g.side === side);
        if (!own.length) return base.slice(Math.min(...group.map((g) => g.from)), to);
        // Rebuild what this side made of the disputed base lines.
        const start = Math.min(...own.map((g) => g.from));
        const res: string[] = [];
        let cursor = start;
        for (const g of own) {
          if (g.from > cursor) res.push(...base.slice(cursor, g.from));
          res.push(...g.insert);
          cursor = Math.max(cursor, g.to);
        }
        if (to > cursor) res.push(...base.slice(cursor, to));
        void src;
        return res;
      };
      const ours = region('mine');
      const others = region('theirs');
      if (ours.join('\n') === others.join('\n')) {
        // Both arrived at the same text — agreement, not conflict.
        out.push(...ours);
      } else {
        conflicts.push({ from: out.length, to: out.length + ours.length, mine: ours, theirs: others });
        out.push(...ours);
      }
    }
    pos = Math.max(pos, to);
    i = j - 1;
  }
  if (pos < base.length) out.push(...base.slice(pos));
  return { lines: out, conflicts, clean: conflicts.length === 0 };
}

/** Split keeping the exact line count, including a trailing empty line. */
export function toLines(text: string): string[] {
  return text.split('\n');
}

export function fromLines(lines: string[]): string {
  return lines.join('\n');
}

export interface TextChange {
  from: number;
  to: number;
  insert: string;
}

/**
 * Turn line edits into character ranges of `current`.
 *
 * Replacing the whole document would be simpler, but it drops the caret to the
 * top and fills the undo history with one giant step. Editing only the lines
 * that actually differ leaves the caret where the writer left it — which is the
 * whole point of merging instead of reloading.
 */
export function lineEditsToChanges(current: string, edits: Edit[]): TextChange[] {
  if (!edits.length) return [];
  const lines = toLines(current);
  const starts: number[] = [];
  let off = 0;
  for (const l of lines) {
    starts.push(off);
    off += l.length + 1; // + the newline that separates it from the next
  }
  const end = current.length;
  const changes: TextChange[] = [];
  for (const e of edits) {
    const from = e.from < lines.length ? starts[e.from] : end;
    const to = e.to < lines.length ? starts[e.to] : end;
    let insert = e.insert.join('\n');
    if (e.insert.length) {
      // A run that stops before the end keeps the separator it consumed; one
      // that reaches the end has none, so a pure append has to bring its own.
      if (e.to < lines.length) insert += '\n';
      else if (from === end && from > 0) insert = `\n${insert}`;
    }
    changes.push({ from, to, insert });
  }
  return changes;
}
