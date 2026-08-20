import type { NodeConfig, DecisionBranch, JsonLogic } from "../types";
import { tr } from "../../../i18n";
import type { KontextFeld, KontextFilter } from "../contextFields";
import { KNOPF_KLEIN, KNOPF_TEXT} from "../../ui";

const OPS = ["==", "!=", ">", ">=", "<", "<="];

type Simple = { field: string; op: string; value: string };

/** JSONLogic → einfacher Feld/Operator/Wert-Builder (best effort). */
function parseGuard(g: JsonLogic | undefined): Simple {
  if (!g || typeof g === "boolean") return { field: "", op: "==", value: "" };
  const op = Object.keys(g)[0];
  const args = (g as any)[op];
  if (OPS.includes(op) && Array.isArray(args) && args[0] && typeof args[0] === "object" && "var" in args[0]) {
    const v = args[1];
    return { field: String(args[0].var), op, value: v == null ? "" : String(v) };
  }
  return { field: "", op: "==", value: "" };
}

/** Builder to JSONLogic. Numeric values are encoded as a number. */
function buildGuard(s: Simple): JsonLogic | undefined {
  if (!s.field) return undefined;
  const num = s.value !== "" && !isNaN(Number(s.value)) ? Number(s.value) : s.value;
  return { [s.op]: [{ var: s.field }, num] };
}

let handleSeq = 0;

export default function DecisionConfig({
  config,
  onChange,
  felder = [],
  filter = [],
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** Filter for templates; here only as a help, the conditions themselves compute without it. */
  filter?: KontextFilter[];
  /** Context fields this flow really has: from the trigger, the steps and keys set by
   *  itself (see `contextFields.ts`). */
  felder?: KontextFeld[];
}) {
  const branches = config.branches || [];
  const setBranches = (b: DecisionBranch[]) => onChange({ ...config, branches: b });
  const patch = (i: number, p: Partial<DecisionBranch>) =>
    setBranches(branches.map((b, j) => (j === i ? { ...b, ...p } : b)));
  const remove = (i: number) => {
    const gone = branches[i];
    setBranches(branches.filter((_, j) => j !== i));
    if (config.default_handle === gone.handle) onChange({ ...config, default_handle: undefined });
  };
  const add = () =>
    setBranches([...branches, { handle: `b${Date.now()}_${handleSeq++}`, label: "Neuer Zweig" }]);

  const inp = "rounded border border-line bg-card px-2 py-1 text-xs text-ink";
  return (
    <div className="space-y-3">
      <div className="mb-1 text-xs font-medium text-muted">{tr("decision_config.zweige")}</div>
      <div className="space-y-2">
        {branches.map((b, i) => {
          const s = parseGuard(b.guard);
          const upd = (p: Partial<Simple>) => patch(i, { guard: buildGuard({ ...s, ...p }) });
          return (
            <div key={b.handle} className="rounded border border-line bg-surface p-2">
              <div className="mb-1 flex items-center gap-1.5">
                <input
                  value={b.label}
                  onChange={(e) => patch(i, { label: e.target.value })}
                  placeholder={tr("decision_config.beschriftung")}
                  className={`flex-1 ${inp}`}
                />
                <button onClick={() => remove(i)} className={KNOPF_TEXT.gefahr} title={tr("decision_config.zweig_entfernen")}>
                  ✕
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {/* Auswahl statt Ratefläche: die Pfade stehen im Kontext dieses Ablaufs.
                    Freie Eingabe bleibt möglich — der Katalog beschreibt, er schreibt nicht
                    vor (eigene Felder aus einem Ziel-Aufruf etwa kennt er nicht). */}
                <input
                  value={s.field}
                  onChange={(e) => upd({ field: e.target.value })}
                  placeholder={tr("decision_config.kontext_feld")}
                  list={felder.length ? "wf-kontextfelder" : undefined}
                  className={`w-40 font-mono ${inp}`}
                />
                <select value={s.op} onChange={(e) => upd({ op: e.target.value })} className={inp}>
                  {OPS.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
                <input
                  value={s.value}
                  onChange={(e) => upd({ value: e.target.value })}
                  placeholder={tr("decision_config.wert")}
                  className={`w-24 ${inp}`}
                />
                <span className="text-[11px] text-muted">
                  leeres Feld = greift immer (Auffang-Zweig, gehört ans Ende)
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {felder.length > 0 && (
        <datalist id="wf-kontextfelder">
          {felder.map((f) => (
            <option key={f.pfad} value={f.pfad}>{`${f.beschreibung} · ${f.quelle}`}</option>
          ))}
        </datalist>
      )}

      <button onClick={add} className={KNOPF_KLEIN.neben}>
        + Zweig
      </button>

      {filter.length > 0 && (
        <details className="rounded border border-line bg-surface p-2 text-xs text-muted">
          <summary className="cursor-pointer">
            Vorlagen-Filter ({filter.length}) — für Texte in Aktionen
          </summary>
          <p className="mt-1 text-[11px]">
            Schreibweise <code className="rounded bg-card px-1">{"{{ pfad | filter:argument }}"}</code>,
            von links nach rechts. Beispiel:{" "}
            <code className="rounded bg-card px-1">{"{{ spam.score | mal:100 | rund:1 }}"}</code>
          </p>
          <table className="mt-2 w-full">
            <tbody>
              {filter.map((f) => (
                <tr key={f.name} className="align-top">
                  <td className="pr-2 font-mono text-ink">{f.name}</td>
                  <td>{f.hilfe}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {felder.length > 0 && (
        <details className="rounded border border-line bg-surface p-2 text-xs text-muted">
          <summary className="cursor-pointer">Verfügbare Kontext-Felder ({felder.length})</summary>
          {/* Untereinander statt vier Spalten: im schmalen Panel lief die Herkunft
              rechts aus dem Bild — sie ist aber genau das, was man wissen will. */}
          <ul className="mt-2 space-y-1.5">
            {felder.map((f) => (
              <li key={f.pfad}>
                <div className="flex items-baseline gap-1.5">
                  <code className="break-all font-mono text-ink">{f.pfad}</code>
                  <span className="shrink-0 text-[11px] opacity-70">{f.typ}</span>
                </div>
                <div className="text-[11px] leading-snug">
                  {f.beschreibung} · <span className="opacity-70">{f.quelle}</span>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      <label className="block text-xs font-medium text-muted">
        Standard-Zweig (wenn keine Bedingung greift)
        <select
          value={config.default_handle || ""}
          onChange={(e) => onChange({ ...config, default_handle: e.target.value || undefined })}
          className={`mt-1 w-full ${inp}`}
        >
          <option value="">— keiner —</option>
          {branches.map((b) => (
            <option key={b.handle} value={b.handle}>
              {b.label || b.handle}
            </option>
          ))}
          {/* Zeigt einen Standard, der (noch) kein Zweig ist — sonst stünde hier stumm
              „keiner", und beim nächsten Speichern wäre die zugehörige Kante verwaist. */}
          {config.default_handle && !branches.some((b) => b.handle === config.default_handle) && (
            <option value={config.default_handle}>
              {config.default_handle} — kein Zweig, bitte anlegen
            </option>
          )}
        </select>
        {config.default_handle && !branches.some((b) => b.handle === config.default_handle) && (
          <span className="mt-1 block text-[11px] text-amber-400">
            Dieser Ausgang ist verdrahtet, aber nicht als Zweig beschrieben. Lege einen Zweig
            mit dieser Kennung an (ohne Bedingung = greift immer).
          </span>
        )}
      </label>
    </div>
  );
}
