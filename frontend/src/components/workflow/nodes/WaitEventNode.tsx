import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

const EVENT_LABEL: Record<string, string> = {
  comment: "Kommentar",
  answer: "Antwort",
  manual: "manuell",
  any: "beliebig",
};

/**
 * Stopping point until something happens from outside: a comment on the ticket, the answer
 * to a question or a manual continue. Every accepted event can get an exit of its own;
 * without a matching edge it runs over "weiter".
 */
export default function WaitEventNode({ id, data, selected }: FlowNodeProps) {
  const events = data.config.events?.length ? data.config.events : ["comment", "manual"];
  const sources: SourceHandleDef[] =
    events.length > 1 ? [{ id: "out", label: "weiter" }] : [{ id: "out" }];
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
      <div>{events.map((e) => EVENT_LABEL[e] || e).join(" · ")}</div>
    </BaseNode>
  );
}
