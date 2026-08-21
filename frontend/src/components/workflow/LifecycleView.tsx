import { useMemo } from "react";
import { tr } from "../../i18n";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

/**
 * Read-only visualisation of the AI agent lifecycle.
 *
 * PURELY ADDITIVE: the graph is hard coded (fixed nodes and positions), and the
 * highlighting comes exclusively from `agentStatus` (plus `holdReason`). No engine instance,
 * no editor, only display. The style follows
 * WorkflowInstanceView/shared.tsx (the ◉/✓/○ language, bg-card/border-line).
 *
 * Assumptions:
 *  - `testing` is highlighted on the `to_test` node (review, acceptance).
 *  - On `hold`/`failed` all main nodes up to and including `in_progress` count as "done"
 *    (the flow has passed through them), so that the progress stays visible; the branching
 *    node itself is "active".
 *  - `agentStatus == null` means nothing is highlighted (a discreet hint at the edge),
 *    and the embedding then usually hides the section entirely.
 */

type NodeId =
  | "open" | "planning" | "plan_review" | "approved"
  | "in_progress" | "to_test" | "done" | "hold" | "failed";

type Visual = "done" | "active" | "pending";
type Kind = "normal" | "done" | "failed" | "hold";

// Keys instead of texts, because the table comes into being while the module loads.
const NODE_LABEL: Record<NodeId, string> = {
  open: "lifecycle.open",
  planning: "lifecycle.planning",
  plan_review: "lifecycle.plan_review",
  approved: "lifecycle.approved",
  in_progress: "lifecycle.in_progress",
  to_test: "lifecycle.to_test",
  done: "lifecycle.done",
  hold: "lifecycle.hold",
  failed: "lifecycle.failed",
};

/** hold_reason as plain text, shown on the blocked node. */
const HOLD_REASON: Record<string, string> = {
  plan_review: "lifecycle.plan_approval",
  plan_split: "lifecycle.split",
  question: "lifecycle.question",
  merge: "lifecycle.merge_conflict",
  verify: "lifecycle.verification",
  review: "lifecycle.review_findings",
  incomplete: "lifecycle.incomplete",
  stuck: "lifecycle.stuck",
  cap: "lifecycle.limit_reached",
  interrupted: "lifecycle.interrupted",
  permission: "lifecycle.permission",
};

// Hauptfluss (links → rechts) — Reihenfolge bestimmt „erledigte Vorgänger".
const MAIN: NodeId[] = [
  "open", "planning", "plan_review", "approved", "in_progress", "to_test", "done",
];

// Fixed positions (compact LR layout; fitView scales it into a height of about 240px).
const X = 168; // horizontaler Rasterabstand
const POS: Record<NodeId, { x: number; y: number }> = {
  open: { x: 0 * X, y: 20 },
  planning: { x: 1 * X, y: 20 },
  plan_review: { x: 2 * X, y: 20 },
  approved: { x: 3 * X, y: 20 },
  in_progress: { x: 4 * X, y: 20 },
  to_test: { x: 5 * X, y: 20 },
  done: { x: 6 * X, y: 20 },
  hold: { x: 4 * X - 24, y: 150 },
  failed: { x: 5 * X - 24, y: 150 },
};

const KIND: Record<NodeId, Kind> = {
  open: "normal", planning: "normal", plan_review: "normal", approved: "normal",
  in_progress: "normal", to_test: "normal", done: "done", hold: "hold", failed: "failed",
};

/** Derives the display state per node from the current agent_status. */
function computeVisuals(agentStatus: string | null): Record<NodeId, Visual> {
  const out = {} as Record<NodeId, Visual>;
  (Object.keys(NODE_LABEL) as NodeId[]).forEach((n) => (out[n] = "pending"));
  if (!agentStatus) return out;

  // testing becomes to_test; otherwise the node of the same name.
  const current = (agentStatus === "testing" ? "to_test" : agentStatus) as NodeId;

  const idx = MAIN.indexOf(current);
  if (idx >= 0) {
    for (let i = 0; i < idx; i++) out[MAIN[i]] = "done";
    out[MAIN[idx]] = "active";
  } else if (current === "hold" || current === "failed") {
    (["open", "planning", "plan_review", "approved", "in_progress"] as NodeId[])
      .forEach((n) => (out[n] = "done"));
    out[current] = "active";
  }
  return out;
}

type LifecycleNodeData = {
  label: string;
  visual: Visual;
  kind: Kind;
  working: boolean;
  sub?: string;
};

const handleDot = "!h-1.5 !w-1.5 !border-0 !bg-transparent";

