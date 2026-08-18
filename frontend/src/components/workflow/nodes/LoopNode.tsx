import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

/**
 * Geht eine Liste Element für Element durch — der Weg von „ich habe die Daten" zu „ich
 * arbeite mit ihnen".
 *
 * Sequentiell und über eine Rückkante: der Ausgang `element` führt in den Körper, dessen
 * letzter Schritt wieder hierher zurück. Ist die Liste erschöpft, geht es bei `fertig`
 * weiter. Ohne die Rückkante läuft der Körper genau einmal — das meldet die Prüfung.
 */
export default function LoopNode({ id, data, selected }: FlowNodeProps) {
  const sources: SourceHandleDef[] = [
    { id: "element", label: "je Element", color: "!bg-brand" },
    { id: "fertig", label: "fertig", color: "!bg-green-500" },
  ];
  const liste = data.config.liste as string | undefined;
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || tr("node.loop")}
      icon="🔁"
      accent="border-t-cyan-500"
      selected={selected}
      runtimeState={data.runtimeState}
      aus={!!data.config.deaktiviert}
      sources={sources}
    >
      <div className="font-mono text-[10px]">{liste || tr("loop.keine_liste")}</div>
    </BaseNode>
  );
}
