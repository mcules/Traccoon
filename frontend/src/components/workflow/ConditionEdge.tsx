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
function ruecklaufPath(
  sx: number, sy: number, tx: number, ty: number, id: string,
  edge: { links: number; right: number },
): [string, number, number] {
  // The track lies OUTSIDE all nodes: only that way does it cross none of them for certain.
  // Several back edges per side are staggered so that they do not lie on top of each other.
  const offset = (Math.abs(hash(id)) % 4) * 26;
  const page = sx >= tx ? 1 : -1;
  const lane = page > 0 ? edge.right + 48 + offset : edge.links - 48 - offset;
  const down = sy + 26;                 // erst ein Stück unter die Quelle
  const up = ty - 26;                   // und oberhalb des Ziels wieder herein
  const r = 14 * page;
  return [
    `M ${sx},${sy} L ${sx},${down - 14} Q ${sx},${down} ${sx + r},${down} ` +
    `L ${lane - r},${down} Q ${lane},${down} ${lane},${down - 14} ` +
    `L ${lane},${up + 14} Q ${lane},${up} ${lane - r},${up} ` +
    `L ${tx + r},${up} Q ${tx},${up} ${tx},${up + 14} L ${tx},${ty}`,
    lane,
    (down + up) / 2,
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
  const edgeKey = useStore((st) => {
    if (!feedback) return "";
    let links = Infinity, right = -Infinity;
    for (const [, n] of st.nodeLookup) {
      links = Math.min(links, n.position.x);
      right = Math.max(right, n.position.x + (n.measured?.width ?? 220));
    }
    return `${Math.round(links)}|${Math.round(right)}`;
  });
  const [l, r] = edgeKey.split("|");
  const edge = { links: Number(l) || 0, right: Number(r) || 0 };
  const [path, labelX, labelY] = feedback
    ? ruecklaufPath(sourceX, sourceY, targetX, targetY, id, edge)
    : getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const readOnly = useCanvasReadOnly();
  const { deleteElements } = useReactFlow();
  const [hover, setHover] = useState(false);
  const text = (label as string) || (data?.label as string) || "";
  const active = !readOnly && (hover || selected);

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
          {active && (
            <button
              type="button"
              title={tr("condition_edge.delete_connection")}
              aria-label={tr("condition_edge.delete_connection")}
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
