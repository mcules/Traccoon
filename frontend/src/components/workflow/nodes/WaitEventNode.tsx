import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

// Catalog keys, not texts: the table comes into being while the module loads, a `tr()`
// here would freeze the language of the first render.
const EVENT_LABEL: Record<string, string> = {
  comment: "wait_event_node.comment",
  answer: "wait_event_node.answer",
  manual: "wait_event_node.manual",
  any: "wait_event_node.any",
};

/**
 * Stopping point until something happens from outside: a comment on the ticket, the answer
 * to a question or a manual continue. Every accepted event can get an exit of its own;
 * without a matching edge it runs over the "resume" exit.
 */
export default function WaitEventNode({ id, data, selected }: FlowNodeProps) {
  const events = data.config.events?.length ? data.config.events : ["comment", "manual"];
  const sources: SourceHandleDef[] =
    events.length > 1 ? [{ id: "out", label: tr("wait_event_node.resume") }] : [{ id: "out" }];
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || tr("wait_event_node.wait_event")}
      icon="⏳"
      accent="border-t-cyan-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={sources}
    >
      <div>{events.map((e) => (EVENT_LABEL[e] ? tr(EVENT_LABEL[e]) : e)).join(" · ")}</div>
    </BaseNode>
  );
}
