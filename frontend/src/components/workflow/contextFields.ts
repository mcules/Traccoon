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
export interface KontextFeld {
  path: string;
  type: string;
  description: string;
  source: string;
}

export interface KontextFilter { name: string; hilfe: string }

export interface KontextKatalog {
  /** Filter for templates: {{ path | filter:argument }} */
  filter?: KontextFilter[];
  base: Omit<KontextFeld, "source">[];
  triggers: Record<string, Omit<KontextFeld, "source">[]>;
  actions: Record<string, Omit<KontextFeld, "source">[]>;
  nodes: Record<string, Omit<KontextFeld, "source">[]>;
}

const cfgOf = (n: FlowNode) => (n.data?.config || {}) as Record<string, any>;

/** Aktionsname eines auto_action-Knotens (beide Config-Formen). */
function aktionsName(n: FlowNode): string | null {
  if (n.type !== "auto_action") return null;
  const a = cfgOf(n).action;
  if (a && typeof a === "object") return String(a.action || a.kind || "");
  return a ? String(a) : null;
}

export function verfuegbareFelder(
  nodes: FlowNode[],
  katalog: KontextKatalog | undefined,
): KontextFeld[] {
  if (!katalog) return [];
  const out: KontextFeld[] = [];
  const gesehen = new Set<string>();
  const nimm = (felder: Omit<KontextFeld, "source">[] | undefined, quelle: string) => {
    for (const f of felder || []) {
      if (gesehen.has(f.path)) continue;
      gesehen.add(f.path);
      out.push({ ...f, source: quelle });
    }
  };

  nimm(katalog.base, "immer da");

  // Trigger: the event on the start node. Without a trigger the flow can be started by a
  // webhook or a job, whose payload stands as an entry of its own in the catalog.
  const start = nodes.find((n) => n.type === "start");
  const ev = start ? (cfgOf(start).trigger?.event as string | undefined) : undefined;

  // Example payload at the start: only the human knows what a foreign system sends. Once
  // inserted, the editor knows the fields, nested ones included.
  const probe = start ? cfgOf(start).trigger?.sample : undefined;
  if (probe && typeof probe === "object") {
    const wandern = (wert: any, pfad: string, tiefe: number) => {
      if (tiefe > 4 || wert === null || wert === undefined) return;
      if (Array.isArray(wert)) {
        nimm([{ path: pfad, type: "list",
                description: tr("context_fields.eintraege_im_beispiel", { anzahl: wert.length }) }],
             "Beispiel-Nutzlast");
        if (wert.length) wandern(wert[0], `${pfad}.0`, tiefe + 1);
        return;
      }
      if (typeof wert === "object") {
        for (const [k, v] of Object.entries(wert)) wandern(v, pfad ? `${pfad}.${k}` : k, tiefe + 1);
        return;
      }
      const typ = typeof wert === "number" ? "number"
        : typeof wert === "boolean" ? "boolean" : "text";
      nimm([{ path: pfad, type: typ, description: `Beispiel: ${String(wert).slice(0, 40)}` }],
           "Beispiel-Nutzlast");
    };
    wandern(probe, "", 0);
  }
  if (ev && katalog.triggers[ev]) nimm(katalog.triggers[ev], tr("context_fields.ausloeser", { name: ev }));
  if (!ev && katalog.triggers["(Webhook/Job)"]) {
    nimm(katalog.triggers["(Webhook/Job)"], tr("context_fields.ausloeser", { name: "Webhook/Job" }));
  }

  for (const n of nodes) {
    const aktion = aktionsName(n);
    if (aktion && katalog.actions[aktion]) nimm(katalog.actions[aktion], `Schritt „${aktion}“`);
    if (n.type && katalog.nodes[n.type]) nimm(katalog.nodes[n.type], `Knoten ${n.type}`);
    // Keys put down by the flow itself: only this graph knows those.
    if (aktion === "set_context") {
      const roh = cfgOf(n).action?.params ?? cfgOf(n);
      const zuweisungen = (roh?.set && typeof roh.set === "object" ? roh.set : roh) || {};
      for (const k of Object.keys(zuweisungen)) {
        if (["action", "kind", "label", "set", "group"].includes(k) || gesehen.has(k)) continue;
        gesehen.add(k);
        out.push({ path: k, type: "text", description: "selbst gesetzt",
                  source: "Schritt „Kontext setzen“" });
      }
    }
  }
  return out;
}
