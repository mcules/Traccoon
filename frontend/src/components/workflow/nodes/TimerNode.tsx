import { BaseNode, type FlowNodeProps } from "./shared";

/**
 * Lässt Zeit vergehen — die zweite Art zu warten.
 *
 * `wait_event` wartet auf jemanden, der etwas meldet; hier wartet der Ablauf auf die Uhr.
 * Geweckt wird im Takt der Engine, nicht von einem schlafenden Task: ein Neustart des
 * Backends darf einen wartenden Lauf nicht vergessen.
 */
export default function TimerNode({ id, data, selected }: FlowNodeProps) {
  const cfg = data.config;
  const text = cfg.bis
    ? `bis ${cfg.bis}`
    : `${cfg.dauer ?? "?"} ${{ s: "Sek.", m: "Min.", h: "Std.", t: "Tage" }[
        (cfg.einheit as string) || "m"
      ] || "Min."}`;
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Warten"}
      icon="⏱"
      accent="border-t-amber-400"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={[{ id: "out" }]}
    >
      <div>{text}</div>
    </BaseNode>
  );
}
