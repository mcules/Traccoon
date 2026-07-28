import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";

const EVENT_LABEL: Record<string, string> = {
  comment: "Kommentar",
  answer: "Antwort",
  manual: "manuell",
  any: "beliebig",
};

/**
 * Haltepunkt, bis von außen etwas passiert — ein Kommentar am Ticket, die Antwort auf eine
 * Rückfrage oder ein manuelles Weiter. Jedes angenommene Ereignis kann einen eigenen
 * Ausgang bekommen; ohne passende Kante läuft es über „weiter".
 */
export default function WaitEventNode({ id, data, selected }: FlowNodeProps) {
  const events = data.config.events?.length ? data.config.events : ["comment", "manual"];
  const sources: SourceHandleDef[] =
    events.length > 1 ? [{ id: "out", label: "weiter" }] : [{ id: "out" }];
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Warten auf Ereignis"}
      icon="⏳"
      accent="border-t-cyan-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={sources}
    >
      <div>{events.map((e) => EVENT_LABEL[e] || e).join(" · ")}</div>
    </BaseNode>
  );
}
