import { BaseNode, type FlowNodeProps } from "./shared";
import { tr } from "../../../i18n";

const OUTCOME_LABEL: Record<string, string> = {
  completed: "outcome.completed",
  failed: "outcome.failed",
  cancelled: "outcome.cancelled",
};

export default function EndNode({ data, selected }: FlowNodeProps) {
  const o = data.config.outcome;
  return (
    <BaseNode
      title={data.config.label || "Ende"}
      icon="⏹"
      accent="border-t-slate-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={[]}
    >
      {o && <div>{tr("node.outcome")}: {OUTCOME_LABEL[o] ? tr(OUTCOME_LABEL[o]) : o}</div>}
    </BaseNode>
  );
}
