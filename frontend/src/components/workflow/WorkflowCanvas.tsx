import { useCallback } from "react";
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
import ConditionEdge from "./ConditionEdge";

// Stabile Referenzen (nicht im Render neu erzeugen — sonst React-Flow-Warnung).
const nodeTypes: NodeTypes = {
  start: StartNode,
  end: EndNode,
  human_task: HumanTaskNode,
  decision: DecisionNode,
  approval: ApprovalNode,
  auto_action: AutoActionNode,
  agent_task: AgentTaskNode,
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
  onDropNode?: (type: WorkflowNodeType, pos: { x: number; y: number }) => void;
}

function Inner(props: WorkflowCanvasProps) {
  const { nodes, edges, readOnly, onNodesChange, onEdgesChange, onConnect, onNodeClick, onDropNode } = props;
  const rf = useReactFlow();

  const onDrop = useCallback(
    (ev: React.DragEvent) => {
      ev.preventDefault();
      const type = ev.dataTransfer.getData("application/reactflow") as WorkflowNodeType;
      if (!type || !onDropNode) return;
      const pos = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
      onDropNode(type, pos);
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
      fitView
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{ type: "condition" }}
    >
      <Background className="!bg-surface" />
      <Controls />
      <MiniMap pannable zoomable className="!bg-card" />
    </ReactFlow>
  );
}

/** React-Flow-Canvas — im Editor mit Interaktion, in der Runtime mit `readOnly`. */
export default function WorkflowCanvas(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
