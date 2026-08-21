import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";
import { SLOT_LABELS } from "../types";

/**
 * Calls another flow and waits for it. Which graph that concretely is is decided by the
 * resolution of the project (own adjustment, then set, then default), so an adjusted
 * acceptance process takes effect everywhere it is called.
 */
export default function SubflowNode({ id, data, selected }: FlowNodeProps) {
  const sources: SourceHandleDef[] = [
    { id: "completed", label: "fertig", color: "!bg-green-500" },
    { id: "failed", label: "gescheitert", color: "!bg-red-500" },
  ];
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || tr("subflow_node.other_flow")}
      icon="🔗"
      accent="border-t-indigo-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={sources}
    >
      <div>{data.config.slot ? tr(SLOT_LABELS[data.config.slot]) : tr("node.no_flow_chosen")}</div>
    </BaseNode>
  );
}
