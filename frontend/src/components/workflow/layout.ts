import dagre from "@dagrejs/dagre";
import type { WorkflowGraph } from "./types";

const NODE_W = 180;
const NODE_H = 60;

/** Prüft, ob alle Positionen fehlen/0 sind (dann brauchen wir ein Auto-Layout). */
export function needsLayout(graph: WorkflowGraph): boolean {
  const ns = graph.nodes || [];
  if (ns.length === 0) return false;
  return ns.every((n) => !n.position || (n.position.x === 0 && n.position.y === 0));
}

/** Berechnet Positionen per dagre (Fallback, wenn im Graph keine gespeichert sind). */
export function layoutGraph(graph: WorkflowGraph): WorkflowGraph {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of graph.edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.id);
      return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } };
    }),
  };
}
