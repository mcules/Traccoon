import { BaseNode, type FlowNodeProps } from "./shared";
import { tr } from "../../../i18n";

export default function StartNode({ id, data, selected }: FlowNodeProps) {
  const t = data.config.trigger;
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Start"}
      icon={t?.event ? "⚡" : t?.kind === "webhook" ? "🌐" : "▶"}
      accent="border-t-green-500"
      selected={selected}
      runtimeState={data.runtimeState}
      hasTarget={false}
    >
      {/* The trigger belongs on the card — otherwise one cannot tell from a flow what starts
          it in the first place. */}
      <div>{t?.event ? t.event
        : t?.kind === "webhook" ? tr("start_config.call_from_outside_webhook")
        : tr("start.manual_start")}</div>
      {t?.project_id && <div>{tr("start_node.project_id_only", { id: t.project_id })}</div>}
      {t?.filter && <div>{tr("start_node.condition")}</div>}
      {(data.config.context_schema?.length ?? 0) > 0 && (
        <div>Kontext: {data.config.context_schema!.map((c) => c.key).join(", ")}</div>
      )}
    </BaseNode>
  );
}
