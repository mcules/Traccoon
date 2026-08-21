import { NODE_TYPE_LABELS, type WorkflowNodeType } from "./types";
import { tr } from "../../i18n";

const ICONS: Record<WorkflowNodeType, string> = {
  start: "▶",
  end: "⏹",
  human_task: "🧑",
  decision: "◈",
  approval: "✅",
  auto_action: "⚙",
  agent_task: "🤖",
  wait_event: "⏳",
  subflow: "🔗",
  loop: "🔁",
  timer: "⏱",
};

const ORDER: WorkflowNodeType[] = [
  "start",
  "human_task",
  "decision",
  "approval",
  "auto_action",
  "agent_task",
  "wait_event",
  "subflow",
  "loop",
  "timer",
  "end",
];

/**
 * The building blocks of the editor.
 *
 * At the desk one drags them onto the canvas. On a phone there is no dragging: HTML5 knows
 * no drag events there, and the editor was therefore simply not operable. That is why every
 * building block is a button at the same time; a tap hangs it behind the selected block, the
 * same rule that applies when dropping without a line.
 */
export default function NodePalette({ onAdd, compact }: {
  onAdd?: (t: WorkflowNodeType) => void;
  /** Side by side instead of below each other, for the narrow view above the canvas. */
  compact?: boolean;
}) {
  return (
    <div className={compact ? "flex flex-wrap gap-1.5" : "space-y-1.5"}>
      {!compact && (
        <div className="mb-2 text-xs font-medium text-muted">
          {tr("node_palette.building_blocks_drag_onto_the_canvas")}
        </div>
      )}
      {ORDER.map((t) => (
        <button
          key={t}
          type="button"
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData("application/reactflow", t);
            e.dataTransfer.effectAllowed = "move";
          }}
          onClick={() => onAdd?.(t)}
          title={onAdd ? tr("node_palette.tap_attach_block_behind") : undefined}
          className={"flex cursor-grab items-center gap-2 rounded border border-line bg-surface"
            + " px-2 py-1.5 text-sm hover:border-brand active:cursor-grabbing"
            + (compact ? "" : " w-full")}
        >
          <span>{ICONS[t]}</span>
          <span>{tr(NODE_TYPE_LABELS[t])}</span>
        </button>
      ))}
    </div>
  );
}
