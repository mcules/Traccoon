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
  const back = new Set<string>();
  const OPEN = 1, DONE = 2;
  const state = new Map<string, number>();

  // Depth first search with colours: an edge onto a node that is still OPEN closes a cycle.
  const run = (startId: string) => {
    const batch: { id: string; i: number }[] = [{ id: startId, i: 0 }];
    state.set(startId, OPEN);
    while (batch.length) {
      const above = batch[batch.length - 1];
      const edges = out.get(above.id) || [];
      if (above.i >= edges.length) {
        state.set(above.id, DONE);
        batch.pop();
        continue;
      }
      const k = edges[above.i++];
      const z = state.get(k.target);
      if (z === OPEN) back.add(k.id);          // back onto a node in the current path
      else if (z === undefined) {
        state.set(k.target, OPEN);
        batch.push({ id: k.target, i: 0 });
      }
    }
  };
  if (start) run(start);
  // Sort in nodes that are not reachable from the start as well.
  for (const n of graph.nodes) if (!state.has(n.id)) run(n.id);
  return back;
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
  const back = feedbackEdges(graph);
  const forward = graph.edges.filter((e) => !back.has(e.id));
  for (const e of forward) g.setEdge(e.source, e.target);
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

  const middle = align(rows, forward, gap);

  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.id);
      if (!p) return n;
      const s = size(n.id);
      return {
        ...n,
        position: {
          x: (middle.get(n.id) ?? p.x) - s.width / 2,
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
function align(
  rows: { items: { node: { id: string }; p: { x: number }; s: NodeSize }[] }[],
  forward: WorkflowGraph["edges"],
  gap: number,
): Map<string, number> {
  const x = new Map<string, number>();
  const width = new Map<string, number>();
  rows.forEach((row) => row.items.forEach((it) => {
    x.set(it.node.id, it.p.x);
    width.set(it.node.id, it.s.width);
  }));

  const high = new Map<string, string[]>();   // the predecessors
  const down = new Map<string, string[]>(); // Nachfolger
  for (const e of forward) {
    if (!x.has(e.source) || !x.has(e.target)) continue;
    high.set(e.target, [...(high.get(e.target) || []), e.source]);
    down.set(e.source, [...(down.get(e.source) || []), e.target]);
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

  for (let round = 0; round < 12; round++) {
    const downward = round % 2 === 0;
    const sequence = downward ? series : [...series].reverse();
    for (const line of sequence) {
      const wish = line.map((id) => {
        const neighbours = (downward ? high : down).get(id) || [];
        return median(neighbours.map((n) => x.get(n)!)) ?? x.get(id)!;
      });
      const placed = isoton(wish, line.map((id) => width.get(id)!), gap);
      line.forEach((id, i) => x.set(id, placed[i]));
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
function isoton(wish: number[], widths: number[], gap: number): number[] {
  if (!wish.length) return [];
  // Factor out the minimum distance: afterwards the values only have to be ascending.
  const offset: number[] = [0];
  for (let i = 1; i < wish.length; i++) {
    offset[i] = offset[i - 1] + widths[i - 1] / 2 + gap + widths[i] / 2;
  }
  const z = wish.map((w, i) => w - offset[i]);

  const blocks: { sum: number; count: number; value: number }[] = [];
  for (const value of z) {
    blocks.push({ sum: value, count: 1, value: value });
    while (blocks.length > 1 && blocks[blocks.length - 2].value > blocks[blocks.length - 1].value) {
      const b = blocks.pop()!;
      const a = blocks.pop()!;
      const sum_total = a.sum + b.sum;
      const count = a.count + b.count;
      blocks.push({ sum: sum_total, count: count, value: sum_total / count });
    }
  }
  const out: number[] = [];
  for (const b of blocks) for (let i = 0; i < b.count; i++) out.push(b.value);
  return out.map((v, i) => v + offset[i]);
}
