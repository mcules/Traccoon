import dagre from "@dagrejs/dagre";
import type { WorkflowGraph } from "./types";

/** Fallback-Maße, solange ein Knoten noch nicht gerendert (und damit nicht gemessen) ist. */
const NODE_W = 220;
const NODE_H = 88;

/** Abstand in px, wenn der Server-Wert (Admin-Einstellung) noch nicht geladen ist. */
export const DEFAULT_GAP = 40;

/** Gemessene Knotengröße aus React Flow (`node.measured`). */
export type NodeSize = { width: number; height: number };

export interface LayoutOptions {
  /** Gleichmäßiger Abstand zwischen den Knoten — waagerecht wie senkrecht. */
  gap?: number;
  /** Tatsächliche Knotengrößen; ohne sie wird mit Fallback-Maßen gerechnet. */
  sizes?: Map<string, NodeSize>;
}

/**
 * Rückläufe (Schleifen) finden: Kanten, die zu einem Knoten zurückführen, der im Ablauf
 * VOR der Quelle liegt.
 *
 * Warum das zählt: Der Ticket-Lebenszyklus schickt nach jedem Wartepunkt wieder auf den
 * Umsetzungs-Knoten zurück — der hat dadurch neun eingehende Kanten, sieben davon von
 * seinen eigenen Nachfolgern. Als lange S-Kurven quer durchs Bild sind sie der Grund,
 * warum ein sonst ordentlich angeordneter Prozess unruhig aussieht (nachgemessen: 16 von
 * 54 Linien schneiden fremde Knoten). Sie werden deshalb eigens gekennzeichnet und
 * gestrichelt außen herum gezeichnet — die Anordnung selbst berücksichtigt sie normal.
 */
export function feedbackEdges(graph: WorkflowGraph): Set<string> {
  const raus = new Map<string, { id: string; target: string }[]>();
  for (const e of graph.edges) {
    raus.set(e.source, [...(raus.get(e.source) || []), { id: e.id, target: e.target }]);
  }
  const start = graph.nodes.find((n) => n.type === "start")?.id
    ?? graph.nodes[0]?.id;
  const rueck = new Set<string>();
  const OFFEN = 1, FERTIG = 2;
  const zustand = new Map<string, number>();

  // Tiefensuche mit Farben: eine Kante auf einen noch OFFENEN Knoten schließt einen Kreis.
  const lauf = (startId: string) => {
    const stapel: { id: string; i: number }[] = [{ id: startId, i: 0 }];
    zustand.set(startId, OFFEN);
    while (stapel.length) {
      const oben = stapel[stapel.length - 1];
      const kanten = raus.get(oben.id) || [];
      if (oben.i >= kanten.length) {
        zustand.set(oben.id, FERTIG);
        stapel.pop();
        continue;
      }
      const k = kanten[oben.i++];
      const z = zustand.get(k.target);
      if (z === OFFEN) rueck.add(k.id);          // zurück auf einen Knoten im aktuellen Pfad
      else if (z === undefined) {
        zustand.set(k.target, OFFEN);
        stapel.push({ id: k.target, i: 0 });
      }
    }
  };
  if (start) lauf(start);
  // Knoten, die vom Start aus nicht erreichbar sind, trotzdem einordnen.
  for (const n of graph.nodes) if (!zustand.has(n.id)) lauf(n.id);
  return rueck;
}

/** Prüft, ob alle Positionen fehlen/0 sind (dann brauchen wir ein Auto-Layout). */
export function needsLayout(graph: WorkflowGraph): boolean {
  const ns = graph.nodes || [];
  if (ns.length === 0) return false;
  return ns.every((n) => !n.position || (n.position.x === 0 && n.position.y === 0));
}

/**
 * Berechnet Positionen per dagre — Fluss von oben nach unten (`TB`), damit lange Prozesse
 * in die Bildschirmhöhe statt in die Breite laufen.
 *
 * Der sichtbare Abstand ist überall derselbe: dagre bekommt die *echten* Knotengrößen (statt
 * einer pauschalen Schätzung), und die Zeilen werden anschließend auf ein gleichmäßiges Raster
 * gesetzt — die Lücke unter der höchsten Karte einer Zeile ist damit exakt `gap`. Ohne diese
 * Korrektur klaffen zwischen unterschiedlich hohen Karten unterschiedlich große Löcher.
 */
