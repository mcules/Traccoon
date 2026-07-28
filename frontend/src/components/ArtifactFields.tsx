import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";

interface Wert { id: number; value: string; label: string; enabled: boolean }
interface Feld {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; description: string; enabled: boolean;
  source: string; options: Wert[]; dynamic_options: [string, string][];
}
interface Antwort {
  artifact_id: number;
  fields: Feld[];
  values: Record<string, any[]>;
}

const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Die Felder eines Artefakts am konkreten Exemplar — Ticket, Hardware oder eigener Typ.
 *
 * Welche Felder es gibt, sagt das Register (Administration → Artefakte); hier werden nur
 * Werte zugeordnet. Gespeichert wird je Feld beim Verlassen bzw. bei der Auswahl, damit
 * niemand einen Speichern-Knopf sucht. Ein Feld mit Mehrfachauswahl zeigt seine Werte als
 * an-/abwählbare Marken.
 */
export default function ArtifactFields({ artifactId, compact, alle }: {
  artifactId: number; compact?: boolean; alle?: boolean;
}) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["artifact-values", artifactId],
    queryFn: () => api.get<Antwort>(`/artifacts/${artifactId}/values`),
  });

  const speichern = useMutation({
    mutationFn: (werte: Record<string, any[]>) =>
      api.put<Antwort>(`/artifacts/${artifactId}/values`, { values: werte }),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["artifact-values", artifactId] });
      qc.invalidateQueries({ queryKey: ["artifacts"] });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
      // Zurück auf den Stand der Datenbank — sonst zeigte die Oberfläche etwas an,
      // das gar nicht gespeichert ist.
      qc.invalidateQueries({ queryKey: ["artifact-values", artifactId] });
    },
  });

  // Eingebaute Felder (Status, Priorität, Vorgangsart …) haben ihre gewohnten Masken —
  // sie hier zusätzlich zu zeigen, hieße dieselbe Angabe zweimal auf einem Bildschirm.
  // `alle` blendet sie bewusst ein (z. B. für einen eigenen Artefakt-Typ ohne Maske).
  const aktive = (data?.fields || []).filter((f) => f.enabled && (alle || !f.source));
  if (isLoading || aktive.length === 0) return null;

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      {err && (
        <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
          {err}
        </div>
      )}
      {aktive.map((f) => (
        <FeldZeile
          key={f.id} feld={f} werte={data?.values[f.key] || []}
          onSet={(werte) => speichern.mutate({ [f.key]: werte })}
        />
      ))}
    </div>
  );
}

function FeldZeile({ feld, werte, onSet }: {
  feld: Feld; werte: any[]; onSet: (werte: any[]) => void;
}) {
  const label = (
    <label className="mb-0.5 block text-xs text-muted" title={feld.description || undefined}>
      {feld.label}
      {feld.required && <span className="ml-0.5 text-red-300">*</span>}
    </label>
  );

  if (feld.kind === "select") {
    const gewaehlt = new Set(werte.map(String));
    const waehlbar = feld.dynamic_options.length
      ? feld.dynamic_options.map(([v, l]) => ({ id: v, value: v, label: l, enabled: true }))
      : feld.options.filter((o) => o.enabled || gewaehlt.has(o.value));
    return (
      <div>
        {label}
        {feld.multi ? (
          <div className="flex flex-wrap gap-1.5">
            {waehlbar.map((o) => {
              const an = gewaehlt.has(o.value);
              return (
                <button
                  key={o.id}
                  onClick={() => onSet(an
                    ? werte.filter((w) => String(w) !== o.value)
                    : [...werte, o.value])}
                  className={`rounded px-2 py-0.5 text-xs ${
                    an ? "bg-brand/25 text-ink" : "bg-surface text-muted hover:text-ink"}`}
                >
                  {o.label || o.value}
                </button>
              );
            })}
            {waehlbar.length === 0 && (
              <span className="text-xs text-muted">Keine Werte gepflegt.</span>
            )}
          </div>
        ) : (
          <select
            value={werte[0] != null ? String(werte[0]) : ""}
            onChange={(e) => onSet(e.target.value ? [e.target.value] : [])}
            className={inp}
          >
            <option value="">—</option>
            {waehlbar.map((o) => (
              <option key={o.id} value={o.value}>{o.label || o.value}</option>
            ))}
          </select>
        )}
      </div>
    );
  }

  if (feld.kind === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm" title={feld.description || undefined}>
        <input type="checkbox" checked={werte[0] === true}
          onChange={(e) => onSet(e.target.checked ? [true] : [])} />
        {feld.label}
      </label>
    );
  }

  return (
    <div>
      {label}
      {feld.multi
        ? <MehrfachText werte={werte} kind={feld.kind} onSet={onSet} />
        : <EinzelText wert={werte[0]} kind={feld.kind} onSet={(w) => onSet(w === "" ? [] : [w])} />}
    </div>
  );
}

function EinzelText({ wert, kind, onSet }: {
  wert: any; kind: string; onSet: (w: string) => void;
}) {
  const [text, setText] = useState(wert != null ? String(wert) : "");
  // Von außen geänderte Werte (anderer Nutzer, Neuladen) übernehmen.
  useEffect(() => { setText(wert != null ? String(wert) : ""); }, [wert]);
  return (
    <input
      type={kind === "number" ? "number" : kind === "date" ? "date" : "text"}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text !== (wert != null ? String(wert) : "") && onSet(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className={inp}
    />
  );
}

function MehrfachText({ werte, kind, onSet }: {
  werte: any[]; kind: string; onSet: (w: any[]) => void;
}) {
  const [neu, setNeu] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {werte.map((w, i) => (
        <span key={`${w}-${i}`} className="flex items-center gap-1 rounded bg-surface px-1.5 py-0.5 text-xs">
          {String(w)}
          <button onClick={() => onSet(werte.filter((_, j) => j !== i))}
            className="text-muted hover:text-red-300" title="Entfernen">✕</button>
        </span>
      ))}
      <input
        type={kind === "number" ? "number" : kind === "date" ? "date" : "text"}
        value={neu}
        onChange={(e) => setNeu(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && neu.trim()) {
            onSet([...werte, neu.trim()]);
            setNeu("");
          }
        }}
        placeholder="+ Enter"
        className="w-28 rounded border border-line bg-surface px-2 py-0.5 text-xs text-ink"
      />
    </div>
  );
}
