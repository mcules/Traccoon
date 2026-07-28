import { useQuery } from "@tanstack/react-query";
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
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Wer arbeitet
        <input
          list="agent-rollen"
          value={config.agent_role || ""}
          onChange={(e) => onChange({ ...config, agent_role: e.target.value })}
          placeholder="exec_agent"
          className={`mt-1 ${inp}`}
        />
        <datalist id="agent-rollen">
          <option value="plan_agent">Planer des Projekts (Standard: Architekt)</option>
          <option value="exec_agent">Ausführender des Projekts (Standard: Developer)</option>
          <option value="review_agent">Prüfer des Projekts</option>
          <option value="assigned">Der am Ticket zugewiesene Agent</option>
          {/* Konkrete Rollen mit Herkunft der Definition, die tatsächlich greift. */}
          {agentOptions(agents).map(([wert, beschriftung]) => (
            <option key={wert} value={wert}>{beschriftung}</option>
          ))}
        </datalist>
        <span className="mt-1 block text-[10px] text-muted">
          Die vier Platzhalter binden den Ablauf an die Projekt-Einstellungen. Ein konkreter
          Rollenname (z. B. <code>developer</code>) setzt sie fest.
        </span>
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
        <div className="mt-1 text-[10px] text-muted">
          Nur nötig, wenn ein Ergebnis auf einen anders benannten Ausgang gehen soll — die
          Ausgänge am Knoten (fertig/Zwischenstand/Rückfrage/Fehler) greifen von allein.
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
