import type { WorkflowGraph } from "./types";

/**
 * Clientseitige Vorab-Validierung (die endgültige macht das Backend).
 * Liefert eine Liste deutscher Fehlermeldungen — leer = ok.
 */
export function validateGraph(graph: WorkflowGraph): string[] {
  const errors: string[] = [];
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const ids = new Set(nodes.map((n) => n.id));

  const starts = nodes.filter((n) => n.type === "start");
  const ends = nodes.filter((n) => n.type === "end");
  if (starts.length !== 1) errors.push(`Es muss genau einen Start-Knoten geben (aktuell ${starts.length}).`);
  if (ends.length < 1) errors.push("Es muss mindestens einen Ende-Knoten geben.");

  // Lose Kanten (Quelle/Ziel fehlt)
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) {
      errors.push(`Kante ${e.id} zeigt auf einen nicht vorhandenen Knoten.`);
    }
  }

  const hasOut = new Set(edges.map((e) => e.source));
  const hasIn = new Set(edges.map((e) => e.target));

  for (const n of nodes) {
    const label = n.data.config.label || n.id;
    // Unverbundene Knoten
    if (n.type !== "start" && !hasIn.has(n.id)) {
      errors.push(`Knoten „${label}" hat keinen eingehenden Weg.`);
    }
    if (n.type !== "end" && !hasOut.has(n.id)) {
      errors.push(`Knoten „${label}" hat keinen ausgehenden Weg.`);
    }
    // Freigabe: beide Handles müssen bedient sein
    if (n.type === "approval") {
      const handles = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      if (!handles.has("approved"))
        errors.push(`Freigabe „${label}": Ausgang „genehmigt" ist nicht verbunden.`);
      if (!handles.has("rejected"))
        errors.push(`Freigabe „${label}": Ausgang „abgelehnt" ist nicht verbunden.`);
    }
    // Verzweigung: jeder Zweig sollte eine Kante haben
    if (n.type === "decision") {
      const branches = n.data.config.branches || [];
      if (branches.length === 0) {
        errors.push(`Verzweigung „${label}" hat keine Zweige definiert.`);
      }
      const served = new Set(
        edges.filter((e) => e.source === n.id).map((e) => e.sourceHandle || "out")
      );
      for (const b of branches) {
        if (!served.has(b.handle))
          errors.push(`Verzweigung „${label}": Zweig „${b.label || b.handle}" ist nicht verbunden.`);
      }
    }
  }

  return errors;
}
