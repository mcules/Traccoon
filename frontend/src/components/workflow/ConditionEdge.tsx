import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";

/** Kante mit optionalem Bedingungs-/Handle-Label in der Mitte. */
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
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const text = (label as string) || (data?.label as string) || "";
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: "rgb(var(--muted))" }} />
      {text && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded border border-line bg-card px-1.5 py-0.5 text-[10px] text-muted"
            style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
          >
            {text}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
