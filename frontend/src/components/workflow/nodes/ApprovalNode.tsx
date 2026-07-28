import { BaseNode, type FlowNodeProps } from "./shared";
import { assigneeLabel } from "../assignee";

const GATE_LABEL: Record<string, string> = {
  ai_assign: "KI-Recht (ai_assign)",
  role: "Rolle",
  none: "ohne Gate",
};

export default function ApprovalNode({ id, data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      nodeId={id}
      title={c.label || "Freigabe"}
      icon="✅"
      accent="border-t-emerald-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={[
        { id: "approved", label: "genehmigt", color: "!bg-green-500" },
        { id: "rejected", label: "abgelehnt", color: "!bg-red-500" },
      ]}
    >
      <div>Freigabe: {assigneeLabel(c.approvers)}</div>
      {c.gate && <div>{GATE_LABEL[c.gate] || c.gate}</div>}
    </BaseNode>
  );
}
