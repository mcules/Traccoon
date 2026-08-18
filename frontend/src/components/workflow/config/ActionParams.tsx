import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
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

/** Read and write nested keys ("to.mode") so that actions like `notify` keep their
 *  sub-objects. */
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
 * Shows the settings of an action as named fields, with selection lists for everything that
 * has fixed values (states, board columns, agents, roles). Unknown parameters stay reachable
 * over "further parameters" so that nothing is lost.
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
  /** Subject of the flow; determines which states exist at all. */
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
  // Recipients: all people this human may see (their own projects, placeholders, themselves)
  // including the ways they are reachable on.
  const { data: personen } = useQuery({
    queryKey: ["users-visible"],
    queryFn: () => api.get<{ id: number; display_name: string; notify_default: string;
                             kanaele: string[] }[]>("/users/visible"),
    enabled: braucht("person"),
    staleTime: 5 * 60_000,
  });
  // States of the artifact the flow hangs off (Administration → Artifacts).
  const { data: typen } = useQuery({
    queryKey: ["artifact-types", subjectKind],
    queryFn: () => api.get<ArtefaktTyp[]>(`/artifact-types?subject=${subjectKind}`),
    enabled: (braucht("artifact_status") || braucht("artifact_field")) && !!subjectKind,
    staleTime: 5 * 60_000,
  });

  // Tools from the MCP registry (Settings → MCP servers). They turn connecting foreign
  // systems into configuration: whoever enters a server finds its tools here again, without
  // anybody having to program an action.
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

  /** If the value contains a template ({{…}}), a text field is needed: a selection list would
   *  swallow it silently on the first opening of the node. The shipped lifecycle uses exactly
   *  that (hold_reason: {{agent.hold_hint}}). */
  const istVorlage = (v: any) => typeof v === "string" && v.includes("{{");

  const auswahl = (f: FieldSpec): [string, string][] => {
    if (f.source === "agent_role") {
      // ONE entry per role, with the origin of the definition that actually applies.
      return agentOptions(agents, { empty: tr(f.required ? "action_params.waehlen" : "action_params.keiner") });
    }
    if (f.source === "board_status") {
      return [["", "—"], ...(meta?.statuses || []).map((s) => [s.name, s.name] as [string, string])];
    }
    if (f.source === "artifact_field") {
      const felder = (typen?.[0]?.fields || []).filter((x) => x.enabled);
      return felder.length
        ? felder.map((x) => [x.key, `${x.label}${x.multi ? " (mehrere)" : ""}`] as [string, string])
        : [["", tr("action_params.keine_felder")]];
    }
    if (f.source === "artifact_status") {
      const st = typen?.[0]?.statuses || [];
      return st.length
        ? st.map((s) => [s.key, s.label] as [string, string])
        : [["", tr("action_params.kein_artefakt")]];
    }
    if (f.source === "mcp_tool") {
      const liste = werkzeuge || [];
      return liste.length
        ? [["", tr("action_params.waehlen")] as [string, string],
           ...liste.map((w) => [w.name,
             `${w.name}${w.pflicht?.length ? ` (${w.pflicht.join(", ")})` : ""}`] as [string, string])]
        : [["", tr("action_params.keine_mcp_server")]];
    }
    if (f.source === "member") {
      return [["", "— niemand —"],
              ...members.map((m) => [String(m.user_id), m.display_name] as [string, string])];
    }
    if (f.source === "person") {
      // Project members are not enough here: an own, project-less flow has none, so the
      // selection stayed empty and nobody could be named.
      const liste = personen || [];
      return [["", "— Betreiber —"],
              ...liste.map((u) => [String(u.id),
                `${u.display_name}${u.kanaele.length ? "" : ` (${tr("action_params.kein_weg")})`}`] as
                [string, string])];
    }
    return f.options || [];
  };

  const sichtbar = (f: FieldSpec) => {
    if (!f.showIf) return true;
    const [feld, werte] = f.showIf;
    // `__subject` checks the subject of the flow instead of a parameter.
    if (feld === "__subject") return werte.includes(subjectKind || "");
    return werte.includes(String(get(params, feld) ?? ""));
  };

  // Parameters no field covers (legacy or a special case) stay editable.
  const bekannt = new Set(spec.fields.map((f) => f.key.split(".")[0]).filter(Boolean));
  const rest = Object.fromEntries(
    Object.entries(params).filter(([k]) => !bekannt.has(k)));
  const nurKv = spec.fields.length === 1 && spec.fields[0].type === "kv" && !spec.fields[0].key;

  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      {spec.summary && <p className="text-[11px] text-muted">{tr(spec.summary)}</p>}

      {spec.fields.filter(sichtbar).map((f) => {
        const wert = f.key ? get(params, f.key) : undefined;
        const aendern = (v: any) => onChange(f.key ? set(params, f.key, v) : v);
        if (f.type === "kv") {
          return (
            <div key={f.key || "kv"}>
              <div className="mb-1 text-xs font-medium text-muted">{tr(f.label)}</div>
              <KeyValueEditor
                value={f.key ? (wert || {}) : params}
                onChange={(v) => aendern(v)}
              />
              {f.hint && <div className="mt-1 text-[11px] text-muted">{tr(f.hint)}</div>}
            </div>
          );
        }
        return (
          <label key={f.key} className="block text-xs font-medium text-muted">
            {tr(f.label)}
            {f.required && <span className="text-red-400"> *</span>}
            {f.type === "select" && !istVorlage(wert) && (
              <select value={wert ?? ""} onChange={(e) => aendern(e.target.value)}
                className={`mt-1 ${inp}`}>
                {/* Ohne Vorbelegung zuerst einen leeren Eintrag, sonst zeigt das Feld einen
                    Wert an, der gar nicht gespeichert ist. */}
                {(wert ?? "") === "" && !auswahl(f).some(([k]) => k === "") && (
                  <option value="">{tr("action_params.waehlen")}</option>
                )}
                {auswahl(f).map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
              </select>
            )}
            {f.type === "select" && istVorlage(wert) && (
              <>
                <input value={wert} onChange={(e) => aendern(e.target.value)}
                  className={`mt-1 ${inp} font-mono`} />
                <span className="mt-1 block text-[11px] text-amber-400">
                  {tr("action_params.wert_aus_kontext")}
                </span>
              </>
            )}
            {f.type === "textarea" && (
              <textarea rows={3} value={wert ?? ""} onChange={(e) => aendern(e.target.value)}
                placeholder={f.placeholder ? tr(f.placeholder) : undefined} className={`mt-1 ${inp}`} />
            )}
            {f.type === "json" && (
              <textarea rows={3} className={`mt-1 ${inp} font-mono`} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                value={typeof wert === "string" ? wert : JSON.stringify(wert ?? "", null, 2)}
                onChange={(e) => {
                  try { aendern(JSON.parse(e.target.value)); }
                  catch { aendern(e.target.value); }
                }} />
            )}
            {f.type === "number" && (
              <input type="number" value={wert ?? ""} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                onChange={(e) => aendern(e.target.value === "" ? "" : Number(e.target.value))}
                className={`mt-1 ${inp}`} />
            )}
            {f.type === "boolean" && (
              <span className="mt-1 flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={wert ?? f.default ?? false}
                  onChange={(e) => onChange(set(params, f.key, e.target.checked))} />
                {f.hint ? tr(f.hint) : tr("action_params.aktiv")}
              </span>
            )}
            {f.type === "text" && (
              <input value={wert ?? ""} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                onChange={(e) => aendern(e.target.value)} className={`mt-1 ${inp}`} />
            )}
            {f.hint && f.type !== "boolean" && (
              <span className="mt-1 block text-[11px] text-muted">{tr(f.hint)}</span>
            )}
          </label>
        );
      })}

      {spec.outcomes && <div className="text-[11px] text-amber-400">{tr(spec.outcomes)}</div>}

      {spec.fields.length === 0 && (
        <div className="text-[11px] text-muted">{tr("action_params.diese_aktion_braucht_keine_einstellungen")}</div>
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
