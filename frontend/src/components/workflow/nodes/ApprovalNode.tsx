import { BaseNode, type FlowNodeProps } from "./shared";
import { tr } from "../../../i18n";
import { assigneeLabel } from "../assignee";

// Catalog keys, not texts: the table comes into being while the module loads, a `tr()` here
// would freeze the language of the first render.
const GATE_LABEL: Record<string, string> = {
  ai_assign: "approval_node.ai_right",
  role: "approval_node.role",
  none: "approval.no_gate",
};

export default function ApprovalNode({ id, data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      nodeId={id}
      title={c.label || tr("approval_node.approval")}
      icon="✅"
      accent="border-t-emerald-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={[
        { id: "approved", label: "genehmigt", color: "!bg-green-500" },
        { id: "rejected", label: "abgelehnt", color: "!bg-red-500" },
      ]}
    >
      <div>Freigabe: {assigneeLabel(c.approvers)}</div>
      {c.gate && <div>{GATE_LABEL[c.gate] ? tr(GATE_LABEL[c.gate]) : c.gate}</div>}
    </BaseNode>
  );
}