export function layoutGraph(graph: WorkflowGraph, opts: LayoutOptions = {}): WorkflowGraph {
  const gap = Math.max(8, opts.gap ?? DEFAULT_GAP);
  const size = (id: string): NodeSize => opts.sizes?.get(id) ?? { width: NODE_W, height: NODE_H };

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: gap, ranksep: gap });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) {
    const s = size(n.id);
    g.setNode(n.id, { width: s.width, height: s.height });
  }
  // Rückläufe bleiben bei der Anordnung außen vor: sie werden ohnehin seitlich am Bild
  // vorbeigezeichnet und würden die Knoten sonst zu ihren Schleifen-Partnern ziehen — genau
  // daher kam das Hin-und-Her im Ticket-Lebenszyklus. Nachgemessen (nur Vorwärts-Kanten):
  // seitlicher Versatz 9010px → 5020px, Breite 1780px → 1270px, Kreuzungen unverändert.
  const rueck = feedbackEdges(graph);
  const vorwaerts = graph.edges.filter((e) => !rueck.has(e.id));
  for (const e of vorwaerts) g.setEdge(e.source, e.target);
  dagre.layout(g);

  // Knoten mit nahezu gleicher Mittellinie bilden eine Zeile (dagre-Rang).
  const placed = graph.nodes
    .map((n) => ({ node: n, p: g.node(n.id), s: size(n.id) }))
    .filter((x) => x.p);
  const rows: { y: number; items: typeof placed }[] = [];
  for (const item of [...placed].sort((a, b) => a.p.y - b.p.y)) {
    const row = rows.find((r) => Math.abs(r.y - item.p.y) < item.s.height / 2 + 1);
    if (row) row.items.push(item);
    else rows.push({ y: item.p.y, items: [item] });
  }

  // Zeilen mit festem Rhythmus stapeln, Karten innerhalb einer Zeile oben bündig.
  const top = new Map<string, number>();
  let cursor = 0;
  for (const row of rows) {
    const height = Math.max(...row.items.map((i) => i.s.height));
    for (const i of row.items) top.set(i.node.id, cursor);
    cursor += height + gap;
  }

  const mitte = ausrichten(rows, vorwaerts, gap);

  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.id);
      if (!p) return n;
      const s = size(n.id);
      return {
        ...n,
        position: {
          x: (mitte.get(n.id) ?? p.x) - s.width / 2,
          y: top.get(n.id) ?? p.y - s.height / 2,
        },
      };
    }),
  };
}

/**
 * Ketten in eine Spalte bringen.
 *
 * dagre sortiert die Knoten je Zeile kreuzungsarm — WO in der Zeile sie sitzen, bleibt aber
 * grob. Ergebnis: ein Schritt steht links, sein Nachfolger rechts, der Fluss zickzackt.
 *
 * Jeder Knoten bekommt hier die Mitte seiner Vorgänger bzw. Nachfolger als Wunschposition
 * (Median, wie im Sugiyama-Verfahren). Die Zeile muss die Reihenfolge und die Mindest-
 * abstände einhalten — gesucht sind also Positionen, die den Wünschen möglichst nahe
 * kommen UND sortiert bleiben. Genau das löst eine isotone Regression (Pool Adjacent
 * Violators) exakt; ein bloßes „nach rechts wegschieben" verzerrt dagegen die ganze Zeile.
 */
function ausrichten(
  rows: { items: { node: { id: string }; p: { x: number }; s: NodeSize }[] }[],
  vorwaerts: WorkflowGraph["edges"],
  gap: number,
): Map<string, number> {
  const x = new Map<string, number>();
  const breite = new Map<string, number>();
  rows.forEach((row) => row.items.forEach((it) => {
    x.set(it.node.id, it.p.x);
    breite.set(it.node.id, it.s.width);
  }));

  const hoch = new Map<string, string[]>();   // Vorgänger
  const runter = new Map<string, string[]>(); // Nachfolger
  for (const e of vorwaerts) {
    if (!x.has(e.source) || !x.has(e.target)) continue;
    hoch.set(e.target, [...(hoch.get(e.target) || []), e.source]);
    runter.set(e.source, [...(runter.get(e.source) || []), e.target]);
  }
  const median = (werte: number[]) => {
    if (!werte.length) return undefined;
    const s = [...werte].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };

  // Reihenfolge je Zeile einmal festhalten (die von dagre gewählte, kreuzungsarme).
  const reihen = rows.map((row) =>
    [...row.items].sort((a, b) => a.p.x - b.p.x).map((it) => it.node.id));

  for (let runde = 0; runde < 12; runde++) {
    const abwaerts = runde % 2 === 0;
    const folge = abwaerts ? reihen : [...reihen].reverse();
    for (const zeile of folge) {
      const wunsch = zeile.map((id) => {
        const nachbarn = (abwaerts ? hoch : runter).get(id) || [];
        return median(nachbarn.map((n) => x.get(n)!)) ?? x.get(id)!;
      });
      const platziert = isoton(wunsch, zeile.map((id) => breite.get(id)!), gap);
      zeile.forEach((id, i) => x.set(id, platziert[i]));
    }
  }
  return x;
}

/**
 * Positionen, die den Wünschen am nächsten kommen und dabei Reihenfolge plus Mindestabstand
 * einhalten (Pool Adjacent Violators). Verletzt ein Nachbarpaar den Abstand, werden beide zu
 * einem Block zusammengefasst und gemeinsam auf ihren Durchschnitt gesetzt — das wiederholt
 * sich, bis alles passt.
 */
function isoton(wunsch: number[], breiten: number[], gap: number): number[] {
  if (!wunsch.length) return [];
  // Mindestabstand herausrechnen: danach müssen die Werte nur noch aufsteigend sein.
  const versatz: number[] = [0];
  for (let i = 1; i < wunsch.length; i++) {
    versatz[i] = versatz[i - 1] + breiten[i - 1] / 2 + gap + breiten[i] / 2;
  }
  const z = wunsch.map((w, i) => w - versatz[i]);

  const bloecke: { summe: number; anzahl: number; wert: number }[] = [];
  for (const wert of z) {
    bloecke.push({ summe: wert, anzahl: 1, wert });
    while (bloecke.length > 1 && bloecke[bloecke.length - 2].wert > bloecke[bloecke.length - 1].wert) {
      const b = bloecke.pop()!;
      const a = bloecke.pop()!;
      const summe = a.summe + b.summe;
      const anzahl = a.anzahl + b.anzahl;
      bloecke.push({ summe, anzahl, wert: summe / anzahl });
    }
  }
  const out: number[] = [];
  for (const b of bloecke) for (let i = 0; i < b.anzahl; i++) out.push(b.wert);
  return out.map((v, i) => v + versatz[i]);
}
