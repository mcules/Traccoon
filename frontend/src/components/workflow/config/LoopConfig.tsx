import type { NodeConfig } from "../types";
import type { KontextFeld } from "../contextFields";
import { tr } from "../../../i18n";

/**
 * Was durchlaufen wird und wie das Element im Ablauf heißt.
 *
 * Die Liste ist ein Kontext-Pfad — meist das Ergebnis eines Schritts davor
 * (`tool.json.items`, `http.body.zeilen`) oder etwas, das der Auslöser mitgebracht hat.
 * Der Rest sind Namen: unter welchem Schlüssel das aktuelle Element steht und wo der
 * Zähler landet.
 */
export default function LoopConfig({
  config,
  onChange,
  felder = [],
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  felder?: KontextFeld[];
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  const set = (p: Partial<NodeConfig>) => onChange({ ...config, ...p });
  // Listen zuerst: alles andere lässt sich zwar durchlaufen (ein Einzelwert zählt als ein
  // Element), gemeint ist aber fast immer eine.
  const listen = felder.filter((f) => f.typ === "liste" || f.typ === "objekt");

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Liste (Kontext-Pfad)
        <input
          value={(config.liste as string) || ""}
          onChange={(e) => set({ liste: e.target.value.trim() })}
          list={felder.length ? "wf-listenfelder" : undefined}
          placeholder="tool.json.items"
          className={`mt-1 font-mono ${inp}`}
        />
        {felder.length > 0 && (
          <datalist id="wf-listenfelder">
            {[...listen, ...felder].map((f) => (
              <option key={f.pfad} value={f.pfad}>{`${f.beschreibung} · ${f.quelle}`}</option>
            ))}
          </datalist>
        )}
        <span className="mt-1 block text-[10px] text-muted">
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
        <span className="mt-1 block text-[10px] text-muted">
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
        <span className="mt-1 block text-[10px] text-muted">
          Schutz gegen die Liste, die aus Versehen sehr lang ist. Mehr als 500 geht nicht.
        </span>
      </label>
    </div>
  );
}
