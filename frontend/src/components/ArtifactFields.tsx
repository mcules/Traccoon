import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import { BUTTON_TEXT } from "./ui";

interface Value { id: number; value: string; label: string; enabled: boolean }
interface Field {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; description: string; enabled: boolean;
  source: string; options: Value[]; dynamic_options: [string, string][];
}
interface Answer {
  artifact_id: number;
  fields: Field[];
  values: Record<string, any[]>;
}

const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * The fields of an artifact on the concrete unit: ticket, hardware or an own type.
 *
 * Which fields exist is said by the register (Administration → Artifacts); here only values
 * are assigned. Saving happens per field on leaving respectively on the selection, so that
 * nobody looks for a save button. A field with multiple selection shows its values as marks
 * that can be selected and deselected.
 */
export default function ArtifactFields({ artifactId, compact, all: all }: {
  artifactId: number; compact?: boolean; all?: boolean;
}) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["artifact-values", artifactId],
    queryFn: () => api.get<Answer>(`/artifacts/${artifactId}/values`),
  });

  const save = useMutation({
    mutationFn: (values: Record<string, any[]>) =>
      api.put<Answer>(`/artifacts/${artifactId}/values`, { values: values }),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["artifact-values", artifactId] });
      qc.invalidateQueries({ queryKey: ["artifacts"] });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
      // Back to the state of the database; otherwise the interface would show something that
      // is not stored at all.
      qc.invalidateQueries({ queryKey: ["artifact-values", artifactId] });
    },
  });

  // Built-in fields (status, priority, issue type …) have their familiar masks, and showing
  // them here as well would mean the same entry twice on one screen. `alle` shows them
  // deliberately (for an own artifact type without a mask for instance).
  const active = (data?.fields || []).filter((f) => f.enabled && (all || !f.source));
  if (isLoading || active.length === 0) return null;

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      {err && (
        <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
          {err}
        </div>
      )}
      {active.map((f) => (
        <FieldLine
          key={f.id} field={f} values={data?.values[f.key] || []}
          onSet={(values) => save.mutate({ [f.key]: values })}
        />
      ))}
    </div>
  );
}

function FieldLine({ field: field, values: values, onSet }: {
  field: Field; values: any[]; onSet: (values: any[]) => void;
}) {
  const label = (
    <label className="mb-0.5 block text-xs text-muted" title={field.description || undefined}>
      {field.label}
      {field.required && <span className="ml-0.5 text-red-300">*</span>}
    </label>
  );

  if (field.kind === "select") {
    const chosen = new Set(values.map(String));
    const selectable = field.dynamic_options.length
      ? field.dynamic_options.map(([v, l]) => ({ id: v, value: v, label: l, enabled: true }))
      : field.options.filter((o) => o.enabled || chosen.has(o.value));
    return (
      <div>
        {label}
        {field.multi ? (
          <div className="flex flex-wrap gap-1.5">
            {selectable.map((o) => {
              const an = chosen.has(o.value);
              return (
                <button
                  key={o.id}
                  onClick={() => onSet(an
                    ? values.filter((w) => String(w) !== o.value)
                    : [...values, o.value])}
                  className={`rounded px-2 py-0.5 text-xs ${
                    an ? "bg-brand/25 text-ink" : "bg-surface text-muted hover:text-ink"}`}
                >
                  {o.label || o.value}
                </button>
              );
            })}
            {selectable.length === 0 && (
              <span className="text-xs text-muted">{tr("artifact_fields.keine_werte_gepflegt")}</span>
            )}
          </div>
        ) : (
          <select
            value={values[0] != null ? String(values[0]) : ""}
            onChange={(e) => onSet(e.target.value ? [e.target.value] : [])}
            className={inp}
          >
            <option value="">—</option>
            {selectable.map((o) => (
              <option key={o.id} value={o.value}>{o.label || o.value}</option>
            ))}
          </select>
        )}
      </div>
    );
  }

  if (field.kind === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm" title={field.description || undefined}>
        <input type="checkbox" checked={values[0] === true}
          onChange={(e) => onSet(e.target.checked ? [true] : [])} />
        {field.label}
      </label>
    );
  }

  return (
    <div>
      {label}
      {field.multi
        ? <MultiText values={values} kind={field.kind} onSet={onSet} />
        : <SingleText value={values[0]} kind={field.kind} onSet={(w) => onSet(w === "" ? [] : [w])} />}
    </div>
  );
}

function SingleText({ value: value, kind, onSet }: {
  value: any; kind: string; onSet: (w: string) => void;
}) {
  const [text, setText] = useState(value != null ? String(value) : "");
  // Take over values changed from outside (another user, a reload).
  useEffect(() => { setText(value != null ? String(value) : ""); }, [value]);
  return (
    <input
      type={kind === "number" ? "number" : kind === "date" ? "date" : "text"}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== (value != null ? String(value) : "") && onSet(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className={inp}
    />
  );
}

function MultiText({ values: values, kind, onSet }: {
  values: any[]; kind: string; onSet: (w: any[]) => void;
}) {
  const [fresh, setNew] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((w, i) => (
        <span key={`${w}-${i}`} className="flex items-center gap-1 rounded bg-surface px-1.5 py-0.5 text-xs">
          {String(w)}
          <button onClick={() => onSet(values.filter((_, j) => j !== i))}
            className={BUTTON_TEXT.danger} title={tr("artifact_fields.entfernen")}>✕</button>
        </span>
      ))}
      <input
        type={kind === "number" ? "number" : kind === "date" ? "date" : "text"}
        value={fresh}
        onChange={(e) => setNew(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && fresh.trim()) {
            onSet([...values, fresh.trim()]);
            setNew("");
          }
        }}
        placeholder="+ Enter"
        className="w-28 rounded border border-line bg-surface px-2 py-0.5 text-xs text-ink"
      />
    </div>
  );
}
