import { MarkerType, type Edge } from "@xyflow/react";
import type { WorkflowGraph } from "./types";
import type { FlowNode, RuntimeState } from "./nodes/shared";

const HANDLE_LABEL: Record<string, string> = {
  approved: "genehmigt",
  rejected: "abgelehnt",
};

function edgeLabel(e: WorkflowGraph["edges"][number]): string | undefined {
  if (e.label) return e.label;
  const h = e.sourceHandle;
  if (!h || h === "out") return undefined;
  return HANDLE_LABEL[h] || h;
}

/** WorkflowGraph → React-Flow-Format (+ optionale Laufzeit-Zustände). */
export function graphToFlow(
  graph: WorkflowGraph,
  rs?: Record<string, RuntimeState>
): { nodes: FlowNode[]; edges: Edge[] } {
  const nodes: FlowNode[] = (graph.nodes || []).map((n) => ({
    id: n.id,
    type: n.type,
    position: n.position || { x: 0, y: 0 },
    data: { config: n.data.config, runtimeState: rs?.[n.id] },
  }));
  const edges: Edge[] = (graph.edges || []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle ?? undefined,
    targetHandle: e.targetHandle ?? undefined,
    type: "condition",
    label: edgeLabel(e),
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes, edges };
}

/** React-Flow-Format → WorkflowGraph (Laufzeit-Felder werden entfernt). */
export function flowToGraph(nodes: FlowNode[], edges: Edge[]): WorkflowGraph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type!,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: { config: n.data.config },
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
      targetHandle: e.targetHandle ?? null,
      label: typeof e.label === "string" ? e.label : undefined,
    })),
  };
}