/** Kompakter Zustands-Knoten — eigene Optik, konsistent zu shared.tsx-Tokens. */
function LifecycleNode({ data }: NodeProps<Node<LifecycleNodeData>>) {
  const { label, visual, kind, working, sub } = data;

  let box = "border-line bg-card text-muted";
  let icon = "○";
  if (visual === "done") {
    box = "border-green-500/60 bg-card text-green-400";
    icon = "✓";
  } else if (visual === "active") {
    if (kind === "failed") { box = "border-red-500 bg-red-500/10 text-red-400"; icon = "✕"; }
    else if (kind === "hold") { box = "border-yellow-500 bg-yellow-500/10 text-yellow-400"; icon = "⏸"; }
    else if (kind === "done") { box = "border-green-500 bg-green-500/10 text-green-400"; icon = "✓"; }
    else { box = "border-brand bg-brand/10 text-brand"; icon = "◉"; }
  }
  const pulse = working && visual === "active" ? "animate-pulse ring-2 ring-brand/40" : "";

  return (
    <div className={`relative min-w-[112px] rounded-md border px-3 py-2 shadow-sm ${box} ${pulse}`}>
      {/* Verdeckte Handles für die fest verdrahteten Kanten (read-only). */}
      <Handle type="target" id="t-left" position={Position.Left} className={handleDot} isConnectable={false} />
      <Handle type="target" id="t-top" position={Position.Top} className={handleDot} isConnectable={false} />
      <Handle type="target" id="t-bottom" position={Position.Bottom} className={handleDot} isConnectable={false} />
      <Handle type="source" id="s-right" position={Position.Right} className={handleDot} isConnectable={false} />
      <Handle type="source" id="s-bottom" position={Position.Bottom} className={handleDot} isConnectable={false} />
      <Handle type="source" id="s-top" position={Position.Top} className={handleDot} isConnectable={false} />

      <div className="flex items-center gap-1.5 text-xs font-medium">
        <span>{icon}</span>
        <span className="truncate">{label}</span>
      </div>
      {sub && <div className="mt-0.5 text-[11px] leading-tight opacity-90">{sub}</div>}
    </div>
  );
}

// Stable reference (not created anew in the render, because otherwise React Flow warns).
const nodeTypes: NodeTypes = { lifecycle: LifecycleNode };

type EdgeDef = {
  from: NodeId; to: NodeId; sh: string; th: string; label?: string; tone?: "muted" | "hold" | "fail";
};
const EDGES: EdgeDef[] = [
  { from: "open", to: "planning", sh: "s-right", th: "t-left" },
  { from: "planning", to: "plan_review", sh: "s-right", th: "t-left" },
  { from: "plan_review", to: "approved", sh: "s-right", th: "t-left" },
  { from: "approved", to: "in_progress", sh: "s-right", th: "t-left" },
  { from: "in_progress", to: "to_test", sh: "s-right", th: "t-left" },
  { from: "to_test", to: "done", sh: "s-right", th: "t-left" },
  // Nebenkanten
  { from: "plan_review", to: "planning", sh: "s-bottom", th: "t-bottom", label: "lifecycle.rejected", tone: "muted" },
  { from: "in_progress", to: "hold", sh: "s-bottom", th: "t-top", label: "lifecycle.blocked_question", tone: "hold" },
  { from: "in_progress", to: "failed", sh: "s-bottom", th: "t-top", label: "lifecycle.failed_2", tone: "fail" },
  { from: "hold", to: "in_progress", sh: "s-top", th: "t-bottom", label: "lifecycle.resumed", tone: "muted" },
];

const TONE_COLOR: Record<string, string> = {
  muted: "rgb(139 148 158)",
  hold: "rgb(234 179 8)",
  fail: "rgb(239 68 68)",
};

export default function LifecycleView({
  agentStatus,
  holdReason,
  agentWorking = false,
  height = "240px",
}: {
  agentStatus: string | null;
  holdReason: string | null;
  agentWorking?: boolean;
  height?: string;
}) {
  const { nodes, edges } = useMemo(() => {
    const visuals = computeVisuals(agentStatus);
    const holdSub =
      agentStatus === "hold" && holdReason
        ? tr("lifecycle.reason_reason", { reason: HOLD_REASON[holdReason] ? tr(HOLD_REASON[holdReason]) : holdReason })
        : undefined;

    const nodes: Node<LifecycleNodeData>[] = (Object.keys(NODE_LABEL) as NodeId[]).map((id) => ({
      id,
      type: "lifecycle",
      position: POS[id],
      data: {
        label: tr(NODE_LABEL[id]),
        visual: visuals[id],
        kind: KIND[id],
        working: agentWorking && visuals[id] === "active",
        sub: id === "hold" ? holdSub : undefined,
      },
      draggable: false,
      selectable: false,
    }));

    const edges: Edge[] = EDGES.map((e, i) => {
      const active =
        visuals[e.from] === "active" || visuals[e.to] === "active" ||
        (visuals[e.from] === "done" && visuals[e.to] === "done");
      const color = e.tone ? TONE_COLOR[e.tone] : undefined;
      return {
        id: `e${i}`,
        source: e.from,
        target: e.to,
        sourceHandle: e.sh,
        targetHandle: e.th,
        label: e.label ? tr(e.label) : e.label,
        animated: active && !e.tone,
        style: { stroke: color || "rgb(72 80 92)", strokeWidth: active ? 1.6 : 1 },
        labelStyle: { fill: "rgb(139 148 158)", fontSize: 9 },
        labelBgStyle: { fill: "rgb(var(--card))" },
        labelBgPadding: [3, 1] as [number, number],
        markerEnd: { type: MarkerType.ArrowClosed, color: color || "rgb(72 80 92)" },
      };
    });
    return { nodes, edges };
  }, [agentStatus, holdReason, agentWorking]);

  if (!agentStatus) {
    return <div className="text-xs text-muted">{tr("lifecycle_view.no_ai_run")}</div>;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-line" style={{ height }}>
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag={true}
          zoomOnScroll={true}
          zoomOnPinch={true}
          zoomOnDoubleClick={false}
          minZoom={0.4}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background className="!bg-surface" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
