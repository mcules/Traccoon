import { ViewportPortal } from "@xyflow/react";
import { tr } from "../../i18n";
import type { FlowNode } from "./nodes/shared";

/** Phases of a flow; the order determines the colour, not the arrangement. */
export const PHASES: [string, string, string][] = [
  ["start", "Eingang", "59 130 246"],
  ["planung", "Planung", "168 85 247"],
  ["umsetzung", "Umsetzung", "34 197 94"],
  ["aufteilung", "Aufteilung", "236 72 153"],
  ["abnahme", "Abnahme", "234 179 8"],
  ["stoerung", "phase.faults", "239 68 68"],
];

/**
 * Labelled bands behind the nodes: one look answers "where am I".
 *
 * The bands are computed from the actual positions of the nodes of a phase (`config.group`)
 * and not set fixed: if somebody moves a node, the band moves with it. Without group
 * entries nothing appears at all, so older and self-built flows look as before.
 */
export default function PhaseBands({ nodes }: { nodes: FlowNode[] }) {
  // One band per contiguous block of a phase, NOT one hull around everything. If a side
  // branch runs along beside another phase, two boxes lying inside each other would
  // otherwise come into being, and one no longer sees what belongs to what.
  const fields = PHASES.flatMap(([key, label, rgb]) => {
    const parts = nodes.filter((n) => n.data.config.group === key);
    if (!parts.length) return [];
    const height = (n: FlowNode) => n.measured?.height ?? 88;
    const sorted = [...parts].sort((a, b) => a.position.y - b.position.y);
    const blocks: FlowNode[][] = [];
    for (const n of sorted) {
      const last = blocks[blocks.length - 1];
      const previous = last?.[last.length - 1];
      // Connect when the next node follows immediately (at most one line of distance).
      const connection = previous
        && n.position.y - (previous.position.y + height(previous)) < height(n) + 80;
      if (connection) last.push(n);
      else blocks.push([n]);
    }
    const air = 26;
    return blocks.map((block, i) => {
      const xs = block.map((n) => n.position.x);
      const right = Math.max(...block.map((n) => n.position.x + (n.measured?.width ?? 220)));
      const below = Math.max(...block.map((n) => n.position.y + height(n)));
      const above = Math.min(...block.map((n) => n.position.y));
      return {
        key: `${key}-${i}`, label, rgb, showLabel: i === 0,
        x: Math.min(...xs) - air, y: above - air - 20,
        w: right - Math.min(...xs) + air * 2, h: below - above + air * 2 + 20,
      };
    });
  });

  if (!fields.length) return null;
  return (
    <ViewportPortal>
      {fields.map((f) => (
        <div
          key={f.key}
          // Behind the nodes and transparent to clicks: it is only orientation.
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
          {f.showLabel && <span
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
