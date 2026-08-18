import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

const PHASE_LABEL: Record<string, string> = {
  planning: "Planung",
  execution: "agent.rolle.ausfuehrung",
};

const ROLE_LABEL: Record<string, string> = {
  plan_agent: "agent_task_node.plan_agent",
  exec_agent: "agent.rolle.exec",
  review_agent: "agent.rolle.review",
  assigned: "agent_task_node.assigned",
};

/** Ausgänge = mögliche Ergebnisse eines Laufs. Nicht gezeichnete Ausgänge fallen auf
 *  „weiter" zurück, damit einfache Graphen mit einer Kante auskommen. */
function outcomes(phase?: string): SourceHandleDef[] {
  const erst = phase === "planning"
    ? { id: "planned", label: "Plan da", color: "!bg-green-500" }
    : { id: "done", label: "fertig", color: "!bg-green-500" };
  return [
    erst,
    { id: "loop_exhausted", label: "Zwischenstand", color: "!bg-amber-500" },
    { id: "blocked", label: tr("agent.ausgang.rueckfrage"), color: "!bg-yellow-500" },
    { id: "failed", label: "Fehler", color: "!bg-red-500" },
    // Auffangnetz: unbekannte Ergebnisse landen laut Standard-Abbildung auf „err".
    { id: "err", label: "sonstiges", color: "!bg-red-500" },
  ];
}

export default function AgentTaskNode({ id, data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      nodeId={id}
      title={c.label || "KI-Agent"}
      icon="🤖"
      accent="border-t-purple-500"
      selected={selected}
      runtimeState={data.runtimeState}
      aus={!!data.config.deaktiviert}
      sources={outcomes(c.phase)}
    >
      <div>Agent: {c.agent_role ? ROLE_LABEL[c.agent_role] || c.agent_role : "—"}</div>
      {c.phase && <div>Phase: {PHASE_LABEL[c.phase] || c.phase}</div>}
    </BaseNode>
  );
}
