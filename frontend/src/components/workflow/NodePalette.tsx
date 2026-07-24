import { NODE_TYPE_LABELS, type WorkflowNodeType } from "./types";

const ICONS: Record<WorkflowNodeType, string> = {
  start: "▶",
  end: "⏹",
  human_task: "🧑",
  decision: "◈",
  approval: "✅",
  auto_action: "⚙",
  agent_task: "🤖",
};

const ORDER: WorkflowNodeType[] = [
  "start",
  "human_task",
  "decision",
  "approval",
  "auto_action",
  "agent_task",
  "end",
];

/** Ziehbare Bausteine für den Editor. */
export default function NodePalette() {
  return (
    <div className="space-y-1.5">
      <div className="mb-2 text-xs font-medium text-muted">Bausteine — in die Fläche ziehen</div>
      {ORDER.map((t) => (
        <div
          key={t}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData("application/reactflow", t);
            e.dataTransfer.effectAllowed = "move";
          }}
          className="flex cursor-grab items-center gap-2 rounded border border-line bg-surface px-2 py-1.5 text-sm hover:border-brand active:cursor-grabbing"
        >
          <span>{ICONS[t]}</span>
          <span>{NODE_TYPE_LABELS[t]}</span>
        </div>
      ))}
    </div>
  );
}
