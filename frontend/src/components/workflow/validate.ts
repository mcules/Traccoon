import type { WorkflowGraph } from "./types";
import { tr } from "../../i18n";

/**
 * Preliminary check in the browser (the final one is done by the server).
 * Returns a list of ready translated messages; empty means all right.
 */
export function validateGraph(graph: WorkflowGraph): string[] {
  const errors: string[] = [];
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const ids = new Set(nodes.map((n) => n.id));

  const starts = nodes.filter((n) => n.type === "start");
  const ends = nodes.filter((n) => n.type === "end");
  if (starts.length !== 1) errors.push(tr("validate.there_exactly_one_start", { count: starts.length }));
  if (ends.length < 1) errors.push(tr("validate.there_least_one_end"));

  // Lose Kanten (Quelle/Ziel fehlt)
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) {
      errors.push(tr("validate.edge_edge_points_node", { edge: e.id }));
    }
  }

  const hasOut = new Set(edges.map((e) => e.source));
  const hasIn = new Set(edges.map((e) => e.target));

  for (const n of nodes) {
    const label = n.data.config.label || n.id;
    // Unverbundene Knoten
    if (n.type !== "start" && !hasIn.has(n.id)) {
      errors.push(tr("validate.node_node_no_incoming", { node: label }));
    }
    if (n.type !== "end" && !hasOut.has(n.id)) {
      errors.push(tr("validate.node_node_no_outgoing", { node: label }));
    }
    // Approval: both handles have to be served
    if (n.type === "approval") {
      const handles = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      if (!handles.has("approved"))
        errors.push(tr("validate.approval_node_approved_outlet", { node: label }));
      if (!handles.has("rejected"))
        errors.push(tr("validate.approval_node_rejected_outlet", { node: label }));
    }
    // Branch: every path should have an edge
    if (n.type === "decision") {
      const branches = n.data.config.branches || [];
      if (branches.length === 0) {
        errors.push(tr("validate.decision_node_no_branches", { node: label }));
      }
      const served = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      for (const b of branches) {
        if (!served.has(b.handle))
          errors.push(tr("validate.decision_node_branch_branch", { node: label, branch: b.label || b.handle }));
      }
    }
  }

  return errors;
}
