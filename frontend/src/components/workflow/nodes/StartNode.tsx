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
      {/* Der Auslöser gehört auf die Karte — sonst sieht man einem Ablauf nicht an,
          wodurch er überhaupt startet. */}
      <div>{t?.event ? t.event
        : t?.kind === "webhook" ? tr("start_config.aufruf_von_aussen_webhook")
        : tr("start.manueller_start")}</div>
      {t?.project_id && <div>{tr("start_node.nur_projekt", { id: t.project_id })}</div>}
      {t?.filter && <div>{tr("start_node.mit_bedingung")}</div>}
      {(data.config.context_schema?.length ?? 0) > 0 && (
        <div>Kontext: {data.config.context_schema!.map((c) => c.key).join(", ")}</div>
      )}
    </BaseNode>
  );
}
