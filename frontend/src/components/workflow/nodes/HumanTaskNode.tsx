import { BaseNode, type FlowNodeProps } from "./shared";
import { tr } from "../../../i18n";
import { assigneeLabel } from "../assignee";

export default function HumanTaskNode({ id, data, selected }: FlowNodeProps) {
  const c = data.config;
  return (
    <BaseNode
      nodeId={id}
      title={c.label || tr("human_task_node.task")}
      icon="🧑"
      accent="border-t-brand"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
    >
      <div>{tr("human_task.responsible")}: {assigneeLabel(c.assignee)}</div>
      {(c.form?.length ?? 0) > 0 && <div>{c.form!.length} Formularfeld(er)</div>}
      {c.handover && <div>{tr("human_task_node.handover_possible")}</div>}
    </BaseNode>
  );
}
