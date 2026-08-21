import { BaseNode, useSourceHandles, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

const ACTION_LABEL: Record<string, string> = {
  create_ticket: "action.create_ticket",
  notify: "action.notify",
  webhook: "action.webhook",
  http_request: "action.http_request",
  tool_call: "action.tool_call",
  set_context: "action.set_context",
  note_append: "action.note_append",
  metric_record: "action.metric_record",
  metric_read: "action.metric_read",
  set_board_status: "action.set_board_status",
  set_status: "action.set_status",
  set_field: "action.set_field",
  refresh_facts: "action.read_project_facts",
  assign_agent: "action.assign_agent",
  set_cap_baseline: "action.set_cap_baseline",
  split_tickets: "action.split_tickets",
  stop_agent: "action.stop_agent",
  start_testenv: "action.start_test_environment",
  stop_testenv: "action.stop_test_environment",
  accept_merge: "action.accept_merge",
  deploy: "action.deploy",
  comment: "action.comment",
  mail_classify: "action.mail_classify",
  spam_evaluate: "action.judge_spam",
  spam_card: "action.spam_card",
  spam_apply: "action.spam_apply",
  mail_assistant_task: "action.mail_assistant_task",
  mail_assistant_card: "action.mail_assistant_card",
  mail_assistant_run: "action.mail_assistant_run",
  assistant_task: "action.assistant_task",
  agent_run: "action.agent_run",
  script: "action.script",
  job_pause: "action.job_pause",
  document: "action.document",
  document_read: "action.document_read",
  answer: "action.answer",
  mail_attachment: "action.mail_attachment",
  mail_flag: "action.mark_mail_read_flagged",
  mail_move: "action.mail_move",
};

/** Actions that run asynchronously and name their exit after the result. */
const OUTCOMES: Record<string, SourceHandleDef[]> = {
  accept_merge: [
    { id: "merged", label: "gemerged", color: "!bg-green-500" },
    { id: "pr_open", label: tr("auto_action_node.pr_open"), color: "!bg-sky-500" },
    { id: "conflict", label: "Konflikt", color: "!bg-red-500" },
    { id: "out", label: "sonst" },
  ],
};

export default function AutoActionNode({ id, data, selected }: FlowNodeProps) {
  const a = data.config.action;
  // Without named exits, edges like "merged" or "conflict" would stay undrawn.
  const basis = OUTCOMES[a?.action ?? ""] ?? [{ id: "out" }];
  // The error exit had long existed in the engine but not in the picture, so whoever wanted
  // to wire it had no point to hang the edge on.
  const sources = useSourceHandles(id, [
    ...basis,
    { id: "error", label: tr("common.error"), color: "!bg-red-500" },
  ]);
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Aktion"}
      icon="⚙"
      accent="border-t-sky-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={sources}
    >
      <div>{a ? tr(ACTION_LABEL[a.action] || a.action) : tr("node.no_action")}</div>
    </BaseNode>
  );
}
