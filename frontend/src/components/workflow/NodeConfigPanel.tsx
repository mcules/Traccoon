import { NODE_TYPE_LABELS, type NodeConfig, type WorkflowInstanceStatus } from "./types";
import { tr } from "../../i18n";
import type { MemberLite } from "../../api";
import type { FlowNode } from "./nodes/shared";
import HumanTaskConfig from "./config/HumanTaskConfig";
import DecisionConfig from "./config/DecisionConfig";
import LoopConfig from "./config/LoopConfig";
import TimerConfig from "./config/TimerConfig";
import type { KontextFeld } from "./contextFields";
import ApprovalConfig from "./config/ApprovalConfig";
import AutoActionConfig from "./config/AutoActionConfig";
import AgentTaskConfig from "./config/AgentTaskConfig";
import WaitEventConfig from "./config/WaitEventConfig";
import SubflowConfig from "./config/SubflowConfig";
import StartConfig from "./config/StartConfig";
import { KNOPF_TEXT } from "../ui";

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
  /** Subject of the flow (issue|hardware_asset|standalone); controls actions and states. */
  subjectKind?: string;
  /** Context fields of this flow; the branch offers them for selection. */
  kontextFelder?: KontextFeld[];
  /** Vorlagen-Filter (Hilfe im Verzweigungs-Editor). */
  kontextFilter?: import("./contextFields").KontextFilter[];
  /** Definition of this flow; the start node needs it for its own address. */
  defId?: number;
}) {
  if (!node) {
    return <div className="p-3 text-sm text-muted">{tr("node_config_panel.knoten_anklicken_um_ihn_zu_konfigurieren")}</div>;
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
            className={KNOPF_TEXT.gefahr}
            title={tr("node_config_panel.knoten_loeschen")}
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
                {l}
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
        <Abschalter config={config} onChange={set} />
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
function Abschalter({ config, onChange }: {
  config: NodeConfig; onChange: (c: NodeConfig) => void;
}) {
  const aus = !!config.deaktiviert;
  const modus = config.deaktiviert_modus || "ueberspringen";
  return (
    <div className={`space-y-2 rounded border p-2 ${
      aus ? "border-amber-500/40 bg-amber-500/5" : "border-line"}`}>
      <label className="flex items-center gap-2 text-xs text-ink">
        <input type="checkbox" checked={aus}
          onChange={(e) => onChange({
            ...config,
            deaktiviert: e.target.checked || undefined,
            deaktiviert_modus: e.target.checked ? modus : undefined,
          })} />
        {tr("abschalter.schalter")}
      </label>
      {aus && (
        <>
          <label className="block text-[11px] font-medium text-muted">
            {tr("abschalter.was_dann")}
            <select value={modus} className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
              onChange={(e) => onChange({ ...config, deaktiviert_modus: e.target.value as NodeConfig["deaktiviert_modus"] })}>
              <option value="ueberspringen">{tr("abschalter.ueberspringen")}</option>
              <option value="abbrechen">{tr("abschalter.abbrechen")}</option>
            </select>
          </label>
          <p className="text-[11px] text-muted">
            {modus === "abbrechen"
              ? tr("abschalter.hinweis_abbrechen")
              : tr("abschalter.hinweis_ueberspringen")}
          </p>
        </>
      )}
    </div>
  );
}
