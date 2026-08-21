import { MarkerType, type Edge } from "@xyflow/react";
import type { WorkflowGraph } from "./types";
import type { FlowNode, RuntimeState } from "./nodes/shared";
import { feedbackEdges } from "./layout";

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

/**
 * Before the artifact register there was a status action of its own per subject. Both are
 * `set_status` today; in published versions the old names still stand though (those are
 * immutable). Renaming them here while loading keeps the interface single-named, and whoever
 * touches such a node saves it under the new name anyway.
 */
const ALT_ACTION: Record<string, string> = {
  set_agent_status: "set_status",
  set_purchase_status: "set_status",
};

function neuerName(a: string): string {
  return ALT_ACTION[a] || a;
}

/**
 * Aktions-Konfiguration vereinheitlichen.
 *
 * The editor writes `{action: {action, params}}`, while older and machine generated graphs
 * (the hardware procurement for instance) write the flat form
 * `{action: "name", status: "ordered"}`. The backend understands both, but with the flat form
 * the interface showed neither the action nor the parameters, and the first edit would have
 * overwritten them. That is why it is rewritten here while loading; what is saved is then the uniform form.
 */
function normaliseAction(config: any): any {
  const roh = config?.action;
  if (roh && typeof roh === "object" && typeof roh.action === "string") {
    const { hold_reason, ...params } = roh.params || {};
    if (!ALT_ACTION[roh.action] && hold_reason === undefined) return config;
    return {
      ...config,
      // `hold_reason` was called that in the predecessor; `set_status` calls it `reason`.
      action: { action: neuerName(roh.action), params: { ...params, reason: hold_reason } },
    };
  }
  if (typeof roh !== "string") return config;
  const { action, kind, label, group, hold_reason, ...remainder } = config;
  const params = hold_reason !== undefined ? { ...remainder, reason: hold_reason } : remainder;
  return { label, group, action: { action: neuerName(roh || kind || "noop"), params } };
}

/** WorkflowGraph to the React Flow format (plus optional runtime states). */
export function graphToFlow(
  graph: WorkflowGraph,
  rs?: Record<string, RuntimeState>
): { nodes: FlowNode[]; edges: Edge[] } {
  const nodes: FlowNode[] = (graph.nodes || []).map((n) => ({
    id: n.id,
    type: n.type,
    position: n.position || { x: 0, y: 0 },
    // Without a start no process runs, and it cannot be removed with a key either
    // (the configuration area hides the wastebasket there as well).
    deletable: n.type !== "start",
    data: {
      config: n.type === "auto_action" ? normaliseAction(n.data.config) : n.data.config,
      runtimeState: rs?.[n.id],
    },
  }));
  // Determine back edges (loops) once: the edge then draws itself differently.
  const rueck = feedbackEdges(graph);
  const edges: Edge[] = (graph.edges || []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle ?? undefined,
    targetHandle: e.targetHandle ?? undefined,
    type: "condition",
    label: edgeLabel(e),
    data: { feedback: rueck.has(e.id) },
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes, edges };
}

/** React Flow format to WorkflowGraph (runtime fields are removed). */
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


/**
 * A graph as comparable text: order and notation drop out.
 *
 * Two questions hang off it, and both go wrong when one simply takes `JSON.stringify`: have
 * I changed something? And is out there running the same thing that stands here? The nodes
 * come in one order or another, and the keys of a configuration object stand differently
 * after loading than after editing. What is compared is therefore a sorted version boiled
 * down to the essentials.
 */
/**
 * Der funktionale Inhalt eines Graphen, ohne die Anordnung.
 *
 * Das Gegenstück zu `workflow_graph.inhalts_signatur` im Backend, und beide müssen dieselbe
 * Antwort geben: sonst zeigt der Editor eine Abweichung, die der Server nicht sieht (oder
 * umgekehrt). Positionen fehlen bewusst — ein verschobener Kasten ändert nichts daran, was
 * der Ablauf tut.
 */
export function inhaltsSignatur(graph: WorkflowGraph | null | undefined): string {
  if (!graph) return "";
  const sortiert = (value: any): any => {
    if (Array.isArray(value)) return value.map(sortiert);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce((acc: any, k) => {
        if (value[k] !== undefined && value[k] !== null) acc[k] = sortiert(value[k]);
        return acc;
      }, {});
    }
    return value;
  };
  const nachId = (a: any, b: any) => String(a.id).localeCompare(String(b.id));
  return JSON.stringify({
    n: [...(graph.nodes || [])].sort(nachId).map((n: any) => ({
      id: n.id, type: n.type, c: sortiert(n.data?.config ?? {}),
    })),
    e: [...(graph.edges || [])].sort(nachId).map((e: any) => ({
      id: e.id, s: e.source, t: e.target,
      h: e.sourceHandle ?? "", l: typeof e.label === "string" ? e.label : "",
    })),
  });
}

export function graphSignatur(graph: WorkflowGraph | null | undefined): string {
  if (!graph) return "";
  const sortiert = (value: any): any => {
    if (Array.isArray(value)) return value.map(sortiert);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce((acc: any, k) => {
        if (value[k] !== undefined && value[k] !== null) acc[k] = sortiert(value[k]);
        return acc;
      }, {});
    }
    return value;
  };
  const nachId = (a: any, b: any) => String(a.id).localeCompare(String(b.id));
  return JSON.stringify({
    n: [...(graph.nodes || [])].sort(nachId).map((n: any) => ({
      id: n.id, type: n.type,
      x: Math.round(n.position?.x ?? 0), y: Math.round(n.position?.y ?? 0),
      c: sortiert(n.data?.config ?? {}),
    })),
    e: [...(graph.edges || [])].sort(nachId).map((e: any) => ({
      id: e.id, s: e.source, t: e.target,
      h: e.sourceHandle ?? "", l: typeof e.label === "string" ? e.label : "",
    })),
  });
}
