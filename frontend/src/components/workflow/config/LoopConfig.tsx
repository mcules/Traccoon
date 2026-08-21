import type { NodeConfig } from "../types";
import type { ContextField } from "../contextFields";
import { tr } from "../../../i18n";

/**
 * What is walked through and what the element is called in the flow.
 *
 * The list is a context path, usually the result of a step before it (`tool.json.items`,
 * `http.body.zeilen`) or something the trigger brought along. The rest are names: under
 * which key the current element stands and where the counter lands.
 */
export default function LoopConfig({
  config,
  onChange,
  felder: fields = [],
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  felder?: ContextField[];
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const set = (p: Partial<NodeConfig>) => onChange({ ...config, ...p });
  // Lists first: everything else can be walked through as well (a single value counts as one
  // element), but a list is almost always what is meant.
  const listen = fields.filter((f) => f.type === "list" || f.type === "object");

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Liste (Kontext-Pfad)
        <input
          value={(config.liste as string) || ""}
          onChange={(e) => set({ liste: e.target.value.trim() })}
          list={fields.length ? "wf-listenfelder" : undefined}
          placeholder="tool.json.items"
          className={`mt-1 font-mono ${inp}`}
        />
        {fields.length > 0 && (
          <datalist id="wf-listenfelder">
            {[...listen, ...fields].map((f) => (
              <option key={f.path} value={f.path}>{`${tr(f.description)} · ${f.source}`}</option>
            ))}
          </datalist>
        )}
        <span className="mt-1 block text-[11px] text-muted">
          Ein einzelner Wert zählt als Liste mit einem Element; fehlt der Pfad, wird der
          Körper übersprungen.
        </span>
      </label>

      <div className="flex gap-2">
        <label className="flex-1 text-xs font-medium text-muted">
          {tr("loop_config.element_heisst")}
          <input
            value={(config.element as string) || ""}
            onChange={(e) => set({ element: e.target.value.trim() })}
            placeholder="element"
            className={`mt-1 font-mono ${inp}`}
          />
        </label>
        <label className="flex-1 text-xs font-medium text-muted">
          {tr("loop_config.zaehler_heisst")}
          <input
            value={(config.index as string) || ""}
            onChange={(e) => set({ index: e.target.value.trim() })}
            placeholder="i"
            className={`mt-1 font-mono ${inp}`}
          />
        </label>
      </div>

      <label className="block text-xs font-medium text-muted">
        Ergebnis je Durchlauf einsammeln (Kontext-Pfad, optional)
        <input
          value={(config.sammle as string) || ""}
          onChange={(e) => set({ sammle: e.target.value.trim() })}
          placeholder="tool.json"
          className={`mt-1 font-mono ${inp}`}
        />
        <span className="mt-1 block text-[11px] text-muted">
          Landet am Ende als Liste unter <code className="rounded bg-surface px-1">ergebnisse</code>.
        </span>
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("loop_config.hoechstens_durchlaeufe")}
        <input
          type="number"
          min={1}
          max={500}
          value={(config.max as number) ?? ""}
          onChange={(e) => set({ max: e.target.value ? Number(e.target.value) : undefined })}
          placeholder="500"
          className={`mt-1 ${inp}`}
        />
        <span className="mt-1 block text-[11px] text-muted">
          Schutz gegen die Liste, die aus Versehen sehr lang ist. Mehr als 500 geht nicht.
        </span>
      </label>
    </div>
  );
}
