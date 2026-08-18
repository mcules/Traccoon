import { BaseNode, useSourceHandles, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

const ACTION_LABEL: Record<string, string> = {
  create_ticket: "action.create_ticket",
  notify: "action.notify",
  webhook: "action.webhook",
  http_request: "action.http_request",
  tool_call: "action.tool_call",
  set_context: "action.set_context",
  messwert: "action.messwert",
  messreihe_lesen: "action.messreihe_lesen",
  set_board_status: "action.set_board_status",
  set_status: "action.set_status",
  set_field: "action.set_field",
  refresh_facts: "action.refresh_facts",
  assign_agent: "action.assign_agent",
  set_cap_baseline: "action.set_cap_baseline",
  split_tickets: "action.split_tickets",
  stop_agent: "action.stop_agent",
  start_testenv: "action.start_testenv",
  stop_testenv: "action.stop_testenv",
  accept_merge: "action.accept_merge",
  deploy: "action.deploy",
  comment: "action.comment",
  mail_classify: "action.mail_classify",
  spam_evaluate: "action.spam_evaluate",
  spam_card: "action.spam_card",
  spam_apply: "action.spam_apply",
  assistant_task: "action.assistant_task",
  assistant_card: "action.assistant_card",
  assistant_run: "action.assistant_run",
};

/** Aktionen, die asynchron laufen und ihren Ausgang nach dem Ergebnis benennen. */
const OUTCOMES: Record<string, SourceHandleDef[]> = {
  accept_merge: [
    { id: "merged", label: "gemerged", color: "!bg-green-500" },
    { id: "pr_open", label: "PR offen", color: "!bg-sky-500" },
    { id: "conflict", label: "Konflikt", color: "!bg-red-500" },
    { id: "out", label: "sonst" },
  ],
};

export default function AutoActionNode({ id, data, selected }: FlowNodeProps) {
  const a = data.config.action;
  // Ohne benannte Ausgänge blieben Kanten wie „merged" oder „conflict" ungezeichnet.
  const basis = OUTCOMES[a?.action ?? ""] ?? [{ id: "out" }];
  // Der Fehlerausgang existierte in der Engine längst, im Bild aber nicht — wer ihn
  // verdrahten wollte, hatte keinen Punkt, an den er die Kante hängen konnte.
  const sources = useSourceHandles(id, [
    ...basis,
    { id: "error", label: "Fehler", color: "!bg-red-500" },
  ]);
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Aktion"}
      icon="⚙"
      accent="border-t-sky-500"
      selected={selected}
      runtimeState={data.runtimeState}
      aus={!!data.config.deaktiviert}
      sources={sources}
    >
      <div>{a ? tr(ACTION_LABEL[a.action] || a.action) : tr("node.keine_aktion")}</div>
    </BaseNode>
  );
}
