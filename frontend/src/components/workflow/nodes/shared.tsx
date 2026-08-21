import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { tr } from "../../../i18n";
import type { NodeConfig, WorkflowNodeType } from "../types";

/** Runtime state of a node for the read-only instance view. */
export type RuntimeState = "done" | "active" | "pending" | "failed";

/** node.data in the React Flow graph. A `type` (object literal), not an interface, because
 *  otherwise the `Record<string, unknown>` constraint of React Flow v12 fails. */
export type FlowNodeData = {
  config: NodeConfig;
  runtimeState?: RuntimeState;
};

export type FlowNode = Node<FlowNodeData, WorkflowNodeType>;
export type FlowNodeProps = NodeProps<FlowNode>;

const RS: Record<RuntimeState, { icon: string; ring: string; text: string }> = {
  done: { icon: "✓", ring: "border-green-500", text: "text-green-400" },
  active: { icon: "◉", ring: "border-yellow-500", text: "text-yellow-400" },
  pending: { icon: "○", ring: "border-line", text: "text-muted" },
  failed: { icon: "✕", ring: "border-red-500", text: "text-red-400" },
};

export interface SourceHandleDef {
  id: string;
  label?: string;
  color?: string; // Tailwind bg class for the dot
}

const handleDot = "!h-2.5 !w-2.5 !border !border-card";

/**
 * Exits a node has to show.
 *
 * What matters is the second source: React Flow draws an edge ONLY when the named exit
 * really exists on the node. If it is missing, the connection disappears without a word and
 * the target node then looks as if it hung in the air. That is why all exits existing edges
 * actually use are added here as well.
 */
export function useSourceHandles(nodeId: string, fallbackValue: SourceHandleDef[]): SourceHandleDef[] {
  const used = useStore((st) => {
    const names = new Set<string>();
    for (const e of st.edges) if (e.source === nodeId) names.add(e.sourceHandle || "out");
    return [...names].sort().join("\u0000");
  });
  const extra = used ? used.split("\u0000") : [];
  const out = [...fallbackValue];
  for (const name of extra) {
    if (!out.some((h) => h.id === name)) out.push({ id: name, label: name });
  }
  return out.length ? out : [{ id: "out" }];
}

/** Shared frame for all custom nodes: title line, config excerpt, handles. */
export function BaseNode({
  nodeId,
  title,
  icon,
  accent,
  selected,
  runtimeState,
  from,
  hasTarget = true,
  sources = [{ id: "out" }],
  children,
}: {
  /** Without the id, missing exits cannot be added (see useSourceHandles). */
  nodeId?: string;
  title: string;
  icon?: string;
  accent: string; // Tailwind border class for the accent edge at the top
  selected?: boolean;
  runtimeState?: RuntimeState;
  /** Switched off step: visibly pale, so that it is not overlooked in the graph. */
  from?: boolean;
  hasTarget?: boolean;
  sources?: SourceHandleDef[];
  children?: React.ReactNode;
}) {
  const rs = runtimeState ? RS[runtimeState] : null;
  const border = rs ? rs.ring : selected ? "border-brand" : "border-line";
  // Every exit an existing edge uses MUST be drawn; otherwise React Flow swallows the edge
  // and the target node seemingly hangs in the air. Central here, so that it applies to
  // every node type (self-built graphs as well).
  const all = useSourceHandles(nodeId ?? "", sources);
  sources = sources.length ? all : sources;
  // The flow runs from top to bottom: entry at the top, exits side by side at the bottom.
  const labeled = sources.filter((s) => s.label).length;
  return (
    <div
      className={`relative max-w-[280px] border-t-4 ${accent} rounded-md border ${border} bg-card px-3 py-2 text-ink shadow-sm ${
        labeled ? "pb-5" : ""
      } ${from ? "opacity-60 [border-style:dashed]" : ""}`}
      style={{ minWidth: Math.max(160, labeled * 92) }}
    >
      {hasTarget && (
        <Handle type="target" position={Position.Top} className={`${handleDot} !bg-muted`} />
      )}
      <div className="flex items-center gap-1.5 text-xs font-medium">
        {icon && <span>{icon}</span>}
        <span className="truncate">{title}</span>
        {from && <span className="ml-auto text-[9px] uppercase text-amber-300">aus</span>}
        {rs && <span className={`ml-auto ${rs.text}`}>{rs.icon}</span>}
      </div>
      {children && <div className="mt-1 space-y-0.5 text-[11px] text-muted">{children}</div>}

      {sources.map((s, i) => {
        const left = sources.length === 1 ? 0.5 : (i + 1) / (sources.length + 1);
        return (
          <div key={s.id}>
            <Handle
              type="source"
              id={s.id}
              position={Position.Bottom}
              style={{ left: `${left * 100}%` }}
              className={`${handleDot} ${s.color || "!bg-brand"}`}
            />
            {s.label && (
              <span
                className="pointer-events-none absolute bottom-0.5 max-w-[88px] -translate-x-1/2 truncate text-center text-[9px] text-muted"
                style={{ left: `${left * 100}%` }}
              >
                {/* A catalog key when it comes from a table in the code, a text when it comes
                    out of the flow: `tr` hands an unknown key back unchanged. */}
                {tr(s.label)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
