import { BaseNode, type FlowNodeProps } from "./shared";

/**
 * Lets time pass: the second way of waiting.
 *
 * `wait_event` waits for somebody who reports something; here the flow waits for the clock.
 * Waking happens on the beat of the engine, not from a sleeping task: a restart of the
 * backend must not forget a waiting run.
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
      aus={!!data.config.deaktiviert}
      sources={[{ id: "out" }]}
    >
      <div>{text}</div>
    </BaseNode>
  );
}
