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
  if (starts.length !== 1) errors.push(tr("validate.genau_ein_start", { anzahl: starts.length }));
  if (ends.length < 1) errors.push(tr("validate.mindestens_ein_ende"));

  // Lose Kanten (Quelle/Ziel fehlt)
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) {
      errors.push(tr("validate.lose_kante", { kante: e.id }));
    }
  }

  const hasOut = new Set(edges.map((e) => e.source));
  const hasIn = new Set(edges.map((e) => e.target));

  for (const n of nodes) {
    const label = n.data.config.label || n.id;
    // Unverbundene Knoten
    if (n.type !== "start" && !hasIn.has(n.id)) {
      errors.push(tr("validate.kein_eingang", { knoten: label }));
    }
    if (n.type !== "end" && !hasOut.has(n.id)) {
      errors.push(tr("validate.kein_ausgang", { knoten: label }));
    }
    // Approval: both handles have to be served
    if (n.type === "approval") {
      const handles = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      if (!handles.has("approved"))
        errors.push(tr("validate.freigabe_genehmigt", { knoten: label }));
      if (!handles.has("rejected"))
        errors.push(tr("validate.freigabe_abgelehnt", { knoten: label }));
    }
    // Branch: every path should have an edge
    if (n.type === "decision") {
      const branches = n.data.config.branches || [];
      if (branches.length === 0) {
        errors.push(tr("validate.keine_zweige", { knoten: label }));
      }
      const served = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      for (const b of branches) {
        if (!served.has(b.handle))
          errors.push(tr("validate.zweig_offen", { knoten: label, zweig: b.label || b.handle }));
      }
    }
  }

  return errors;
}
