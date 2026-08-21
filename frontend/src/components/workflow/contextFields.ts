import type { FlowNode } from "./nodes/shared";
import { tr } from "../../i18n";

/**
 * Which context fields are available in THIS flow?
 *
 * The catalog from the backend says who writes what into the context (triggers, actions,
 * node types). Which of those apply here is revealed by the graph itself: its trigger event,
 * the actions it uses and the keys it puts down itself over `set_context`.
 *
 * Deliberately not filtered by order: whether a field has already been written at this point
 * depends on the path taken, not on the drawing. Better to offer one field too many than to
 * conceal one that exists.
 */
export interface ContextField {
  path: string;
  type: string;
  description: string;
  source: string;
}

export interface ContextFilter { name: string; help: string }

export interface ContextCatalog {
  /** Filter for templates: {{ path | filter:argument }} */
  filter?: ContextFilter[];
  base: Omit<ContextField, "source">[];
  triggers: Record<string, Omit<ContextField, "source">[]>;
  actions: Record<string, Omit<ContextField, "source">[]>;
  nodes: Record<string, Omit<ContextField, "source">[]>;
}

const cfgOf = (n: FlowNode) => (n.data?.config || {}) as Record<string, any>;

/** Aktionsname eines auto_action-Knotens (beide Config-Formen). */
function actionName(n: FlowNode): string | null {
  if (n.type !== "auto_action") return null;
  const a = cfgOf(n).action;
  if (a && typeof a === "object") return String(a.action || a.kind || "");
  return a ? String(a) : null;
}

export function availableFields(
  nodes: FlowNode[],
  catalog: ContextCatalog | undefined,
): ContextField[] {
  if (!catalog) return [];
  const out: ContextField[] = [];
  const seen = new Set<string>();
  const take = (fields: Omit<ContextField, "source">[] | undefined, source: string) => {
    for (const f of fields || []) {
      if (seen.has(f.path)) continue;
      seen.add(f.path);
      out.push({ ...f, source: source });
    }
  };

  take(catalog.base, "immer da");

  // Trigger: the event on the start node. Without a trigger the flow can be started by a
  // webhook or a job, whose payload stands as an entry of its own in the catalog.
  const start = nodes.find((n) => n.type === "start");
  const ev = start ? (cfgOf(start).trigger?.event as string | undefined) : undefined;

  // Example payload at the start: only the human knows what a foreign system sends. Once
  // inserted, the editor knows the fields, nested ones included.
  const probe = start ? cfgOf(start).trigger?.sample : undefined;
  if (probe && typeof probe === "object") {
    const walk = (value: any, path: string, depth: number) => {
      if (depth > 4 || value === null || value === undefined) return;
      if (Array.isArray(value)) {
        take([{ path: path, type: "list",
                description: tr("context_fields.count_entries_sample", { count: value.length }) }],
             "Beispiel-Nutzlast");
        if (value.length) walk(value[0], `${path}.0`, depth + 1);
        return;
      }
      if (typeof value === "object") {
        for (const [k, v] of Object.entries(value)) walk(v, path ? `${path}.${k}` : k, depth + 1);
        return;
      }
      const kind = typeof value === "number" ? "number"
        : typeof value === "boolean" ? "boolean" : "text";
      take([{ path: path, type: kind, description: `Beispiel: ${String(value).slice(0, 40)}` }],
           "Beispiel-Nutzlast");
    };
    walk(probe, "", 0);
  }
  if (ev && catalog.triggers[ev]) take(catalog.triggers[ev], tr("context_fields.trigger_name", { name: ev }));
  if (!ev && catalog.triggers["(Webhook/Job)"]) {
    take(catalog.triggers["(Webhook/Job)"], tr("context_fields.trigger_name", { name: "Webhook/Job" }));
  }

  for (const n of nodes) {
    const action = actionName(n);
    if (action && catalog.actions[action]) take(catalog.actions[action], `Schritt „${action}“`);
    if (n.type && catalog.nodes[n.type]) take(catalog.nodes[n.type], `Knoten ${n.type}`);
    // Keys put down by the flow itself: only this graph knows those.
    if (action === "set_context") {
      const raw = cfgOf(n).action?.params ?? cfgOf(n);
      const assignments = (raw?.set && typeof raw.set === "object" ? raw.set : raw) || {};
      for (const k of Object.keys(assignments)) {
        if (["action", "kind", "label", "set", "group"].includes(k) || seen.has(k)) continue;
        seen.add(k);
        out.push({ path: k, type: "text", description: "selbst gesetzt",
                  source: "Schritt „Kontext setzen“" });
      }
    }
  }
  return out;
}
