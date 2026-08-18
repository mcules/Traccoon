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
 * Die Bausteine des Editors.
 *
 * Am Schreibtisch zieht man sie auf die Fläche. Auf einem Handy gibt es kein Ziehen: HTML5
 * kennt dort keine Zieh-Ereignisse, der Editor war damit schlicht nicht bedienbar. Deshalb
 * ist jeder Baustein zugleich ein Knopf — ein Tipp hängt ihn hinter den ausgewählten
 * Baustein, dieselbe Regel, die auch beim Ablegen ohne Linie gilt.
 */
export default function NodePalette({ onAdd, kompakt }: {
  onAdd?: (t: WorkflowNodeType) => void;
  /** Nebeneinander statt untereinander — für die schmale Ansicht über der Fläche. */
  kompakt?: boolean;
}) {
  return (
    <div className={kompakt ? "flex flex-wrap gap-1.5" : "space-y-1.5"}>
      {!kompakt && (
        <div className="mb-2 text-xs font-medium text-muted">
          {tr("node_palette.bausteine_in_die_flaeche_ziehen")}
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
          title={onAdd ? tr("node_palette.tippen_haengt_an") : undefined}
          className={"flex cursor-grab items-center gap-2 rounded border border-line bg-surface"
            + " px-2 py-1.5 text-sm hover:border-brand active:cursor-grabbing"
            + (kompakt ? "" : " w-full")}
        >
          <span>{ICONS[t]}</span>
          <span>{tr(NODE_TYPE_LABELS[t])}</span>
        </button>
      ))}
    </div>
  );
}
