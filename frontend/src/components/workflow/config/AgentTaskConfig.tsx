import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { api } from "../../../api";
import type { NodeConfig } from "../types";
import { KeyValueEditor } from "../kv";
import { agentOptions, type AgentLite } from "./agentOptions";

export default function AgentTaskConfig({
  config,
  onChange,
  projectId,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  projectId?: number;
}) {
  const { data: agents } = useQuery({
    queryKey: ["agents", projectId ?? null],
    queryFn: () => api.get<AgentLite[]>(`/agents${projectId ? `?project_id=${projectId}` : ""}`),
    staleTime: 5 * 60_000,
  });
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  // A role name that does (not yet) exist stays operable: the field then switches to free
  // input by itself instead of discarding the value silently.
  const known = new Set(["plan_agent", "exec_agent", "review_agent", "assigned",
                           ...agentOptions(agents).map(([w]) => w)]);
  const [ownRole, setOwnRole] = useState(
    !!config.agent_role && !known.has(config.agent_role));

  return (
    <div className="space-y-3">
      {/* A select instead of a suggestion list: a text field with `datalist` shows what there
          is only while typing — so one has to know what to look for. With a handful of roles
          that would be a memory check without a reason. */}
      <label className="block text-xs font-medium text-muted">
        {tr("agent_task_config.who_works")}
        <select
          value={ownRole ? "__frei__" : (config.agent_role || "exec_agent")}
          onChange={(e) => {
            const value = e.target.value;
            // "Another role" switches to a text field instead of setting something at once:
            // otherwise a role name nobody chose would briefly stand there while switching.
            setOwnRole(value === "__frei__");
            if (value !== "__frei__") onChange({ ...config, agent_role: value });
          }}
          className={`mt-1 ${inp}`}
        >
          <optgroup label={tr("agent_task_config.from_project_settings")}>
            <option value="plan_agent">{tr("agent_task_config.planner_of_the_project_default_architect")}</option>
            <option value="exec_agent">{tr("agent_task_config.executor_of_the_project_default_developer")}</option>
            <option value="review_agent">{tr("agent_task_config.reviewer_project")}</option>
            <option value="assigned">{tr("agent_task_config.the_agent_assigned_to_the_ticket")}</option>
          </optgroup>
          <optgroup label="Fester Agent">
            {/* Concrete roles with the origin of the definition that actually applies. */}
            {agentOptions(agents).filter(([value]) => value).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </optgroup>
          <option value="__frei__">Andere Rolle (eintippen)…</option>
        </select>
        {ownRole && (
          <input
            value={config.agent_role || ""}
            onChange={(e) => onChange({ ...config, agent_role: e.target.value })}
            placeholder="rolle_die_es_noch_nicht_gibt"
            className={`mt-1 ${inp} font-mono`}
          />
        )}
        <span className="mt-1 block text-[11px] text-muted">
          <span dangerouslySetInnerHTML={{ __html: tr("agent_task_config.placeholders_hint") }} />
        </span>
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("agent_task_config.phase")}
        <select
          value={config.phase || "execution"}
          onChange={(e) => onChange({ ...config, phase: e.target.value as NodeConfig["phase"] })}
          className={`mt-1 ${inp}`}
        >
          <option value="planning">{tr("agent_task_config.planning")}</option>
          <option value="execution">{tr("agent_task_config.execution")}</option>
        </select>
      </label>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">{tr("agent_task_config.outcome_mapping_outcomes_map")}</div>
        <KeyValueEditor
          value={config.outcomes_map || {}}
          onChange={(m) => onChange({ ...config, outcomes_map: m as Record<string, string> })}
          keyPlaceholder={tr("agent_task_config.result")}
          valuePlaceholder={tr("agent_task_config.handle_status")}
        />
        <div className="mt-1 text-[11px] text-muted">
          {tr("agent_task_config.outcomes_hint")}
        </div>
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
