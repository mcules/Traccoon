import { useCallback, useEffect } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type NodeTypes,
  type EdgeTypes,
  type OnConnect,
  type OnNodesChange,
  type OnEdgesChange,
  useStore,
} from "@xyflow/react";
import type { WorkflowNodeType } from "./types";
import type { FlowNode } from "./nodes/shared";
import type { Edge } from "@xyflow/react";
import StartNode from "./nodes/StartNode";
import EndNode from "./nodes/EndNode";
import HumanTaskNode from "./nodes/HumanTaskNode";
import DecisionNode from "./nodes/DecisionNode";
import ApprovalNode from "./nodes/ApprovalNode";
import AutoActionNode from "./nodes/AutoActionNode";
import AgentTaskNode from "./nodes/AgentTaskNode";
import WaitEventNode from "./nodes/WaitEventNode";
import SubflowNode from "./nodes/SubflowNode";
import LoopNode from "./nodes/LoopNode";
import TimerNode from "./nodes/TimerNode";
import ConditionEdge from "./ConditionEdge";
import { CanvasModeProvider } from "./canvasMode";
import PhaseBands from "./PhaseBands";

// Stable references (not created anew in the render, because otherwise React Flow warns).
const nodeTypes: NodeTypes = {
  start: StartNode,
  end: EndNode,
  human_task: HumanTaskNode,
  decision: DecisionNode,
  approval: ApprovalNode,
  auto_action: AutoActionNode,
  agent_task: AgentTaskNode,
  wait_event: WaitEventNode,
  subflow: SubflowNode,
  loop: LoopNode,
  timer: TimerNode,
};
const edgeTypes: EdgeTypes = { condition: ConditionEdge };

export interface WorkflowCanvasProps {
  nodes: FlowNode[];
  edges: Edge[];
  readOnly?: boolean;
  onNodesChange?: OnNodesChange<FlowNode>;
  onEdgesChange?: OnEdgesChange;
  onConnect?: OnConnect;
  onNodeClick?: (id: string) => void;
  /** `edgeId` is set when the building block was dragged onto an existing connection;
   *  then it belongs in between, not beside it. */
  onDropNode?: (type: WorkflowNodeType, pos: { x: number; y: number }, edgeId?: string) => void;
  /** Show this point (canvas coordinates) at the top centre. `token` triggers it. */
  fokus?: { x: number; y: number; token: number };
}

function Inner(props: WorkflowCanvasProps) {
  const { nodes, edges, readOnly, onNodesChange, onEdgesChange, onConnect, onNodeClick,
          onDropNode, fokus } = props;
  const rf = useReactFlow();
  const hoehe = useStore((st) => st.height);

  // After arranging, the view should stand where the flow begins; otherwise one looks at an
  // arbitrary excerpt of the newly distributed cards after the click.
  useEffect(() => {
    if (!fokus || !hoehe) return;
    const zoom = rf.getZoom();
    const rand = 60;
    rf.setCenter(fokus.x, fokus.y - rand + hoehe / (2 * zoom), { zoom, duration: 400 });
    // Deliberately only on `token`: aiming at the same target again is a new wish.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fokus?.token]);

  const onDrop = useCallback(
    (ev: React.DragEvent) => {
      ev.preventDefault();
      const type = ev.dataTransfer.getData("application/reactflow") as WorkflowNodeType;
      if (!type || !onDropNode) return;
      const pos = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
      // Is there a connection under the pointer? React Flow draws it as an SVG path, and the
      // hit is most reliably read from the rendered element. The line is thin, which is why
      // the surroundings are checked as well (React Flow puts a wide, invisible path over it,
      // `.react-flow__edge-interaction`).
      let edgeId: string | undefined;
      for (const [dx, dy] of [[0, 0], [0, -6], [0, 6], [-6, 0], [6, 0]]) {
        const treffer = document.elementFromPoint(ev.clientX + dx, ev.clientY + dy);
        const kante = treffer?.closest?.(".react-flow__edge");
        if (kante) {
          edgeId = kante.getAttribute("data-id") || undefined;
          break;
        }
      }
      onDropNode(type, pos, edgeId);
    },
    [rf, onDropNode]
  );
  const onDragOver = useCallback((ev: React.DragEvent) => {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
  }, []);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={readOnly ? undefined : onNodesChange}
      onEdgesChange={readOnly ? undefined : onEdgesChange}
      onConnect={readOnly ? undefined : onConnect}
      onNodeClick={onNodeClick ? (_, n) => onNodeClick(n.id) : undefined}
      onDrop={readOnly ? undefined : onDrop}
      onDragOver={readOnly ? undefined : onDragOver}
      nodesDraggable={!readOnly}
      nodesConnectable={!readOnly}
      elementsSelectable
      // Delete AND backspace remove the selection (edge or node). React Flow ignores the
      // keys while typing in an input field.
      deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
      fitView
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{ type: "condition" }}
    >
      <PhaseBands nodes={nodes} />
      <Background className="!bg-surface" />
      <Controls />
      {/* Die Übersichtskarte deckte am Handy ein Viertel der Fläche zu — dort ist die
          Fläche selbst schon die Übersicht. */}
      <MiniMap pannable zoomable className="!bg-card !hidden md:!block" />
    </ReactFlow>
  );
}

/** React Flow canvas: with interaction in the editor, with `readOnly` in the runtime. */
export default function WorkflowCanvas(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasModeProvider value={!!props.readOnly}>
        <Inner {...props} />
      </CanvasModeProvider>
    </ReactFlowProvider>
  );
}
