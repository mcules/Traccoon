import { ViewportPortal } from "@xyflow/react";
import { tr } from "../../i18n";
import type { FlowNode } from "./nodes/shared";

/** Phasen eines Ablaufs — Reihenfolge bestimmt die Farbe, nicht die Anordnung. */
export const PHASES: [string, string, string][] = [
  ["start", "Eingang", "59 130 246"],
  ["planung", "Planung", "168 85 247"],
  ["umsetzung", "Umsetzung", "34 197 94"],
  ["aufteilung", "Aufteilung", "236 72 153"],
  ["abnahme", "Abnahme", "234 179 8"],
  ["stoerung", "phase.stoerungen", "239 68 68"],
];

/**
 * Beschriftete Bänder hinter den Knoten — ein Blick genügt, um „wo bin ich" zu beantworten.
 *
 * Die Bänder werden aus den tatsächlichen Positionen der Knoten einer Phase berechnet
 * (`config.group`), nicht fest gesetzt: verschiebt jemand einen Knoten, wandert das Band mit.
 * Ohne Gruppen-Angaben erscheint gar nichts — ältere und selbstgebaute Abläufe sehen aus
 * wie bisher.
 */
export default function PhaseBands({ nodes }: { nodes: FlowNode[] }) {
  // Ein Band je zusammenhängendem Block einer Phase — NICHT eine Hülle um alles. Läuft ein
  // Nebenzweig neben einer anderen Phase her, entstünden sonst zwei ineinander liegende
  // Kästen, und man sieht nicht mehr, was wozu gehört.
  const felder = PHASES.flatMap(([key, label, rgb]) => {
    const teile = nodes.filter((n) => n.data.config.group === key);
    if (!teile.length) return [];
    const hoehe = (n: FlowNode) => n.measured?.height ?? 88;
    const sortiert = [...teile].sort((a, b) => a.position.y - b.position.y);
    const bloecke: FlowNode[][] = [];
    for (const n of sortiert) {
      const letzter = bloecke[bloecke.length - 1];
      const vorheriges = letzter?.[letzter.length - 1];
      // Anschluss, wenn der nächste Knoten unmittelbar folgt (max. eine Zeile Abstand).
      const anschluss = vorheriges
        && n.position.y - (vorheriges.position.y + hoehe(vorheriges)) < hoehe(n) + 80;
      if (anschluss) letzter.push(n);
      else bloecke.push([n]);
    }
    const luft = 26;
    return bloecke.map((block, i) => {
      const xs = block.map((n) => n.position.x);
      const rechts = Math.max(...block.map((n) => n.position.x + (n.measured?.width ?? 220)));
      const unten = Math.max(...block.map((n) => n.position.y + hoehe(n)));
      const oben = Math.min(...block.map((n) => n.position.y));
      return {
        key: `${key}-${i}`, label, rgb, zeigLabel: i === 0,
        x: Math.min(...xs) - luft, y: oben - luft - 20,
        w: rechts - Math.min(...xs) + luft * 2, h: unten - oben + luft * 2 + 20,
      };
    });
  });

  if (!felder.length) return null;
  return (
    <ViewportPortal>
      {felder.map((f) => (
        <div
          key={f.key}
          // Hinter den Knoten und für Klicks durchlässig — es ist nur Orientierung.
          style={{
            position: "absolute",
            transform: `translate(${f.x}px, ${f.y}px)`,
            width: f.w, height: f.h,
            border: `1px solid rgb(${f.rgb} / 0.35)`,
            background: `rgb(${f.rgb} / 0.05)`,
            borderRadius: 14,
            pointerEvents: "none",
            zIndex: -1,
          }}
        >
          {f.zeigLabel && <span
            style={{
              position: "absolute", top: 6, left: 12,
              fontSize: 13, letterSpacing: "0.04em", textTransform: "uppercase",
              color: `rgb(${f.rgb})`, opacity: 0.85,
            }}
          >
            {tr(f.label)}
          </span>}
        </div>
      ))}
    </ViewportPortal>
  );
}
