import { BaseNode, type FlowNodeProps } from "./shared";

export default function StartNode({ data, selected }: FlowNodeProps) {
  return (
    <BaseNode
      title={data.config.label || "Start"}
      icon="▶"
      accent="border-l-green-500"
      selected={selected}
      runtimeState={data.runtimeState}
      hasTarget={false}
    >
      {(data.config.context_schema?.length ?? 0) > 0 && (
        <div>Kontext: {data.config.context_schema!.map((c) => c.key).join(", ")}</div>
      )}
    </BaseNode>
  );
}
