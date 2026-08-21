import type { FormField } from "./types";
import { tr } from "../../i18n";
import { BUTTON_KLEIN, BUTTON_TEXT} from "../ui";

export function emptyField(): FormField {
  return { key: "", label: "", type: "text", required: false };
}

/** Default and empty values for a set of form fields. */
export function defaultValues(fields: FormField[] | undefined): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of fields || []) {
    out[f.key] = f.type === "boolean" ? false : f.type === "number" ? "" : "";
  }
  return out;
}

const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/** Editor for the field definition of a human_task (the config panel). */
export function FormFieldsEditor({
  fields,
  onChange,
}: {
  fields: FormField[];
  onChange: (f: FormField[]) => void;
}) {
  const patch = (i: number, p: Partial<FormField>) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, ...p } : f)));
  const remove = (i: number) => onChange(fields.filter((_, j) => j !== i));

  return (
    <div className="space-y-2">
      {fields.map((f, i) => (
        <div key={i} className="rounded border border-line bg-surface p-2">
          <div className="mb-1 flex items-center gap-1.5">
            <input
              value={f.key}
              onChange={(e) => patch(i, { key: e.target.value })}
              placeholder={tr("form_fields.schluessel")}
              className="w-28 rounded border border-line bg-card px-2 py-1 font-mono text-xs"
            />
            <input
              value={f.label}
              onChange={(e) => patch(i, { label: e.target.value })}
              placeholder={tr("form_fields.beschriftung")}
              className="flex-1 rounded border border-line bg-card px-2 py-1 text-sm"
            />
            <button onClick={() => remove(i)} className={BUTTON_TEXT.gefahr} title={tr("form_fields.feld_entfernen")}>
              ✕
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={f.type}
              onChange={(e) => patch(i, { type: e.target.value as FormField["type"] })}
              className="rounded border border-line bg-card px-2 py-1 text-xs text-ink"
            >
              <option value="text">{tr("form_fields.text")}</option>
              <option value="number">{tr("form_fields.zahl")}</option>
              <option value="select">{tr("form_fields.auswahl")}</option>
              <option value="date">{tr("form_fields.datum")}</option>
              <option value="boolean">{tr("form_fields.ja_nein")}</option>
            </select>
            <label className="flex items-center gap-1 text-xs text-muted">
              <input
                type="checkbox"
                checked={!!f.required}
                onChange={(e) => patch(i, { required: e.target.checked })}
              />
              Pflicht
            </label>
            {f.type === "select" && (
              <input
                value={(f.options || []).join(", ")}
                onChange={(e) =>
                  patch(i, { options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })
                }
                placeholder={tr("form_fields.optionen_kommagetrennt")}
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-xs"
              />
            )}
            {f.type !== "select" && f.type !== "boolean" && (
              <input
                value={f.placeholder || ""}
                onChange={(e) => patch(i, { placeholder: e.target.value })}
                placeholder={tr("form_fields.platzhalter")}
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-xs"
              />
            )}
          </div>
        </div>
      ))}
      <button
        onClick={() => onChange([...fields, emptyField()])}
        className={BUTTON_KLEIN.neben}
      >
        + Feld
      </button>
    </div>
  );
}

/** Filling-in form for the runtime (WorkflowTaskForm). */
export function DynamicForm({
  fields,
  values,
  onChange,
}: {
  fields: FormField[];
  values: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
}) {
  const set = (k: string, v: any) => onChange({ ...values, [k]: v });
  return (
    <div className="space-y-2">
      {fields.map((f) => (
        <label key={f.key} className="block text-xs text-muted">
          {f.label || f.key}
          {f.required && <span className="text-red-400"> *</span>}
          {f.type === "text" && (
            <input
              value={values[f.key] ?? ""}
              placeholder={f.placeholder}
              onChange={(e) => set(f.key, e.target.value)}
              className={`mt-1 ${inp}`}
            />
          )}
          {f.type === "number" && (
            <input
              type="number"
              value={values[f.key] ?? ""}
              placeholder={f.placeholder}
              onChange={(e) => set(f.key, e.target.value === "" ? "" : Number(e.target.value))}
              className={`mt-1 ${inp}`}
            />
          )}
          {f.type === "date" && (
            <input
              type="date"
              value={values[f.key] ?? ""}
              onChange={(e) => set(f.key, e.target.value)}
              className={`mt-1 ${inp}`}
            />
          )}
          {f.type === "select" && (
            <select
              value={values[f.key] ?? ""}
              onChange={(e) => set(f.key, e.target.value)}
              className={`mt-1 ${inp}`}
            >
              <option value="">{tr("action_params.waehlen")}</option>
              {(f.options || []).map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          )}
          {f.type === "boolean" && (
            <span className="mt-1 flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={!!values[f.key]}
                onChange={(e) => set(f.key, e.target.checked)}
              />
              {f.placeholder || "Ja"}
            </span>
          )}
        </label>
      ))}
    </div>
  );
}

/** Checks the mandatory fields; returns the missing labels. */
export function missingRequired(fields: FormField[], values: Record<string, any>): string[] {
  const miss: string[] = [];
  for (const f of fields) {
    if (!f.required) continue;
    const v = values[f.key];
    if (v === undefined || v === "" || v === null) miss.push(f.label || f.key);
  }
  return miss;
}
