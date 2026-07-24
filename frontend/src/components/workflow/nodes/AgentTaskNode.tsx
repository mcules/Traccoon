import { BaseNode, type FlowNodeProps } from "./shared";

const PHASE_LABEL: Record<string, string> = {
  planning: "Planung",
  execution: "Ausführung",
};

export default function AgentTaskNode({ data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      title={c.label || "KI-Agent"}
      icon="🤖"
      accent="border-l-purple-500"
      selected={selected}
      runtimeState={data.runtimeState}
    >
      <div>Agent: {c.agent_role || "—"}</div>
      {c.phase && <div>Phase: {PHASE_LABEL[c.phase] || c.phase}</div>}
    </BaseNode>
  );
}
