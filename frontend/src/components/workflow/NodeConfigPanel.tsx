import { NODE_TYPE_LABELS, type NodeConfig, type WorkflowInstanceStatus } from "./types";
import { tr } from "../../i18n";
import type { MemberLite } from "../../api";
import type { FlowNode } from "./nodes/shared";
import HumanTaskConfig from "./config/HumanTaskConfig";
import DecisionConfig from "./config/DecisionConfig";
import LoopConfig from "./config/LoopConfig";
import TimerConfig from "./config/TimerConfig";
import type { ContextField } from "./contextFields";
import ApprovalConfig from "./config/ApprovalConfig";
import AutoActionConfig from "./config/AutoActionConfig";
import AgentTaskConfig from "./config/AgentTaskConfig";
import WaitEventConfig from "./config/WaitEventConfig";
import SubflowConfig from "./config/SubflowConfig";
import StartConfig from "./config/StartConfig";
import { BUTTON_TEXT } from "../ui";

const OUTCOMES: [WorkflowInstanceStatus, string][] = [
  ["completed", "outcome.completed"],
  ["failed", "outcome.failed"],
  ["cancelled", "outcome.cancelled"],
];

export default function NodeConfigPanel({
  node,
  members,
  onChange,
  onDelete,
  projectId,
  subjectKind,
  contextFields: contextFields,
  contextFilter: contextFilter,
  defId,
}: {
  node: FlowNode | null;
  members: MemberLite[];
  onChange: (nodeId: string, config: NodeConfig) => void;
  onDelete: (nodeId: string) => void;
  projectId?: number;
  /** Subject of the flow (issue|hardware_asset|standalone); controls actions and states. */
  subjectKind?: string;
  /** Context fields of this flow; the branch offers them for selection. */
  contextFields?: ContextField[];
  /** Vorlagen-Filter (Hilfe im Verzweigungs-Editor). */
  contextFilter?: import("./contextFields").ContextFilter[];
  /** Definition of this flow; the start node needs it for its own address. */
  defId?: number;
}) {
  if (!node) {
    return <div className="p-3 text-sm text-muted">{tr("node_config_panel.click_a_node_to_configure_it")}</div>;
  }
  const config = node.data.config;
  const set = (c: NodeConfig) => onChange(node.id, c);
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center gap-2">
        <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{tr(NODE_TYPE_LABELS[node.type])}</span>
        <div className="flex-1" />
        {node.type !== "start" && (
          <button
            onClick={() => onDelete(node.id)}
            className={BUTTON_TEXT.danger}
            title={tr("node_config_panel.delete_node")}
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
          placeholder={tr(NODE_TYPE_LABELS[node.type])}
          className={`mt-1 ${inp}`}
        />
      </label>

      {node.type === "human_task" && <HumanTaskConfig config={config} onChange={set} members={members} />}
      {node.type === "timer" && <TimerConfig config={config} onChange={set} />}
      {node.type === "loop" && (
        <LoopConfig config={config} onChange={set} fields={contextFields} />
      )}
      {node.type === "decision" && (
        <DecisionConfig config={config} onChange={set} fields={contextFields}
          filter={contextFilter} />
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
      {node.type === "subflow" && <SubflowConfig config={config} onChange={set} defId={defId} />}

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
                {tr(l)}
              </option>
            ))}
          </select>
        </label>
      )}

      {node.type === "start" && (
        <StartConfig config={config} onChange={set} defId={defId}
          subjectKind={subjectKind} />
      )}

      {node.type !== "start" && node.type !== "end" && (
        <Killswitch config={config} onChange={set} />
      )}
    </div>
  );
}

/**
 * Switch a step off without taking it out of the graph.
 *
 * Two different needs behind the same switch, and they need different answers. While
 * building one takes a step out of the way and everything behind it should keep running. In
 * an emergency one pulls the handbrake, and then a flow silently running past the switched
 * off step would be exactly the dangerous outcome. That is why the choice stands there
 * explicitly instead of being guessed.
 */
function Killswitch({ config, onChange }: {
  config: NodeConfig; onChange: (c: NodeConfig) => void;
}) {
  const from = !!config.disabled;
  const mode = config.disabled_mode || "skip";
  return (
    <div className={`space-y-2 rounded border p-2 ${
      from ? "border-amber-500/40 bg-amber-500/5" : "border-line"}`}>
      <label className="flex items-center gap-2 text-xs text-ink">
        <input type="checkbox" checked={from}
          onChange={(e) => onChange({
            ...config,
            disabled: e.target.checked || undefined,
            disabled_mode: e.target.checked ? mode : undefined,
          })} />
        {tr("killswitch.switch_step_off")}
      </label>
      {from && (
        <>
          <label className="block text-[11px] font-medium text-muted">
            {tr("killswitch.what_happen")}
            <select value={mode} className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
              onChange={(e) => onChange({ ...config, disabled_mode: e.target.value as NodeConfig["disabled_mode"] })}>
              <option value="skip">{tr("killswitch.skip_continue")}</option>
              <option value="abort">{tr("killswitch.end_flow_here")}</option>
            </select>
          </label>
          <p className="text-[11px] text-muted">
            {mode === "abort"
              ? tr("killswitch.runs_end_here_cancelled")
              : tr("killswitch.step_does_nothing_flow")}
          </p>
        </>
      )}
    </div>
  );
}
