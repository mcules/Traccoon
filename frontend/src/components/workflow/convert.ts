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
 * Vor dem Artefakt-Register gab es je Subjekt eine eigene Zustands-Aktion. Beide sind heute
 * `set_status`; in veröffentlichten Versionen stehen die alten Namen aber weiter (die sind
 * unveränderlich). Sie hier beim Laden umzubenennen hält die Oberfläche einnamig — und wer
 * so einen Knoten anfasst, speichert ihn schon unter dem neuen Namen.
 */
const ALT_AKTION: Record<string, string> = {
  set_agent_status: "set_status",
  set_purchase_status: "set_status",
};

function neuerName(a: string): string {
  return ALT_AKTION[a] || a;
}

/**
 * Aktions-Konfiguration vereinheitlichen.
 *
 * Der Editor schreibt `{action: {action, params}}`, ältere und maschinell erzeugte Graphen
 * (z. B. die Hardware-Beschaffung) die flache Form `{action: "name", status: "ordered"}`.
 * Das Backend versteht beide — die Oberfläche zeigte bei der flachen Form aber weder Aktion
 * noch Parameter an, und die erste Bearbeitung hätte sie überschrieben. Deshalb wird hier
 * beim Laden umgeschrieben; gespeichert wird dann die einheitliche Form.
 */
function normalisiereAktion(config: any): any {
  const roh = config?.action;
  if (roh && typeof roh === "object" && typeof roh.action === "string") {
    const { hold_reason, ...params } = roh.params || {};
    if (!ALT_AKTION[roh.action] && hold_reason === undefined) return config;
    return {
      ...config,
      // `hold_reason` hieß beim Vorgänger so — `set_status` nennt es `reason`.
      action: { action: neuerName(roh.action), params: { ...params, reason: hold_reason } },
    };
  }
  if (typeof roh !== "string") return config;
  const { action, kind, label, group, hold_reason, ...rest } = config;
  const params = hold_reason !== undefined ? { ...rest, reason: hold_reason } : rest;
  return { label, group, action: { action: neuerName(roh || kind || "noop"), params } };
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
    // Ohne Start läuft kein Prozess — er lässt sich auch per Taste nicht entfernen
    // (der Konfigurations-Bereich blendet den Papierkorb dort ebenfalls aus).
    deletable: n.type !== "start",
    data: {
      config: n.type === "auto_action" ? normalisiereAktion(n.data.config) : n.data.config,
      runtimeState: rs?.[n.id],
    },
  }));
  // Rückläufe (Schleifen) einmalig bestimmen — die Kante zeichnet sich damit anders.
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


/**
 * Ein Graph als vergleichbarer Text — Reihenfolge und Schreibweise fallen heraus.
 *
 * Zwei Fragen hängen daran, und beide gehen schief, wenn man einfach `JSON.stringify`
 * nimmt: Habe ich etwas geändert? Und läuft draußen dasselbe, was hier steht? Die Knoten
 * kommen mal in dieser, mal in jener Reihenfolge, und die Schlüssel eines Konfigurations-
 * Objekts stehen nach dem Laden anders als nach dem Bearbeiten. Verglichen wird deshalb
 * eine sortierte, auf das Wesentliche eingedampfte Fassung.
 */
export function graphSignatur(graph: WorkflowGraph | null | undefined): string {
  if (!graph) return "";
  const sortiert = (wert: any): any => {
    if (Array.isArray(wert)) return wert.map(sortiert);
    if (wert && typeof wert === "object") {
      return Object.keys(wert).sort().reduce((acc: any, k) => {
        if (wert[k] !== undefined && wert[k] !== null) acc[k] = sortiert(wert[k]);
        return acc;
      }, {});
    }
    return wert;
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
