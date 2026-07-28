import { BaseNode, type FlowNodeProps } from "./shared";
import { assigneeLabel } from "../assignee";

export default function HumanTaskNode({ id, data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      nodeId={id}
      title={c.label || "Aufgabe"}
      icon="🧑"
      accent="border-t-brand"
      selected={selected}
      runtimeState={data.runtimeState}
    >
      <div>Zuständig: {assigneeLabel(c.assignee)}</div>
      {(c.form?.length ?? 0) > 0 && <div>{c.form!.length} Formularfeld(er)</div>}
      {c.handover && <div>↪ Übergabe möglich</div>}
    </BaseNode>
  );
}
