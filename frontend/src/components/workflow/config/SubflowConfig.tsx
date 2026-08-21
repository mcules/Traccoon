import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { workflowApi } from "../../../api";
import { SLOT_LABELS, type NodeConfig, type WorkflowSlot } from "../types";

const SLOTS = Object.keys(SLOT_LABELS) as WorkflowSlot[];

/**
 * Which flow is called here: a slot or a specific flow.
 *
 * Until now only the five shipped slots stood to choose from. That made the node useless
 * for everything of one's own: one builds a flow, wants to call it out of a second one, and
 * finds the ticket lifecycle in the dropdown. Both ways have their point: a **slot** is
 * resolved per project (an own adjustment beats the set beats the default), while a **named
 * flow** is exactly this one, no matter where it runs.
 */
export default function SubflowConfig({
  config,
  onChange,
  defId,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** The flow this node sits in; it must not call itself. */
  defId?: number;
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const { data: all } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  // Callable is what is published: a draft has no version a child instance could point at.
  // And the own flow drops out (an endless loop).
  const abläufe = (all || []).filter(
    (d) => d.current_version_id && !d.archived_at && d.id !== defId && !d.slot);

  const value = config.definition_id ? `def:${config.definition_id}`
    : config.slot ? `slot:${config.slot}` : "";
  const set = (v: string) => {
    const [art, remainder] = v.split(":");
    onChange({
      ...config,
      slot: art === "slot" ? (remainder as WorkflowSlot) : undefined,
      definition_id: art === "def" ? Number(remainder) : undefined,
    });
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Aufzurufender Ablauf
        <select value={value} onChange={(e) => set(e.target.value)} className={`mt-1 ${inp}`}>
          <option value="">— wählen —</option>
          <optgroup label={tr("subflow_config.fest_benannte_ablaeufe_je_projekt_aufgel")}>
            {SLOTS.map((s) => (
              <option key={s} value={`slot:${s}`}>{tr(SLOT_LABELS[s])}</option>
            ))}
          </optgroup>
          {abläufe.length > 0 && (
            <optgroup label={tr("subflow_config.eigene_ablaeufe_veroeffentlicht")}>
              {abläufe.map((d) => (
                <option key={d.id} value={`def:${d.id}`}>{d.name}</option>
              ))}
            </optgroup>
          )}
        </select>
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={config.inherit_context !== false}
          onChange={(e) => onChange({ ...config, inherit_context: e.target.checked })}
        />
        {tr("subflow_config.kontext_weitergeben")}
      </label>

      <p className="text-[11px] text-muted">
        {tr("subflow_config.hinweis")}
      </p>
    </div>
  );
}
