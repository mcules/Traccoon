import { useState } from "react";
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, useReactFlow, useStore, type EdgeProps,
} from "@xyflow/react";
import { useCanvasReadOnly } from "./canvasMode";

/**
 * Weg für einen Rücklauf (Schleife): seitlich hinaus, außen hoch, seitlich wieder hinein —
 * statt als große S-Kurve quer durch die Mitte.
 *
 * Die Spur liegt umso weiter außen, je länger der Rücksprung ist; zusätzlich versetzt sie
 * ein Wert aus der Kanten-Kennung, damit mehrere Rückläufe nicht übereinanderliegen. Die
 * Ecken sind leicht gerundet, das liest sich ruhiger als spitze Winkel.
 */
function ruecklaufPfad(
  sx: number, sy: number, tx: number, ty: number, id: string,
  rand: { links: number; rechts: number },
): [string, number, number] {
  // Die Spur liegt AUSSERHALB aller Knoten — nur so kreuzt sie garantiert keinen.
  // Mehrere Rückläufe je Seite werden gestaffelt, damit sie nicht aufeinanderliegen.
  const versatz = (Math.abs(hash(id)) % 4) * 26;
  const seite = sx >= tx ? 1 : -1;
  const lane = seite > 0 ? rand.rechts + 48 + versatz : rand.links - 48 - versatz;
  const runter = sy + 26;                 // erst ein Stück unter die Quelle
  const rauf = ty - 26;                   // und oberhalb des Ziels wieder herein
  const r = 14 * seite;
  return [
    `M ${sx},${sy} L ${sx},${runter - 14} Q ${sx},${runter} ${sx + r},${runter} ` +
    `L ${lane - r},${runter} Q ${lane},${runter} ${lane},${runter - 14} ` +
    `L ${lane},${rauf + 14} Q ${lane},${rauf} ${lane - r},${rauf} ` +
    `L ${tx + r},${rauf} Q ${tx},${rauf} ${tx},${rauf + 14} L ${tx},${ty}`,
    lane,
    (runter + rauf) / 2,
  ];
}

/** Kleiner, stabiler Zahlenwert aus der Kanten-Kennung (nur für den Spur-Versatz). */
function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

/**
 * Kante mit optionalem Bedingungs-/Handle-Label — und einem Löschknopf.
 *
 * Eine Verbindung entsteht mit einem Zug, soll aber auch wieder verschwinden können. Der ✕
 * erscheint beim Überfahren oder wenn die Linie ausgewählt ist; Entf/Rücktaste tun
 * dasselbe. In der Laufzeit-Ansicht (read-only) gibt es ihn nicht.
 */
export default function ConditionEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  label,
  data,
  selected,
}: EdgeProps) {
  const feedback = !!data?.feedback;
  // Äußere Kanten des Bildes (als Zeichenkette, damit die Auswahl vergleichbar bleibt und
  // nicht bei jedem Bildaufbau neu auslöst).
  const randKey = useStore((st) => {
    if (!feedback) return "";
    let links = Infinity, rechts = -Infinity;
    for (const [, n] of st.nodeLookup) {
      links = Math.min(links, n.position.x);
      rechts = Math.max(rechts, n.position.x + (n.measured?.width ?? 220));
    }
    return `${Math.round(links)}|${Math.round(rechts)}`;
  });
  const [l, r] = randKey.split("|");
  const rand = { links: Number(l) || 0, rechts: Number(r) || 0 };
  const [path, labelX, labelY] = feedback
    ? ruecklaufPfad(sourceX, sourceY, targetX, targetY, id, rand)
    : getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const readOnly = useCanvasReadOnly();
  const { deleteElements } = useReactFlow();
  const [hover, setHover] = useState(false);
  const text = (label as string) || (data?.label as string) || "";
  const aktiv = !readOnly && (hover || selected);

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        // Breiter unsichtbarer Streifen um die Linie — sonst müsste man die dünne Kurve
        // pixelgenau treffen, um sie auszuwählen.
        interactionWidth={24}
        style={{
          // brand ist im Theme ein fester Hex-Wert (keine CSS-Variable wie muted).
          stroke: selected ? "#0052CC" : "rgb(var(--muted))",
          strokeWidth: selected ? 2 : 1,
          // Rückläufe treten zurück: gestrichelt und blasser — sie sind die Ausnahme
          // im Ablauf, nicht der Hauptweg.
          strokeDasharray: feedback && !selected ? "5 4" : undefined,
          opacity: feedback && !selected ? 0.55 : 1,
        }}
      />
      {/* Eigener Streifen nur fürs Überfahren — BaseEdge meldet kein hover. */}
      <path
        d={path}
        fill="none"
        strokeWidth={24}
        stroke="transparent"
        style={{ pointerEvents: readOnly ? "none" : "stroke" }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      />
      <EdgeLabelRenderer>
        <div
          className="absolute flex items-center gap-1"
          style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          {text && (
            <span className="pointer-events-none rounded border border-line bg-card px-1.5 py-0.5 text-[10px] text-muted">
              {text}
            </span>
          )}
          {aktiv && (
            <button
              type="button"
              title="Verbindung löschen"
              aria-label="Verbindung löschen"
              // nodrag/nopan: sonst schwenkt React Flow beim Klick die Fläche.
              className="nodrag nopan pointer-events-auto flex h-5 w-5 items-center justify-center
                         rounded-full border border-line bg-card text-[11px] leading-none text-muted
                         shadow-sm hover:border-red-400 hover:text-red-400"
              onClick={(e) => {
                e.stopPropagation();
                deleteElements({ edges: [{ id }] });
              }}
            >
              ✕
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
