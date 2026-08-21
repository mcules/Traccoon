import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

export default function DecisionNode({ id, data, selected }: FlowNodeProps) {
  const branches = data.config.branches || [];
  const sources: SourceHandleDef[] = branches.length
    ? branches.map((b) => ({
        id: b.handle,
        label: b.label || b.handle,
        color: b.handle === data.config.default_handle ? "!bg-slate-400" : "!bg-brand",
      }))
    : [{ id: "out" }];
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Verzweigung"}
      icon="◈"
      accent="border-t-amber-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={sources}
    >
      {branches.length === 0 && <div>{tr("decision_node.no_branches_defined")}</div>}
    </BaseNode>
  );
}
