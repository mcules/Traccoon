import type { FlowNode } from "./nodes/shared";
import { tr } from "../../i18n";

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

export interface KontextFilter { name: string; hilfe: string }

export interface KontextKatalog {
  /** Filter für Vorlagen: {{ pfad | filter:argument }} */
  filter?: KontextFilter[];
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

  // Beispiel-Nutzlast am Start: was ein fremdes System schickt, weiß nur der Mensch.
  // Einmal eingefügt, kennt der Editor die Felder — inklusive verschachtelter.
  const probe = start ? cfgOf(start).trigger?.sample : undefined;
  if (probe && typeof probe === "object") {
    const wandern = (wert: any, pfad: string, tiefe: number) => {
      if (tiefe > 4 || wert === null || wert === undefined) return;
      if (Array.isArray(wert)) {
        nimm([{ pfad, typ: "liste", beschreibung: tr("context_fields.eintraege_im_beispiel", { anzahl: wert.length }) }],
             "Beispiel-Nutzlast");
        if (wert.length) wandern(wert[0], `${pfad}.0`, tiefe + 1);
        return;
      }
      if (typeof wert === "object") {
        for (const [k, v] of Object.entries(wert)) wandern(v, pfad ? `${pfad}.${k}` : k, tiefe + 1);
        return;
      }
      const typ = typeof wert === "number" ? "zahl" : typeof wert === "boolean" ? "ja/nein" : "text";
      nimm([{ pfad, typ, beschreibung: `Beispiel: ${String(wert).slice(0, 40)}` }],
           "Beispiel-Nutzlast");
    };
    wandern(probe, "", 0);
  }
  if (ev && katalog.ausloeser[ev]) nimm(katalog.ausloeser[ev], tr("context_fields.ausloeser", { name: ev }));
  if (!ev && katalog.ausloeser["(Webhook/Job)"]) {
    nimm(katalog.ausloeser["(Webhook/Job)"], tr("context_fields.ausloeser", { name: "Webhook/Job" }));
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
