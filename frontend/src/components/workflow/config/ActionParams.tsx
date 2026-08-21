import { useQuery } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { api } from "../../../api";
import type { MemberLite } from "../../../api";
import { KeyValueEditor } from "../kv";
import { ACTION_SPECS, FALLBACK_SPEC, type FieldSpec } from "./actionFields";
import { agentOptions, type AgentLite } from "./agentOptions";
import type { AutoActionName } from "../types";

interface StatusLite { id: number; name: string }
interface ArtifactStatus { key: string; label: string; category: string }
interface ArtifactField { key: string; label: string; kind: string; multi: boolean; enabled: boolean }
interface ArtifactKind {
  key: string; name: string; icon: string;
  statuses: ArtifactStatus[]; fields: ArtifactField[];
}

/** Read and write nested keys ("to.mode") so that actions like `notify` keep their
 *  sub-objects. */
function get(obj: Record<string, any>, path: string): any {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function set(obj: Record<string, any>, path: string, value: any): Record<string, any> {
  const parts = path.split(".");
  const copy = { ...obj };
  let cur: any = copy;
  for (let i = 0; i < parts.length - 1; i++) {
    cur[parts[i]] = { ...(cur[parts[i]] || {}) };
    cur = cur[parts[i]];
  }
  const last = parts[parts.length - 1];
  if (value === "" || value === undefined) delete cur[last];
  else cur[last] = value;
  return copy;
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
  const needs = (q: string) => spec.fields.some((f) => f.source === q);

  const { data: agents } = useQuery({
    queryKey: ["agents", projectId ?? null],
    queryFn: () => api.get<AgentLite[]>(`/agents${projectId ? `?project_id=${projectId}` : ""}`),
    enabled: needs("agent_role"),
    staleTime: 5 * 60_000,
  });
  // Recipients: all people this human may see (their own projects, placeholders, themselves)
  // including the ways they are reachable on.
  const { data: persons } = useQuery({
    queryKey: ["users-visible"],
    queryFn: () => api.get<{ id: number; display_name: string; notify_default: string;
                             channels: string[] }[]>("/users/visible"),
    enabled: needs("person"),
    staleTime: 5 * 60_000,
  });
  // States of the artifact the flow hangs off (Administration → Artifacts).
  const { data: types } = useQuery({
    queryKey: ["artifact-types", subjectKind],
    queryFn: () => api.get<ArtifactKind[]>(`/artifact-types?subject=${subjectKind}`),
    enabled: (needs("artifact_status") || needs("artifact_field")) && !!subjectKind,
    staleTime: 5 * 60_000,
  });

  // Tools from the MCP registry (Settings → MCP servers). They turn connecting foreign
  // systems into configuration: whoever enters a server finds its tools here again, without
  // anybody having to program an action.
  const { data: tools } = useQuery({
    queryKey: ["workflow-tools"],
    queryFn: () => api.get<{ name: string; server: string; description: string;
                             required: string[] }[]>("/workflow-tools"),
    enabled: needs("mcp_tool"),
    staleTime: 10 * 60_000,
  });

  const { data: meta } = useQuery({
    queryKey: ["meta", projectId],
    queryFn: () => api.get<{ statuses: StatusLite[] }>(`/projects/${projectId}/meta`),
    enabled: needs("board_status") && !!projectId,
    staleTime: 5 * 60_000,
  });

  /** If the value contains a template ({{…}}), a text field is needed: a selection list would
   *  swallow it silently on the first opening of the node. The shipped lifecycle uses exactly
   *  that (hold_reason: {{agent.hold_hint}}). */
  const isTemplate = (v: any) => typeof v === "string" && v.includes("{{");

  const selection = (f: FieldSpec): [string, string][] => {
    if (f.source === "agent_role") {
      // ONE entry per role, with the origin of the definition that actually applies.
      return agentOptions(agents, { empty: tr(f.required ? "action_params.waehlen" : "action_params.keiner") });
    }
    if (f.source === "board_status") {
      return [["", "—"], ...(meta?.statuses || []).map((s) => [s.name, s.name] as [string, string])];
    }
    if (f.source === "artifact_field") {
      const fields = (types?.[0]?.fields || []).filter((x) => x.enabled);
      return fields.length
        ? fields.map((x) => [x.key, `${x.label}${x.multi ? " (mehrere)" : ""}`] as [string, string])
        : [["", tr("action_params.keine_felder")]];
    }
    if (f.source === "artifact_status") {
      const st = types?.[0]?.statuses || [];
      return st.length
        ? st.map((s) => [s.key, s.label] as [string, string])
        : [["", tr("action_params.kein_artefakt")]];
    }
    if (f.source === "mcp_tool") {
      const listing = tools || [];
      return listing.length
        ? [["", tr("action_params.waehlen")] as [string, string],
           ...listing.map((w) => [w.name,
             `${w.name}${w.required?.length ? ` (${w.required.join(", ")})` : ""}`] as [string, string])]
        : [["", tr("action_params.keine_mcp_server")]];
    }
    if (f.source === "member") {
      return [["", "— niemand —"],
              ...members.map((m) => [String(m.user_id), m.display_name] as [string, string])];
    }
    if (f.source === "person") {
      // Project members are not enough here: an own, project-less flow has none, so the
      // selection stayed empty and nobody could be named.
      const listing = persons || [];
      return [["", "— Betreiber —"],
              ...listing.map((u) => [String(u.id),
                `${u.display_name}${u.channels.length ? "" : ` (${tr("action_params.kein_weg")})`}`] as
                [string, string])];
    }
    return f.options || [];
  };

  const visible = (f: FieldSpec) => {
    if (!f.showIf) return true;
    const [field, values] = f.showIf;
    // `__subject` checks the subject of the flow instead of a parameter.
    if (field === "__subject") return values.includes(subjectKind || "");
    return values.includes(String(get(params, field) ?? ""));
  };

  // Parameters no field covers (legacy or a special case) stay editable.
  const known = new Set(spec.fields.map((f) => f.key.split(".")[0]).filter(Boolean));
  const remainder = Object.fromEntries(
    Object.entries(params).filter(([k]) => !known.has(k)));
  const onlyKv = spec.fields.length === 1 && spec.fields[0].type === "kv" && !spec.fields[0].key;

  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      {spec.summary && <p className="text-[11px] text-muted">{tr(spec.summary)}</p>}

      {spec.fields.filter(visible).map((f) => {
        const value = f.key ? get(params, f.key) : undefined;
        const update = (v: any) => onChange(f.key ? set(params, f.key, v) : v);
        if (f.type === "kv") {
          return (
            <div key={f.key || "kv"}>
              <div className="mb-1 text-xs font-medium text-muted">{tr(f.label)}</div>
              <KeyValueEditor
                value={f.key ? (value || {}) : params}
                onChange={(v) => update(v)}
              />
              {f.hint && <div className="mt-1 text-[11px] text-muted">{tr(f.hint)}</div>}
            </div>
          );
        }
        return (
          <label key={f.key} className="block text-xs font-medium text-muted">
            {tr(f.label)}
            {f.required && <span className="text-red-400"> *</span>}
            {f.type === "select" && !isTemplate(value) && (
              <select value={value ?? ""} onChange={(e) => update(e.target.value)}
                className={`mt-1 ${inp}`}>
                {/* Ohne Vorbelegung zuerst einen leeren Eintrag, sonst zeigt das Feld einen
                    Wert an, der gar nicht gespeichert ist. */}
                {(value ?? "") === "" && !selection(f).some(([k]) => k === "") && (
                  <option value="">{tr("action_params.waehlen")}</option>
                )}
                {selection(f).map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
              </select>
            )}
            {f.type === "select" && isTemplate(value) && (
              <>
                <input value={value} onChange={(e) => update(e.target.value)}
                  className={`mt-1 ${inp} font-mono`} />
                <span className="mt-1 block text-[11px] text-amber-400">
                  {tr("action_params.wert_aus_kontext")}
                </span>
              </>
            )}
            {f.type === "textarea" && (
              <textarea rows={3} value={value ?? ""} onChange={(e) => update(e.target.value)}
                placeholder={f.placeholder ? tr(f.placeholder) : undefined} className={`mt-1 ${inp}`} />
            )}
            {f.type === "json" && (
              <textarea rows={3} className={`mt-1 ${inp} font-mono`} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                value={typeof value === "string" ? value : JSON.stringify(value ?? "", null, 2)}
                onChange={(e) => {
                  try { update(JSON.parse(e.target.value)); }
                  catch { update(e.target.value); }
                }} />
            )}
            {f.type === "number" && (
              <input type="number" value={value ?? ""} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                onChange={(e) => update(e.target.value === "" ? "" : Number(e.target.value))}
                className={`mt-1 ${inp}`} />
            )}
            {f.type === "boolean" && (
              <span className="mt-1 flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={value ?? f.default ?? false}
                  onChange={(e) => onChange(set(params, f.key, e.target.checked))} />
                {f.hint ? tr(f.hint) : tr("action_params.aktiv")}
              </span>
            )}
            {f.type === "text" && (
              <input value={value ?? ""} placeholder={f.placeholder ? tr(f.placeholder) : undefined}
                onChange={(e) => update(e.target.value)} className={`mt-1 ${inp}`} />
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

      {!onlyKv && Object.keys(remainder).length > 0 && (
        <details className="rounded border border-line p-2">
          <summary className="cursor-pointer text-xs text-muted">
            Weitere Parameter ({Object.keys(remainder).length})
          </summary>
          <div className="mt-2">
            <KeyValueEditor
              value={remainder}
              onChange={(v) => onChange({
                ...Object.fromEntries(Object.entries(params).filter(([k]) => known.has(k))),
                ...v,
              })}
            />
          </div>
        </details>
      )}
    </div>
  );
}
