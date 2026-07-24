import { BaseNode, type FlowNodeProps } from "./shared";

const OUTCOME_LABEL: Record<string, string> = {
  completed: "abgeschlossen",
  failed: "fehlgeschlagen",
  cancelled: "abgebrochen",
};

export default function EndNode({ data, selected }: FlowNodeProps) {
  const o = data.config.outcome;
  return (
    <BaseNode
      title={data.config.label || "Ende"}
      icon="⏹"
      accent="border-l-slate-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={[]}
    >
      {o && <div>Ergebnis: {OUTCOME_LABEL[o] || o}</div>}
    </BaseNode>
  );
}
