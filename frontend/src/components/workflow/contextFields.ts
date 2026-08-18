import type { FlowNode } from "./nodes/shared";

/**
 * Welche Kontextfelder stehen in DIESEM Ablauf zur Verfügung?
 *
 * Der Katalog aus dem Backend sagt, wer was in den Kontext schreibt (Auslöser, Aktionen,
 * Knotentypen). Was davon hier gilt, verrät der Graph selbst: sein Trigger-Ereignis, die
 * Aktionen, die er benutzt, und die Schlüssel, die er sich per `set_context` selbst legt.
 *
 * Bewusst nicht nach Reihenfolge gefiltert: ob ein Feld an dieser Stelle schon geschrieben
 * wurde, hängt vom gelaufenen Weg ab, nicht von der Zeichnung. Lieber ein Feld zu viel
 * anbieten als eines verschweigen, das es gibt.
 */
export interface KontextFeld {
  pfad: string;
  typ: string;
  beschreibung: string;
  quelle: string;
}

export interface KontextKatalog {
  basis: Omit<KontextFeld, "quelle">[];
  ausloeser: Record<string, Omit<KontextFeld, "quelle">[]>;
  aktionen: Record<string, Omit<KontextFeld, "quelle">[]>;
  knoten: Record<string, Omit<KontextFeld, "quelle">[]>;
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
  const nimm = (felder: Omit<KontextFeld, "quelle">[] | undefined, quelle: string) => {
    for (const f of felder || []) {
      if (gesehen.has(f.pfad)) continue;
      gesehen.add(f.pfad);
      out.push({ ...f, quelle });
    }
  };

  nimm(katalog.basis, "immer da");

  // Auslöser: das Ereignis am Start-Knoten. Ohne Trigger kann der Ablauf von einem Webhook
  // oder Job angestoßen werden — dessen Nutzlast steht als eigener Eintrag im Katalog.
  const start = nodes.find((n) => n.type === "start");
  const ev = start ? (cfgOf(start).trigger?.event as string | undefined) : undefined;
  if (ev && katalog.ausloeser[ev]) nimm(katalog.ausloeser[ev], `Auslöser ${ev}`);
  if (!ev && katalog.ausloeser["(Webhook/Job)"]) {
    nimm(katalog.ausloeser["(Webhook/Job)"], "Auslöser Webhook/Job");
  }

  for (const n of nodes) {
    const aktion = aktionsName(n);
    if (aktion && katalog.aktionen[aktion]) nimm(katalog.aktionen[aktion], `Schritt „${aktion}“`);
    if (n.type && katalog.knoten[n.type]) nimm(katalog.knoten[n.type], `Knoten ${n.type}`);
    // Selbst gelegte Schlüssel: die kennt nur dieser Graph.
    if (aktion === "set_context") {
      const roh = cfgOf(n).action?.params ?? cfgOf(n);
      const zuweisungen = (roh?.set && typeof roh.set === "object" ? roh.set : roh) || {};
      for (const k of Object.keys(zuweisungen)) {
        if (["action", "kind", "label", "set", "group"].includes(k) || gesehen.has(k)) continue;
        gesehen.add(k);
        out.push({ pfad: k, typ: "text", beschreibung: "selbst gesetzt", quelle: "Schritt „Kontext setzen“" });
      }
    }
  }
  return out;
}
