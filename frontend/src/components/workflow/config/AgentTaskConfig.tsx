import type { NodeConfig } from "../types";
import { KeyValueEditor } from "../kv";

export default function AgentTaskConfig({
  config,
  onChange,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Agent-Rolle
        <input
          value={config.agent_role || ""}
          onChange={(e) => onChange({ ...config, agent_role: e.target.value })}
          placeholder="z. B. developer, architect"
          className={`mt-1 ${inp}`}
        />
      </label>

      <label className="block text-xs font-medium text-muted">
        Phase
        <select
          value={config.phase || "execution"}
          onChange={(e) => onChange({ ...config, phase: e.target.value as NodeConfig["phase"] })}
          className={`mt-1 ${inp}`}
        >
          <option value="planning">Planung</option>
          <option value="execution">Ausführung</option>
        </select>
      </label>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">Ergebnis-Zuordnung (outcomes_map)</div>
        <KeyValueEditor
          value={config.outcomes_map || {}}
          onChange={(m) => onChange({ ...config, outcomes_map: m as Record<string, string> })}
          keyPlaceholder="Ergebnis"
          valuePlaceholder="Handle/Status"
        />
        <div className="mt-1 text-[10px] text-muted">z. B. done → ok, failed → err, blocked → blocked</div>
      </div>

      <label className="block text-xs font-medium text-muted">
        Timeout (Sekunden)
        <input
          type="number"
          value={config.timeout_sec ?? ""}
          onChange={(e) =>
            onChange({ ...config, timeout_sec: e.target.value === "" ? undefined : Number(e.target.value) })
          }
          className={`mt-1 ${inp}`}
        />
      </label>
    </div>
  );
}
