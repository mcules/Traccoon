import { BaseNode, type FlowNodeProps, type SourceHandleDef } from "./shared";
import { tr } from "../../../i18n";

/**
 * Walks through a list element by element: the way from "I have the data" to "I work with
 * it".
 *
 * Sequentially and over a back edge: the exit `element` leads into the body, whose last
 * step leads back here. When the list is exhausted it continues at `fertig`. Without the
 * back edge the body runs exactly once, which the validation reports.
 */
export default function LoopNode({ id, data, selected }: FlowNodeProps) {
  const sources: SourceHandleDef[] = [
    { id: "element", label: "je Element", color: "!bg-brand" },
    { id: "fertig", label: "fertig", color: "!bg-green-500" },
  ];
  const listing = data.config.list as string | undefined;
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || tr("node.loop")}
      icon="🔁"
      accent="border-t-cyan-500"
      selected={selected}
      runtimeState={data.runtimeState}
      from={!!data.config.disabled}
      sources={sources}
    >
      <div className="font-mono text-[11px]">{listing || tr("loop.no_list_chosen")}</div>
    </BaseNode>
  );
}
