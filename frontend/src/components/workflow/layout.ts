import dagre from "@dagrejs/dagre";
import type { WorkflowGraph } from "./types";

/** Fallback dimensions as long as a node is not rendered (and therefore not measured) yet. */
const NODE_W = 220;
const NODE_H = 88;

/** Spacing in px when the server value (admin setting) is not loaded yet. */
export const DEFAULT_GAP = 40;

/** Measured node size from React Flow (`node.measured`). */
export type NodeSize = { width: number; height: number };

export interface LayoutOptions {
  /** Even spacing between the nodes, horizontally as vertically. */
  gap?: number;
  /** Actual node sizes; without them the fallback dimensions are used. */
  sizes?: Map<string, NodeSize>;
}

/**
 * Find back edges (loops): edges leading back to a node that lies BEFORE the source in the
 * flow.
 *
 * Why that matters: the ticket lifecycle sends back to the implementation node after every
 * waiting point, and that node thereby has nine incoming edges, seven of them from its own
 * successors. As long S curves right across the picture they are the reason an otherwise
 * tidily arranged process looks restless (measured: 16 of 54 lines cross foreign nodes).
 * They are therefore marked specially and drawn dashed around the outside, while the
 * arrangement itself treats them normally.
 */
export function feedbackEdges(graph: WorkflowGraph): Set<string> {
  const out = new Map<string, { id: string; target: string }[]>();
  for (const e of graph.edges) {
    out.set(e.source, [...(out.get(e.source) || []), { id: e.id, target: e.target }]);
  }
  const start = graph.nodes.find((n) => n.type === "start")?.id
    ?? graph.nodes[0]?.id;
  const rueck = new Set<string>();
  const OPEN = 1, DONE = 2;
  const state = new Map<string, number>();

  // Depth first search with colours: an edge onto a node that is still OPEN closes a cycle.
  const lauf = (startId: string) => {
    const stapel: { id: string; i: number }[] = [{ id: startId, i: 0 }];
    state.set(startId, OPEN);
    while (stapel.length) {
      const oben = stapel[stapel.length - 1];
      const edges = out.get(oben.id) || [];
      if (oben.i >= edges.length) {
        state.set(oben.id, DONE);
        stapel.pop();
        continue;
      }
      const k = edges[oben.i++];
      const z = state.get(k.target);
      if (z === OPEN) rueck.add(k.id);          // zurück auf einen Knoten im aktuellen Pfad
      else if (z === undefined) {
        state.set(k.target, OPEN);
        stapel.push({ id: k.target, i: 0 });
      }
    }
  };
  if (start) lauf(start);
  // Sort in nodes that are not reachable from the start as well.
  for (const n of graph.nodes) if (!state.has(n.id)) lauf(n.id);
  return rueck;
}

/** Checks whether all positions are missing or 0 (then an auto layout is needed). */
export function needsLayout(graph: WorkflowGraph): boolean {
  const ns = graph.nodes || [];
  if (ns.length === 0) return false;
  return ns.every((n) => !n.position || (n.position.x === 0 && n.position.y === 0));
}

/**
 * Computes positions with dagre, the flow from top to bottom (`TB`), so that long processes
 * run into the height of the screen instead of into the width.
 *
 * The visible spacing is the same everywhere: dagre gets the *real* node sizes (instead of a
 * flat estimate), and the rows are afterwards placed on an even grid, so the gap below the
 * tallest card of a row is exactly `gap`. Without this correction, holes of different sizes
 * gape between cards of different heights.
 */
export function layoutGraph(graph: WorkflowGraph, opts: LayoutOptions = {}): WorkflowGraph {
  const gap = Math.max(8, opts.gap ?? DEFAULT_GAP);
  const size = (id: string): NodeSize => opts.sizes?.get(id) ?? { width: NODE_W, height: NODE_H };

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: gap, ranksep: gap });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) {
    const s = size(n.id);
    g.setNode(n.id, { width: s.width, height: s.height });
  }
  // Back edges stay out of the arrangement: they are drawn past the side of the picture
  // anyway and would otherwise pull the nodes towards their loop partners, which is exactly
  // where the back and forth in the ticket lifecycle came from. Measured (forward edges
  // only): lateral offset 9010px to 5020px, width 1780px to 1270px, crossings unchanged.
  const rueck = feedbackEdges(graph);
  const vorwaerts = graph.edges.filter((e) => !rueck.has(e.id));
  for (const e of vorwaerts) g.setEdge(e.source, e.target);
  dagre.layout(g);

  // Nodes with an almost identical centre line form a row (dagre rank).
  const placed = graph.nodes
    .map((n) => ({ node: n, p: g.node(n.id), s: size(n.id) }))
    .filter((x) => x.p);
  const rows: { y: number; items: typeof placed }[] = [];
  for (const item of [...placed].sort((a, b) => a.p.y - b.p.y)) {
    const row = rows.find((r) => Math.abs(r.y - item.p.y) < item.s.height / 2 + 1);
    if (row) row.items.push(item);
    else rows.push({ y: item.p.y, items: [item] });
  }

  // Stack rows with a fixed rhythm, cards within a row aligned at the top.
  const top = new Map<string, number>();
  let cursor = 0;
  for (const row of rows) {
    const height = Math.max(...row.items.map((i) => i.s.height));
    for (const i of row.items) top.set(i.node.id, cursor);
    cursor += height + gap;
  }

  const mitte = ausrichten(rows, vorwaerts, gap);

  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.id);
      if (!p) return n;
      const s = size(n.id);
      return {
        ...n,
        position: {
          x: (mitte.get(n.id) ?? p.x) - s.width / 2,
          y: top.get(n.id) ?? p.y - s.height / 2,
        },
      };
    }),
  };
}

