import { NODE_TYPE_LABELS, type NodeConfig, type WorkflowInstanceStatus } from "./types";
import type { MemberLite } from "../../api";
import type { FlowNode } from "./nodes/shared";
import HumanTaskConfig from "./config/HumanTaskConfig";
import DecisionConfig from "./config/DecisionConfig";
import LoopConfig from "./config/LoopConfig";
import type { KontextFeld } from "./contextFields";
import ApprovalConfig from "./config/ApprovalConfig";
import AutoActionConfig from "./config/AutoActionConfig";
import AgentTaskConfig from "./config/AgentTaskConfig";
import WaitEventConfig from "./config/WaitEventConfig";
import SubflowConfig from "./config/SubflowConfig";
import StartConfig from "./config/StartConfig";

const OUTCOMES: [WorkflowInstanceStatus, string][] = [
  ["completed", "abgeschlossen"],
  ["failed", "fehlgeschlagen"],
  ["cancelled", "abgebrochen"],
];

export default function NodeConfigPanel({
  node,
  members,
  onChange,
  onDelete,
  projectId,
  subjectKind,
  kontextFelder,
  kontextFilter,
  defId,
}: {
  node: FlowNode | null;
  members: MemberLite[];
  onChange: (nodeId: string, config: NodeConfig) => void;
  onDelete: (nodeId: string) => void;
  projectId?: number;
  /** Subjekt des Ablaufs (issue|hardware_asset|standalone) — steuert Aktionen und Zustände. */
  subjectKind?: string;
  /** Kontextfelder dieses Ablaufs — die Verzweigung bietet sie zur Auswahl an. */
  kontextFelder?: KontextFeld[];
  /** Vorlagen-Filter (Hilfe im Verzweigungs-Editor). */
  kontextFilter?: import("./contextFields").KontextFilter[];
  /** Definition dieses Ablaufs — der Start-Knoten braucht sie für seine eigene Adresse. */
  defId?: number;
}) {
  if (!node) {
    return <div className="p-3 text-sm text-muted">Knoten anklicken, um ihn zu konfigurieren.</div>;
  }
  const config = node.data.config;
  const set = (c: NodeConfig) => onChange(node.id, c);
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center gap-2">
        <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{NODE_TYPE_LABELS[node.type]}</span>
        <div className="flex-1" />
        {node.type !== "start" && (
          <button
            onClick={() => onDelete(node.id)}
            className="text-sm text-muted hover:text-red-400"
            title="Knoten löschen"
          >
            🗑
          </button>
        )}
      </div>

      <label className="block text-xs font-medium text-muted">
        Bezeichnung
        <input
          value={config.label || ""}
          onChange={(e) => set({ ...config, label: e.target.value })}
          placeholder={NODE_TYPE_LABELS[node.type]}
          className={`mt-1 ${inp}`}
        />
      </label>

      {node.type === "human_task" && <HumanTaskConfig config={config} onChange={set} members={members} />}
      {node.type === "loop" && (
        <LoopConfig config={config} onChange={set} felder={kontextFelder} />
      )}
      {node.type === "decision" && (
        <DecisionConfig config={config} onChange={set} felder={kontextFelder}
          filter={kontextFilter} />
      )}
      {node.type === "approval" && <ApprovalConfig config={config} onChange={set} members={members} />}
      {node.type === "auto_action" && (
        <AutoActionConfig config={config} onChange={set} members={members}
          projectId={projectId} subjectKind={subjectKind} />
      )}
      {node.type === "agent_task" && (
        <AgentTaskConfig config={config} onChange={set} projectId={projectId} />
      )}
      {node.type === "wait_event" && <WaitEventConfig config={config} onChange={set} />}
      {node.type === "subflow" && <SubflowConfig config={config} onChange={set} />}

      {node.type === "end" && (
        <label className="block text-xs font-medium text-muted">
          Ergebnis
          <select
            value={config.outcome || "completed"}
            onChange={(e) => set({ ...config, outcome: e.target.value as WorkflowInstanceStatus })}
            className={`mt-1 ${inp}`}
          >
            {OUTCOMES.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
      )}

      {node.type === "start" && (
        <StartConfig config={config} onChange={set} defId={defId} />
      )}
    </div>
  );
}
