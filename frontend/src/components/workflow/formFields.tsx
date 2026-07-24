import type { FormField } from "./types";

export function emptyField(): FormField {
  return { key: "", label: "", type: "text", required: false };
}

/** Standard-/Leerwerte für einen Satz Formularfelder. */
export function defaultValues(fields: FormField[] | undefined): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of fields || []) {
    out[f.key] = f.type === "boolean" ? false : f.type === "number" ? "" : "";
  }
  return out;
}

const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/** Editor für die Feld-Definition eines human_task (Konfig-Panel). */
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
              placeholder="schlüssel"
              className="w-28 rounded border border-line bg-card px-2 py-1 font-mono text-xs"
            />
            <input
              value={f.label}
              onChange={(e) => patch(i, { label: e.target.value })}
              placeholder="Beschriftung"
              className="flex-1 rounded border border-line bg-card px-2 py-1 text-sm"
            />
            <button onClick={() => remove(i)} className="text-muted hover:text-red-400" title="Feld entfernen">
              ✕
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={f.type}
              onChange={(e) => patch(i, { type: e.target.value as FormField["type"] })}
              className="rounded border border-line bg-card px-2 py-1 text-xs text-ink"
            >
              <option value="text">Text</option>
              <option value="number">Zahl</option>
              <option value="select">Auswahl</option>
              <option value="date">Datum</option>
              <option value="boolean">Ja/Nein</option>
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
                placeholder="Optionen, kommagetrennt"
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-xs"
              />
            )}
            {f.type !== "select" && f.type !== "boolean" && (
              <input
                value={f.placeholder || ""}
                onChange={(e) => patch(i, { placeholder: e.target.value })}
                placeholder="Platzhalter"
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-xs"
              />
            )}
          </div>
        </div>
      ))}
      <button
        onClick={() => onChange([...fields, emptyField()])}
        className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink"
      >
        + Feld
      </button>
    </div>
  );
}

/** Ausfüll-Formular für die Runtime (WorkflowTaskForm). */
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
              <option value="">— wählen —</option>
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

/** Prüft Pflichtfelder; gibt fehlende Beschriftungen zurück. */
export function missingRequired(fields: FormField[], values: Record<string, any>): string[] {
  const miss: string[] = [];
  for (const f of fields) {
    if (!f.required) continue;
    const v = values[f.key];
    if (v === undefined || v === "" || v === null) miss.push(f.label || f.key);
  }
  return miss;
}