/**
 * Bring chains into one column.
 *
 * dagre sorts the nodes per row with few crossings, but WHERE in the row they sit stays
 * coarse. The result: one step stands left, its successor right, and the flow zigzags.
 *
 * Every node gets the centre of its predecessors respectively successors as its desired
 * position here (the median, as in the Sugiyama method). The row has to keep the order and
 * the minimum distances, so what is sought are positions that come as close as possible to
 * the wishes AND stay sorted. Exactly that is solved precisely by an isotonic regression
 * (pool adjacent violators); merely "pushing to the right" distorts the whole row instead.
 */
function ausrichten(
  rows: { items: { node: { id: string }; p: { x: number }; s: NodeSize }[] }[],
  vorwaerts: WorkflowGraph["edges"],
  gap: number,
): Map<string, number> {
  const x = new Map<string, number>();
  const breite = new Map<string, number>();
  rows.forEach((row) => row.items.forEach((it) => {
    x.set(it.node.id, it.p.x);
    breite.set(it.node.id, it.s.width);
  }));

  const hoch = new Map<string, string[]>();   // Vorgänger
  const runter = new Map<string, string[]>(); // Nachfolger
  for (const e of vorwaerts) {
    if (!x.has(e.source) || !x.has(e.target)) continue;
    hoch.set(e.target, [...(hoch.get(e.target) || []), e.source]);
    runter.set(e.source, [...(runter.get(e.source) || []), e.target]);
  }
  const median = (values: number[]) => {
    if (!values.length) return undefined;
    const s = [...values].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };

  // Record the order per row once (the one dagre chose, with few crossings).
  const series = rows.map((row) =>
    [...row.items].sort((a, b) => a.p.x - b.p.x).map((it) => it.node.id));

  for (let runde = 0; runde < 12; runde++) {
    const abwaerts = runde % 2 === 0;
    const sequence = abwaerts ? series : [...series].reverse();
    for (const line of sequence) {
      const wunsch = line.map((id) => {
        const nachbarn = (abwaerts ? hoch : runter).get(id) || [];
        return median(nachbarn.map((n) => x.get(n)!)) ?? x.get(id)!;
      });
      const platziert = isoton(wunsch, line.map((id) => breite.get(id)!), gap);
      line.forEach((id, i) => x.set(id, platziert[i]));
    }
  }
  return x;
}

/**
 * Positions that come closest to the wishes while keeping the order plus the minimum
 * distance (pool adjacent violators). If a neighbouring pair violates the distance, both are
 * combined into one block and set together on their average, which repeats until everything
 * fits.
 */
function isoton(wunsch: number[], breiten: number[], gap: number): number[] {
  if (!wunsch.length) return [];
  // Factor out the minimum distance: afterwards the values only have to be ascending.
  const versatz: number[] = [0];
  for (let i = 1; i < wunsch.length; i++) {
    versatz[i] = versatz[i - 1] + breiten[i - 1] / 2 + gap + breiten[i] / 2;
  }
  const z = wunsch.map((w, i) => w - versatz[i]);

  const bloecke: { summe: number; anzahl: number; wert: number }[] = [];
  for (const value of z) {
    bloecke.push({ summe: value, anzahl: 1, wert: value });
    while (bloecke.length > 1 && bloecke[bloecke.length - 2].wert > bloecke[bloecke.length - 1].wert) {
      const b = bloecke.pop()!;
      const a = bloecke.pop()!;
      const sum_total = a.summe + b.summe;
      const count = a.anzahl + b.anzahl;
      bloecke.push({ summe: sum_total, anzahl: count, wert: sum_total / count });
    }
  }
  const out: number[] = [];
  for (const b of bloecke) for (let i = 0; i < b.anzahl; i++) out.push(b.wert);
  return out.map((v, i) => v + versatz[i]);
}
