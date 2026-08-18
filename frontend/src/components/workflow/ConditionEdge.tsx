import { useState } from "react";
import { tr } from "../../i18n";
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, useReactFlow, useStore, type EdgeProps,
} from "@xyflow/react";
import { useCanvasReadOnly } from "./canvasMode";

/**
 * Path for a back edge (loop): out to the side, up along the outside, back in from the side,
 * instead of a large S curve right through the middle.
 *
 * The track lies further outside the longer the jump back is; in addition it is offset by a
 * value from the edge id so that several back edges do not lie on top of each other. The
 * corners are slightly rounded, which reads more calmly than sharp angles.
 */
function ruecklaufPfad(
  sx: number, sy: number, tx: number, ty: number, id: string,
  rand: { links: number; rechts: number },
): [string, number, number] {
  // The track lies OUTSIDE all nodes: only that way does it cross none of them for certain.
  // Several back edges per side are staggered so that they do not lie on top of each other.
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

/** Small, stable numeric value from the edge id (only for the track offset). */
function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

/**
 * Edge with an optional condition or handle label, and a delete button.
 *
 * A connection comes into being with one drag but should be able to disappear again. The ✕
 * appears on hovering or when the line is selected; delete and backspace do the same. In the
 * runtime view (read-only) it does not exist.
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
  // Outer edges of the picture (as a string, so that the selection stays comparable and does
  // not trigger anew on every render).
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
        // A wide invisible strip around the line; otherwise one would have to hit the thin
        // curve pixel exactly in order to select it.
        interactionWidth={24}
        style={{
          // brand is a fixed hex value in the theme (not a CSS variable like muted).
          stroke: selected ? "#0052CC" : "rgb(var(--muted))",
          strokeWidth: selected ? 2 : 1,
          // Back edges step back: dashed and paler, because they are the exception in the
          // flow, not the main path.
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
            <span className="pointer-events-none rounded border border-line bg-card px-1.5 py-0.5 text-[11px] text-muted">
              {text}
            </span>
          )}
          {aktiv && (
            <button
              type="button"
              title={tr("condition_edge.verbindung_loeschen")}
              aria-label={tr("condition_edge.verbindung_loeschen")}
              // nodrag/nopan: otherwise React Flow pans the canvas on a click.
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
