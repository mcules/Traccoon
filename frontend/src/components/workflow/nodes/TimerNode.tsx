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
  const text = cfg.to
    ? `bis ${cfg.to}`
    : `${cfg.duration ?? "?"} ${{ s: "Sek.", m: "Min.", h: "Std.", t: "Tage" }[
        (cfg.unit as string) || "m"
      ] || "Min."}`;
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Warten"}
      icon="⏱"
      accent="border-t-amber-400"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={[{ id: "out" }]}
    >
      <div>{text}</div>
    </BaseNode>
  );
}
