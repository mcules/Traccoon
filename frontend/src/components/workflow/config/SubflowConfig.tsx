import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { workflowApi } from "../../../api";
import { SLOT_LABELS, type NodeConfig, type WorkflowSlot } from "../types";

const SLOTS = Object.keys(SLOT_LABELS) as WorkflowSlot[];

/**
 * Welcher Ablauf hier aufgerufen wird — ein Slot oder ein bestimmter Ablauf.
 *
 * Bisher standen nur die fünf ausgelieferten Slots zur Wahl. Damit war der Knoten für
 * alles Eigene nutzlos: man baut sich einen Ablauf, will ihn aus einem zweiten heraus
 * aufrufen — und findet im Dropdown den Ticket-Lebenszyklus. Beide Wege haben ihren Sinn:
 * ein **Slot** wird je Projekt aufgelöst (eigene Anpassung schlägt Satz schlägt Standard),
 * ein **benannter Ablauf** ist genau dieser eine, egal wo er läuft.
 */
export default function SubflowConfig({
  config,
  onChange,
  defId,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** Der Ablauf, in dem dieser Knoten steckt — er darf sich nicht selbst aufrufen. */
  defId?: number;
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const { data: alle } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  // Aufrufbar ist, was veröffentlicht ist — ein Entwurf hat keine Version, auf die eine
  // Kind-Instanz zeigen könnte. Und der eigene Ablauf fällt raus (Endlosschleife).
  const abläufe = (alle || []).filter(
    (d) => d.current_version_id && !d.archived_at && d.id !== defId && !d.slot);

  const wert = config.definition_id ? `def:${config.definition_id}`
    : config.slot ? `slot:${config.slot}` : "";
  const setzen = (v: string) => {
    const [art, rest] = v.split(":");
    onChange({
      ...config,
      slot: art === "slot" ? (rest as WorkflowSlot) : undefined,
      definition_id: art === "def" ? Number(rest) : undefined,
    });
  };

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Aufzurufender Ablauf
        <select value={wert} onChange={(e) => setzen(e.target.value)} className={`mt-1 ${inp}`}>
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
