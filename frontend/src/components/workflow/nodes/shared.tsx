import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
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

/**
 * Ausgänge, die ein Knoten zeigen muss.
 *
 * Entscheidend ist die zweite Quelle: React Flow zeichnet eine Kante NUR, wenn es den
 * benannten Ausgang am Knoten wirklich gibt. Fehlt er, verschwindet die Verbindung
 * kommentarlos — der Zielknoten sieht dann aus, als hinge er in der Luft. Deshalb werden
 * hier auch alle Ausgänge ergänzt, die vorhandene Kanten tatsächlich benutzen.
 */
export function useSourceHandles(nodeId: string, vorgabe: SourceHandleDef[]): SourceHandleDef[] {
  const genutzt = useStore((st) => {
    const namen = new Set<string>();
    for (const e of st.edges) if (e.source === nodeId) namen.add(e.sourceHandle || "out");
    return [...namen].sort().join("\u0000");
  });
  const zusatz = genutzt ? genutzt.split("\u0000") : [];
  const out = [...vorgabe];
  for (const name of zusatz) {
    if (!out.some((h) => h.id === name)) out.push({ id: name, label: name });
  }
  return out.length ? out : [{ id: "out" }];
}

/** Gemeinsamer Rahmen für alle Custom-Nodes: Titelzeile, Konfig-Auszug, Handles. */
export function BaseNode({
  nodeId,
  title,
  icon,
  accent,
  selected,
  runtimeState,
  aus,
  hasTarget = true,
  sources = [{ id: "out" }],
  children,
}: {
  /** Ohne die Kennung können fehlende Ausgänge nicht ergänzt werden (s. useSourceHandles). */
  nodeId?: string;
  title: string;
  icon?: string;
  accent: string; // Tailwind-border-Klasse für die obere Akzentkante
  selected?: boolean;
  runtimeState?: RuntimeState;
  /** Abgeschalteter Schritt: sichtbar blass, damit man ihn im Graphen nicht übersieht. */
  aus?: boolean;
  hasTarget?: boolean;
  sources?: SourceHandleDef[];
  children?: React.ReactNode;
}) {
  const rs = runtimeState ? RS[runtimeState] : null;
  const border = rs ? rs.ring : selected ? "border-brand" : "border-line";
  // Jeder Ausgang, den eine vorhandene Kante benutzt, MUSS gezeichnet werden — sonst
  // verschluckt React Flow die Kante und der Zielknoten hängt scheinbar in der Luft.
  // Zentral hier, damit das für jeden Knotentyp gilt (auch selbstgebaute Graphen).
  const alle = useSourceHandles(nodeId ?? "", sources);
  sources = sources.length ? alle : sources;
  // Fluss läuft von oben nach unten: Eingang oben, Ausgänge nebeneinander unten.
  const labeled = sources.filter((s) => s.label).length;
  return (
    <div
      className={`relative max-w-[280px] border-t-4 ${accent} rounded-md border ${border} bg-card px-3 py-2 text-ink shadow-sm ${
        labeled ? "pb-5" : ""
      } ${aus ? "opacity-60 [border-style:dashed]" : ""}`}
      style={{ minWidth: Math.max(160, labeled * 92) }}
    >
      {hasTarget && (
        <Handle type="target" position={Position.Top} className={`${handleDot} !bg-muted`} />
      )}
      <div className="flex items-center gap-1.5 text-xs font-medium">
        {icon && <span>{icon}</span>}
        <span className="truncate">{title}</span>
        {aus && <span className="ml-auto text-[9px] uppercase text-amber-300">aus</span>}
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
                {s.label}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
