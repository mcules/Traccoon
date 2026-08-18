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

// Stabile Referenzen (nicht im Render neu erzeugen — sonst React-Flow-Warnung).
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
  /** `edgeId` ist gesetzt, wenn der Baustein auf eine bestehende Verbindung gezogen wurde
   *  — dann gehört er dazwischen, nicht daneben. */
  onDropNode?: (type: WorkflowNodeType, pos: { x: number; y: number }, edgeId?: string) => void;
  /** Diesen Punkt (Flächen-Koordinaten) oben mittig zeigen. `token` löst aus. */
  fokus?: { x: number; y: number; token: number };
}

function Inner(props: WorkflowCanvasProps) {
  const { nodes, edges, readOnly, onNodesChange, onEdgesChange, onConnect, onNodeClick,
          onDropNode, fokus } = props;
  const rf = useReactFlow();
  const hoehe = useStore((st) => st.height);

  // Nach dem Anordnen soll der Blick dort stehen, wo der Ablauf beginnt — sonst schaut man
  // nach dem Klick auf einen beliebigen Ausschnitt der neu verteilten Karten.
  useEffect(() => {
    if (!fokus || !hoehe) return;
    const zoom = rf.getZoom();
    const rand = 60;
    rf.setCenter(fokus.x, fokus.y - rand + hoehe / (2 * zoom), { zoom, duration: 400 });
    // Absichtlich nur auf `token`: dasselbe Ziel erneut anzusteuern ist ein neuer Wunsch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fokus?.token]);

  const onDrop = useCallback(
    (ev: React.DragEvent) => {
      ev.preventDefault();
      const type = ev.dataTransfer.getData("application/reactflow") as WorkflowNodeType;
      if (!type || !onDropNode) return;
      const pos = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
      // Liegt unter dem Zeiger eine Verbindung? React Flow zeichnet sie als SVG-Pfad; der
      // Treffer lässt sich am zuverlässigsten am gerenderten Element ablesen. Die Linie ist
      // dünn, deshalb wird ringsum nachgeschaut (React Flow legt dafür einen breiten,
      // unsichtbaren Pfad darüber — `.react-flow__edge-interaction`).
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
      // Entf UND Rücktaste löschen die Auswahl (Kante oder Knoten). React Flow ignoriert
      // die Tasten, während in einem Eingabefeld getippt wird.
      deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
      fitView
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{ type: "condition" }}
    >
      <PhaseBands nodes={nodes} />
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
      <CanvasModeProvider value={!!props.readOnly}>
        <Inner {...props} />
      </CanvasModeProvider>
    </ReactFlowProvider>
  );
}
