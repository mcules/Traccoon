/** Simple key/value editor (for auto_action.params and outcomes_map). */
import { tr } from "../../i18n";
import { BUTTON_SMALL, BUTTON_TEXT} from "../ui";
export function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = "",
  valuePlaceholder = "Wert",
}: {
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const entries = Object.entries(value || {});
  const setKey = (oldKey: string, newKey: string) => {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(value)) out[k === oldKey ? newKey : k] = v;
    onChange(out);
  };
  const setVal = (k: string, v: string) => onChange({ ...value, [k]: v });
  const remove = (k: string) => {
    const out = { ...value };
    delete out[k];
    onChange(out);
  };
  const add = () => {
    if (value[""] !== undefined) return;
    onChange({ ...value, "": "" });
  };
  const inp = "rounded border border-line bg-card px-2 py-1 text-xs text-ink";
  return (
    <div className="space-y-1">
      {entries.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input
            value={k}
            onChange={(e) => setKey(k, e.target.value)}
            placeholder={keyPlaceholder}
            className={`w-32 font-mono ${inp}`}
          />
          <input
            value={typeof v === "string" ? v : JSON.stringify(v)}
            onChange={(e) => setVal(k, e.target.value)}
            placeholder={valuePlaceholder}
            className={`flex-1 ${inp}`}
          />
          <button onClick={() => remove(k)} className={BUTTON_TEXT.danger} title={tr("kv.remove")}>
            ✕
          </button>
        </div>
      ))}
      <button onClick={add} className={BUTTON_SMALL.secondary}>
        + Eintrag
      </button>
    </div>
  );
}
