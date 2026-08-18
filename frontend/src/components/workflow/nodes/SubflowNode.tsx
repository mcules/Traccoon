import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { SLOT_LABELS } from "../types";

/**
 * Ruft einen anderen Ablauf auf und wartet auf ihn. Welcher Graph das konkret ist,
 * entscheidet die Auflösung des Projekts (eigene Anpassung → Satz → Standard) — ein
 * angepasster Abnahme-Prozess wirkt damit überall, wo er aufgerufen wird.
 */
export default function SubflowNode({ id, data, selected }: FlowNodeProps) {
  const sources: SourceHandleDef[] = [
    { id: "completed", label: "fertig", color: "!bg-green-500" },
    { id: "failed", label: "gescheitert", color: "!bg-red-500" },
  ];
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Anderer Ablauf"}
      icon="🔗"
      accent="border-t-indigo-500"
      selected={selected}
      runtimeState={data.runtimeState}
      aus={!!data.config.deaktiviert}
      sources={sources}
    >
      <div>{data.config.slot ? SLOT_LABELS[data.config.slot] : "kein Ablauf gewählt"}</div>
    </BaseNode>
  );
}
