import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { NodeConfig, WorkflowNodeType } from "../types";

/** Laufzeit-Zustand eines Knotens für die Read-only-Instanz-Ansicht. */
export type RuntimeState = "done" | "active" | "pending" | "failed";

/** node.data im React-Flow-Graphen. `type` (Object-Literal!) — nicht als interface,
 *  sonst schlägt der `Record<string, unknown>`-Constraint von React-Flow v12 fehl. */
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
  color?: string; // Tailwind-bg-Klasse für den Punkt
}

const handleDot = "!h-2.5 !w-2.5 !border !border-card";

/** Gemeinsamer Rahmen für alle Custom-Nodes: Titelzeile, Konfig-Auszug, Handles. */
export function BaseNode({
  title,
  icon,
  accent,
  selected,
  runtimeState,
  hasTarget = true,
  sources = [{ id: "out" }],
  children,
}: {
  title: string;
  icon?: string;
  accent: string; // Tailwind-border-Klasse für die linke Akzentkante
  selected?: boolean;
  runtimeState?: RuntimeState;
  hasTarget?: boolean;
  sources?: SourceHandleDef[];
  children?: React.ReactNode;
}) {
  const rs = runtimeState ? RS[runtimeState] : null;
  const border = rs ? rs.ring : selected ? "border-brand" : "border-line";
  return (
    <div
      className={`relative min-w-[140px] max-w-[220px] border-l-4 ${accent} rounded-md border ${border} bg-card px-3 py-2 text-ink shadow-sm`}
    >
      {hasTarget && (
        <Handle type="target" position={Position.Left} className={`${handleDot} !bg-muted`} />
      )}
      <div className="flex items-center gap-1.5 text-xs font-medium">
        {icon && <span>{icon}</span>}
        <span className="truncate">{title}</span>
        {rs && <span className={`ml-auto ${rs.text}`}>{rs.icon}</span>}
      </div>
      {children && <div className="mt-1 space-y-0.5 text-[10px] text-muted">{children}</div>}

      {sources.map((s, i) => {
        const top = sources.length === 1 ? 0.5 : (i + 1) / (sources.length + 1);
        return (
          <div key={s.id}>
            <Handle
              type="source"
              id={s.id}
              position={Position.Right}
              style={{ top: `${top * 100}%` }}
              className={`${handleDot} ${s.color || "!bg-brand"}`}
            />
            {s.label && (
              <span
                className="pointer-events-none absolute right-1 -translate-y-1/2 text-[9px] text-muted"
                style={{ top: `${top * 100}%` }}
              >
                {s.label}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
