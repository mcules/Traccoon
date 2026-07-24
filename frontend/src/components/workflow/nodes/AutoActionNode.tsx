import { BaseNode, type FlowNodeProps } from "./shared";

const ACTION_LABEL: Record<string, string> = {
  create_ticket: "Ticket anlegen",
  notify: "Benachrichtigen",
  webhook: "Webhook",
  set_context: "Kontext setzen",
  set_board_status: "Board-Status setzen",
};

export default function AutoActionNode({ data, selected }: FlowNodeProps) {
  const a = data.config.action;
  return (
    <BaseNode
      title={data.config.label || "Aktion"}
      icon="⚙"
      accent="border-l-sky-500"
      selected={selected}
      runtimeState={data.runtimeState}
    >
      <div>{a ? ACTION_LABEL[a.action] || a.action : "keine Aktion"}</div>
    </BaseNode>
  );
}
