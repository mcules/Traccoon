import { useQuery } from "@tanstack/react-query";
import { api } from "../../../api";
import type { MemberLite } from "../../../api";
import { KeyValueEditor } from "../kv";
import { ACTION_SPECS, FALLBACK_SPEC, type FieldSpec } from "./actionFields";
import { agentOptions, type AgentLite } from "./agentOptions";
import type { AutoActionName } from "../types";

interface StatusLite { id: number; name: string }
interface ArtefaktStatus { key: string; label: string; category: string }
interface ArtefaktFeld { key: string; label: string; kind: string; multi: boolean; enabled: boolean }
interface ArtefaktTyp {
  key: string; name: string; icon: string;
  statuses: ArtefaktStatus[]; fields: ArtefaktFeld[];
}

/** Verschachtelte Schlüssel („to.mode") lesen/schreiben, damit Aktionen wie `notify`
 *  ihre Unterobjekte behalten. */
function get(obj: Record<string, any>, pfad: string): any {
  return pfad.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function set(obj: Record<string, any>, pfad: string, wert: any): Record<string, any> {
  const teile = pfad.split(".");
  const kopie = { ...obj };
  let cur: any = kopie;
  for (let i = 0; i < teile.length - 1; i++) {
    cur[teile[i]] = { ...(cur[teile[i]] || {}) };
    cur = cur[teile[i]];
  }
  const letzter = teile[teile.length - 1];
  if (wert === "" || wert === undefined) delete cur[letzter];
  else cur[letzter] = wert;
  return kopie;
}

/**
 * Zeigt die Einstellmöglichkeiten einer Aktion als benannte Felder — mit Auswahllisten für
 * alles, was feste Werte hat (Zustände, Board-Spalten, Agenten, Rollen). Unbekannte
 * Parameter bleiben über „Weitere Parameter" erreichbar, damit nichts verloren geht.
 */
export default function ActionParams({
  action,
  params,
  onChange,
  members,
  projectId,
  subjectKind,
}: {
  action: AutoActionName;
  params: Record<string, any>;
  onChange: (p: Record<string, any>) => void;
  members: MemberLite[];
  projectId?: number;
  /** Subjekt des Ablaufs — bestimmt, welche Zustände es überhaupt gibt. */
  subjectKind?: string;
}) {
  const spec = ACTION_SPECS[action] || FALLBACK_SPEC;
  const braucht = (q: string) => spec.fields.some((f) => f.source === q);

  const { data: agents } = useQuery({
    queryKey: ["agents", projectId ?? null],
    queryFn: () => api.get<AgentLite[]>(`/agents${projectId ? `?project_id=${projectId}` : ""}`),
    enabled: braucht("agent_role"),
    staleTime: 5 * 60_000,
  });
  // Empfänger: alle Personen, die dieser Mensch sehen darf (eigene Projekte, Platzhalter,
  // er selbst) — samt der Wege, auf denen sie erreichbar sind.
  const { data: personen } = useQuery({
    queryKey: ["users-visible"],
    queryFn: () => api.get<{ id: number; display_name: string; notify_default: string;
                             kanaele: string[] }[]>("/users/visible"),
    enabled: braucht("person"),
    staleTime: 5 * 60_000,
  });
  // Zustände des Artefakts, an dem der Ablauf hängt (Administration → Artefakte).
  const { data: typen } = useQuery({
    queryKey: ["artifact-types", subjectKind],
    queryFn: () => api.get<ArtefaktTyp[]>(`/artifact-types?subject=${subjectKind}`),
    enabled: (braucht("artifact_status") || braucht("artifact_field")) && !!subjectKind,
    staleTime: 5 * 60_000,
  });

  // Werkzeuge aus der MCP-Registry (Einstellungen → MCP-Server). Sie machen das Anbinden
  // fremder Systeme zur Konfiguration: wer einen Server einträgt, findet seine Werkzeuge
  // hier wieder — ohne dass jemand eine Aktion programmieren müsste.
  const { data: werkzeuge } = useQuery({
    queryKey: ["workflow-tools"],
    queryFn: () => api.get<{ name: string; server: string; beschreibung: string;
                             pflicht: string[] }[]>("/workflow-tools"),
    enabled: braucht("mcp_tool"),
    staleTime: 10 * 60_000,
  });

  const { data: meta } = useQuery({
    queryKey: ["meta", projectId],
    queryFn: () => api.get<{ statuses: StatusLite[] }>(`/projects/${projectId}/meta`),
    enabled: braucht("board_status") && !!projectId,
    staleTime: 5 * 60_000,
  });

  /** Enthält der Wert eine Vorlage ({{…}}), muss ein Textfeld her — eine Auswahlliste
   *  würde ihn beim ersten Öffnen des Knotens stillschweigend verschlucken. Der
   *  ausgelieferte Lebenszyklus nutzt genau das (hold_reason: {{agent.hold_hint}}). */
  const istVorlage = (v: any) => typeof v === "string" && v.includes("{{");

  const auswahl = (f: FieldSpec): [string, string][] => {
    if (f.source === "agent_role") {
      // Je Rolle EIN Eintrag, mit Herkunft der Definition, die tatsächlich greift.
      return agentOptions(agents, { empty: f.required ? "— wählen —" : "— keiner —" });
    }
    if (f.source === "board_status") {
      return [["", "—"], ...(meta?.statuses || []).map((s) => [s.name, s.name] as [string, string])];
    }
    if (f.source === "artifact_field") {
      const felder = (typen?.[0]?.fields || []).filter((x) => x.enabled);
      return felder.length
        ? felder.map((x) => [x.key, `${x.label}${x.multi ? " (mehrere)" : ""}`] as [string, string])
        : [["", "— keine Felder gepflegt —"]];
    }
    if (f.source === "artifact_status") {
      const st = typen?.[0]?.statuses || [];
      return st.length
        ? st.map((s) => [s.key, s.label] as [string, string])
        : [["", "— kein Artefakt an diesem Ablauf —"]];
    }
    if (f.source === "mcp_tool") {
      const liste = werkzeuge || [];
      return liste.length
        ? [["", "— wählen —"] as [string, string],
           ...liste.map((w) => [w.name,
             `${w.name}${w.pflicht?.length ? ` (${w.pflicht.join(", ")})` : ""}`] as [string, string])]
        : [["", "— keine MCP-Server eingetragen —"]];
    }
    if (f.source === "member") {
      return [["", "— niemand —"],
              ...members.map((m) => [String(m.user_id), m.display_name] as [string, string])];
    }
    if (f.source === "person") {
      // Projekt-Mitglieder reichen hier nicht: ein eigener, projektloser Ablauf hat gar
      // keine — die Auswahl blieb leer und man konnte niemanden benennen.
      const liste = personen || [];
      return [["", "— Betreiber —"],
              ...liste.map((u) => [String(u.id),
                `${u.display_name}${u.kanaele.length ? "" : " (kein Weg hinterlegt)"}`] as
                [string, string])];
    }
    return f.options || [];
  };

  const sichtbar = (f: FieldSpec) => {
    if (!f.showIf) return true;
    const [feld, werte] = f.showIf;
    // `__subject` prüft das Subjekt des Ablaufs statt eines Parameters.
    if (feld === "__subject") return werte.includes(subjectKind || "");
    return werte.includes(String(get(params, feld) ?? ""));
  };

  // Parameter, die kein Feld abdeckt (Altbestand oder Sonderfall) — bleiben editierbar.
  const bekannt = new Set(spec.fields.map((f) => f.key.split(".")[0]).filter(Boolean));
  const rest = Object.fromEntries(
    Object.entries(params).filter(([k]) => !bekannt.has(k)));
  const nurKv = spec.fields.length === 1 && spec.fields[0].type === "kv" && !spec.fields[0].key;

  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      {spec.summary && <p className="text-[11px] text-muted">{spec.summary}</p>}

      {spec.fields.filter(sichtbar).map((f) => {
        const wert = f.key ? get(params, f.key) : undefined;
        const aendern = (v: any) => onChange(f.key ? set(params, f.key, v) : v);
        if (f.type === "kv") {
          return (
            <div key={f.key || "kv"}>
              <div className="mb-1 text-xs font-medium text-muted">{f.label}</div>
              <KeyValueEditor
                value={f.key ? (wert || {}) : params}
                onChange={(v) => aendern(v)}
              />
              {f.hint && <div className="mt-1 text-[10px] text-muted">{f.hint}</div>}
            </div>
          );
        }
        return (
          <label key={f.key} className="block text-xs font-medium text-muted">
            {f.label}
            {f.required && <span className="text-red-400"> *</span>}
            {f.type === "select" && !istVorlage(wert) && (
              <select value={wert ?? ""} onChange={(e) => aendern(e.target.value)}
                className={`mt-1 ${inp}`}>
                {/* Ohne Vorbelegung zuerst einen leeren Eintrag, sonst zeigt das Feld einen
                    Wert an, der gar nicht gespeichert ist. */}
                {(wert ?? "") === "" && !auswahl(f).some(([k]) => k === "") && (
                  <option value="">— wählen —</option>
                )}
                {auswahl(f).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            )}
            {f.type === "select" && istVorlage(wert) && (
              <>
                <input value={wert} onChange={(e) => aendern(e.target.value)}
                  className={`mt-1 ${inp} font-mono`} />
                <span className="mt-1 block text-[10px] text-amber-400">
                  Wert kommt aus dem Kontext — zum Auswählen erst leeren.
                </span>
              </>
            )}
            {f.type === "textarea" && (
              <textarea rows={3} value={wert ?? ""} onChange={(e) => aendern(e.target.value)}
                placeholder={f.placeholder} className={`mt-1 ${inp}`} />
            )}
            {f.type === "json" && (
              <textarea rows={3} className={`mt-1 ${inp} font-mono`} placeholder={f.placeholder}
                value={typeof wert === "string" ? wert : JSON.stringify(wert ?? "", null, 2)}
                onChange={(e) => {
                  try { aendern(JSON.parse(e.target.value)); }
                  catch { aendern(e.target.value); }
                }} />
            )}
            {f.type === "number" && (
              <input type="number" value={wert ?? ""} placeholder={f.placeholder}
                onChange={(e) => aendern(e.target.value === "" ? "" : Number(e.target.value))}
                className={`mt-1 ${inp}`} />
            )}
            {f.type === "boolean" && (
              <span className="mt-1 flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={wert ?? f.default ?? false}
                  onChange={(e) => onChange(set(params, f.key, e.target.checked))} />
                {f.hint || "aktiv"}
              </span>
            )}
            {f.type === "text" && (
              <input value={wert ?? ""} placeholder={f.placeholder}
                onChange={(e) => aendern(e.target.value)} className={`mt-1 ${inp}`} />
            )}
            {f.hint && f.type !== "boolean" && (
              <span className="mt-1 block text-[10px] text-muted">{f.hint}</span>
            )}
          </label>
        );
      })}

      {spec.outcomes && <div className="text-[10px] text-amber-400">{spec.outcomes}</div>}

      {spec.fields.length === 0 && (
        <div className="text-[11px] text-muted">Diese Aktion braucht keine Einstellungen.</div>
      )}

      {!nurKv && Object.keys(rest).length > 0 && (
        <details className="rounded border border-line p-2">
          <summary className="cursor-pointer text-xs text-muted">
            Weitere Parameter ({Object.keys(rest).length})
          </summary>
          <div className="mt-2">
            <KeyValueEditor
              value={rest}
              onChange={(v) => onChange({
                ...Object.fromEntries(Object.entries(params).filter(([k]) => bekannt.has(k))),
                ...v,
              })}
            />
          </div>
        </details>
      )}
    </div>
  );
}
