import { SLOT_LABELS, type NodeConfig, type WorkflowSlot } from "../types";

const SLOTS = Object.keys(SLOT_LABELS) as WorkflowSlot[];

export default function SubflowConfig({
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
        Aufzurufender Ablauf
        <select
          value={config.slot || ""}
          onChange={(e) => onChange({ ...config, slot: (e.target.value || undefined) as WorkflowSlot })}
          className={`mt-1 ${inp}`}
        >
          <option value="">— wählen —</option>
          {SLOTS.map((s) => (
            <option key={s} value={s}>
              {SLOT_LABELS[s]}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={config.inherit_context !== false}
          onChange={(e) => onChange({ ...config, inherit_context: e.target.checked })}
        />
        Kontext weitergeben
      </label>

      <p className="text-[10px] text-muted">
        Aufgerufen wird der Ablauf, der für <b>dieses Projekt</b> gilt — also eine eigene
        Anpassung, sonst der Satz, sonst der Standard. Ausgänge: <b>fertig</b> bzw.
        <b> gescheitert</b>.
      </p>
    </div>
  );
}
