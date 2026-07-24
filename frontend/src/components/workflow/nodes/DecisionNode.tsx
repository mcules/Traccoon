import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";

export default function DecisionNode({ data, selected }: FlowNodeProps) {
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
      title={data.config.label || "Verzweigung"}
      icon="◈"
      accent="border-l-amber-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={sources}
    >
      {branches.length === 0 && <div>keine Zweige definiert</div>}
    </BaseNode>
  );
}
