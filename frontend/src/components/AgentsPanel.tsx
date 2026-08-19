import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

interface Agent {
  id: number; role: string; display_name: string; system_prompt: string;
  provider: string; model: string; token_name: string; effort: string;
  fallback: string | null; fallback_model: string; fallback_token_name: string;
  temperature: number; max_tokens: number; max_context_tokens: number | null;
  max_turns_planning: number; max_turns_execution: number;
  can_code: boolean; can_read_code: boolean; can_delegate: boolean; web_search: boolean;
  learns: boolean;
  allowed_tools: string[]; allowed_skills: string[]; autoload_skills: string[]; delegate_to: string[]; active: boolean;
  project_id: number | null; origin_agent_id: number | null; customized: boolean;
}

const PROVIDERS = ["claude_code", "codex", "openai"];
const EMPTY: Partial<Agent> = {
  role: "", display_name: "", system_prompt: "", provider: "claude_code", model: "", token_name: "", effort: "",
  fallback: null, fallback_model: "", fallback_token_name: "", max_context_tokens: null,
  temperature: 0.3, max_tokens: 8192, max_turns_planning: 10, max_turns_execution: 80,
  can_code: false, can_read_code: false, can_delegate: false, web_search: false, learns: true,
  allowed_tools: [], allowed_skills: [], autoload_skills: [], delegate_to: [], active: true,
};

export default function AgentsPanel({ projectId }: { projectId?: number } = {}) {
  const qc = useQueryClient();
  const key = projectId ? ["agents", projectId] : ["agents"];
  const { data: allAgents } = useQuery({
    queryKey: key,
    queryFn: () => api.get<Agent[]>(`/agents${projectId ? `?project_id=${projectId}` : ""}`),
  });
  const { data: tokens } = useQuery({ queryKey: ["provider-tokens"], queryFn: () => api.get<any[]>("/me/provider-tokens") });
  const { data: models } = useQuery({ queryKey: ["provider-models"], queryFn: () => api.get<any[]>("/providers/models") });
  const { data: skills } = useQuery({ queryKey: ["skills"], queryFn: () => api.get<any[]>("/skills") });
  const { data: mcpServers } = useQuery({ queryKey: ["mcp-servers"], queryFn: () => api.get<any[]>("/mcp-servers") });
  const [edit, setEdit] = useState<Partial<Agent> | null>(null);
  const [showAdv, setShowAdv] = useState(false);
  const inv = () => qc.invalidateQueries({ queryKey: key });
  const tokensFor = (p?: string) => (tokens || []).filter((t) => t.provider === p);
  // Deactivated models (the endpoint no longer knows them) drop out of the suggestions.
  const modelsFor = (p?: string) => (models || []).filter((m) => m.provider === p && m.enabled !== false);
  // What the model carries and how fast it writes: the choice is decided by that, not by the
  // name. With local models it is the only distinction (the price is 0).
  const modelLabel = (m: any) => {
    const teile = [m.display_name || m.model];
    if (m.context_tokens) teile.push(`${Math.round(m.context_tokens / 1000)}k Kontext`);
    if (m.speed_tps) teile.push(`≈${m.speed_tps} t/s`);
    return teile.join(" · ");
  };

  // In project mode show only the project-owned agents; project-less ones are "inherited".
  const agents = projectId ? (allAgents || []).filter((a) => a.project_id === projectId) : allAgents;
  const inherited = projectId ? (allAgents || []).filter((a) => a.project_id == null) : [];
  const newAgent = () => setEdit({ ...EMPTY, project_id: projectId ?? null });

  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const save = useMutation({
    mutationFn: (a: Partial<Agent>) => a.id ? api.put(`/agents/${a.id}`, a) : api.post("/agents", a),
    onSuccess: () => { setEdit(null); setErr(""); inv(); }, onError: fail,
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/agents/${id}`), onSuccess: inv, onError: fail });
  const seed = useMutation({ mutationFn: () => api.post("/agents/seed-defaults"), onSuccess: inv, onError: fail });
  const loadInto = useMutation({
    mutationFn: (id: number) => api.post(`/agents/${id}/copy-to-project`, { project_id: projectId }),
    onSuccess: inv, onError: fail,
  });
  const syncLinked = useMutation({
    mutationFn: (id: number) => api.post<any>(`/agents/${id}/sync-linked`),
    onSuccess: (r: any) => { setNote(`${r?.synced ?? 0} verknüpfte Kopie(n) aktualisiert.`); setTimeout(() => setNote(""), 3000); inv(); },
    onError: fail,
  });
  const fetchModels = useMutation({
    mutationFn: () => api.post<Record<string, any>>("/providers/models/fetch"),
    onSuccess: (r) => {
      const parts = Object.entries(r).map(([p, v]: [string, any]) =>
        v.error ? `${p}: Fehler` : `${p}: ${v.total} (${v.added} neu)`);
      setNote(parts.length ? `Modelle aktualisiert — ${parts.join(", ")}` : "Keine Provider-Tokens hinterlegt.");
      setTimeout(() => setNote(""), 5000);
      qc.invalidateQueries({ queryKey: ["provider-models"] });
    },
    onError: fail,
  });

  return (
    <div>
      {err && <div className="mb-2 rounded border border-red-400/40 bg-red-400/10 px-2 py-1 text-sm text-red-400">{err}</div>}
      {note && <div className="mb-2 text-sm text-green-400">{note}</div>}
      <div className="mb-3 flex items-center gap-2">
        <p className="flex-1 text-sm text-muted">
          {tr(projectId ? "agents_panel.einleitung_projekt" : "agents_panel.einleitung_eigene")}</p>
        {!projectId && (!allAgents || allAgents.length === 0) && (
          <button onClick={() => seed.mutate()} className="rounded border border-line px-3 py-1.5 text-sm">
            {tr("agents_panel.standard_agenten_anlegen")}</button>
        )}
        <button onClick={() => fetchModels.mutate()} disabled={fetchModels.isPending}
          title={tr("agents_panel.verfuegbare_modelle_live_bei_den_provide")}
          className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink disabled:opacity-50">
          {fetchModels.isPending ? tr("common.laedt") : `↻ ${tr("agents_panel.modelle_abrufen")}`}</button>
        <button onClick={newAgent} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          + Agent</button>
      </div>

      {projectId && inherited.length > 0 && (agents?.length ?? 0) === 0 && (
        <p className="mb-2 text-xs text-muted">Aktuell erben alle Rollen deine persönlichen Agenten
          ({inherited.map((a) => a.role).join(", ")}). Lege hier einen an, um sie fürs Projekt zu überschreiben.</p>
      )}

      <div className="space-y-2">
        {agents?.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-line bg-card p-2.5 text-sm">
            <span className="font-mono font-medium">{a.role}</span>
            <span className="text-xs text-muted">{a.provider}{a.model ? ` · ${a.model}` : ""}</span>
            <div className="flex gap-1">
              {a.can_code && <span className="rounded bg-surface px-1 text-xs">code</span>}
              {a.can_read_code && <span className="rounded bg-surface px-1 text-xs">read</span>}
              {a.can_delegate && <span className="rounded bg-surface px-1 text-xs">delegate</span>}
              {a.web_search && <span className="rounded bg-surface px-1 text-xs">web</span>}
              {a.learns && <span className="rounded bg-surface px-1 text-xs" title={tr("agents_panel.liest_das_gedaechtnis_und_lernt_aus_jede")}>lernt</span>}
            </div>
            {a.origin_agent_id && <span className="rounded bg-surface px-1 text-xs">{tr(a.customized ? "agents_panel.bearbeitet" : "agents_panel.verknuepft")}</span>}
            <div className="hidden flex-1 sm:block" />
            {!projectId && <button onClick={() => syncLinked.mutate(a.id)} className="text-xs text-muted hover:text-ink" title={tr("agents_panel.verknuepfte_projekt_kopien_aktualisieren")}>{tr("agents_panel.verknuepfte")}</button>}
            <button onClick={() => setEdit(a)} className="text-brand">{tr("common.bearbeiten")}</button>
            <button onClick={() => del.mutate(a.id)} className="text-muted hover:text-red-400">{tr("common.loeschen_klein")}</button>
          </div>
        ))}
      </div>

      {/* Projekt-Modus: geerbte (globale) Agenten mit „In Projekt laden" */}
      {projectId && inherited.map((a) => (
        <div key={`inh-${a.id}`} className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-dashed border-line px-2.5 py-1.5 text-sm text-muted">
          <span className="font-mono">{a.role}</span><span className="text-xs">geerbt (global)</span>
          <div className="flex-1" />
          <button onClick={() => loadInto.mutate(a.id)} className="text-brand hover:underline">{tr("agents_panel.in_projekt_laden")}</button>
        </div>
      ))}

      {edit && (
        <div className="fixed inset-0 z-30 flex justify-end bg-black/40" onClick={() => setEdit(null)}>
          <div className="h-full w-full max-w-lg overflow-y-auto bg-card p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-medium">{edit.id ? "Agent bearbeiten" : "Neuer Agent"}</h3>
              <button onClick={() => setEdit(null)} className="text-muted">✕</button>
            </div>
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <F label={tr("agents_panel.rolle_kennung")}><input value={edit.role || ""} onChange={(e) => setEdit({ ...edit, role: e.target.value })} className={inp} /></F>
                <F label={tr("agents_panel.anzeigename")}><input value={edit.display_name || ""} onChange={(e) => setEdit({ ...edit, display_name: e.target.value })} className={inp} /></F>
              </div>

              <Sec title={tr("agents_panel.modell_token")}>
                <div className="flex gap-2">
                  <F label={tr("agents_panel.provider")}><select value={edit.provider} onChange={(e) => setEdit({ ...edit, provider: e.target.value, model: "", token_name: "" })} className={inp}>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</select></F>
                  <F label={tr("agents_panel.modell_leer_default")}>
                    <input list="models-primary" value={edit.model || ""} onChange={(e) => setEdit({ ...edit, model: e.target.value })} placeholder="claude-sonnet-4-5" className={inp} />
                    <datalist id="models-primary">{modelsFor(edit.provider).map((m) => <option key={m.model} value={m.model}>{modelLabel(m)}</option>)}</datalist>
                  </F>
                  <F label={tr("agents_panel.token")}><select value={edit.token_name || ""} onChange={(e) => setEdit({ ...edit, token_name: e.target.value })} className={inp}>
                    <option value="">{tr("agents_panel.standard")}</option>
                    {tokensFor(edit.provider).map((t) => <option key={t.id} value={t.name}>{t.name}{t.is_default ? " (Std.)" : ""}</option>)}
                  </select></F>
                </div>
                <div className="mt-2 flex gap-2">
                  <F label={tr("agents_panel.fallback_provider")}><select value={edit.fallback || ""} onChange={(e) => setEdit({ ...edit, fallback: e.target.value || null, fallback_model: "", fallback_token_name: "" })} className={inp}>
                    <option value="">{tr("agents_panel.kein")}</option>
                    {PROVIDERS.map((p) => <option key={p}>{p}</option>)}
                  </select></F>
                  <F label={tr("agents_panel.fallback_modell")}>
                    <input list="models-fallback" value={edit.fallback_model || ""} onChange={(e) => setEdit({ ...edit, fallback_model: e.target.value })} disabled={!edit.fallback} className={inp} />
                    <datalist id="models-fallback">{modelsFor(edit.fallback || "").map((m) => <option key={m.model} value={m.model}>{modelLabel(m)}</option>)}</datalist>
                  </F>
                  <F label={tr("agents_panel.fallback_token")}><select value={edit.fallback_token_name || ""} onChange={(e) => setEdit({ ...edit, fallback_token_name: e.target.value })} disabled={!edit.fallback} className={inp}>
                    <option value="">{tr("agents_panel.standard")}</option>
                    {tokensFor(edit.fallback || "").map((t) => <option key={t.id} value={t.name}>{t.name}</option>)}
                  </select></F>
                </div>
              </Sec>

              <Sec title={tr("agents_panel.system_prompt")}>
                <textarea rows={5} value={edit.system_prompt || ""} onChange={(e) => setEdit({ ...edit, system_prompt: e.target.value })} className={inp} />
              </Sec>

              <Sec title={tr("agents_panel.skills")}>
                <SkillPicker skills={skills || []}
                  allowed={edit.allowed_skills || []} autoload={edit.autoload_skills || []}
                  onChange={(allowed, autoload) => setEdit({ ...edit, allowed_skills: allowed, autoload_skills: autoload })} />
              </Sec>

              <Sec title="MCP-Server">
                {edit.id
                  ? <AgentMcp agentId={edit.id} servers={mcpServers || []} />
                  : <div className="text-xs text-muted">{tr("agents_panel.erst_speichern_dann_mcp_server_freigeben")}</div>}
              </Sec>

              <button onClick={() => setShowAdv(!showAdv)} className="text-xs text-muted hover:text-ink">
                {showAdv ? "▾" : "▸"} Erweitert (Fähigkeiten, Limits)
              </button>
              {showAdv && (
                <div className="space-y-2 rounded border border-line bg-surface/50 p-2">
                  <div className="flex flex-wrap gap-3">
                    {(["can_code", "can_read_code", "can_delegate", "web_search", "learns"] as const).map((c) => (
                      <label key={c} className="flex items-center gap-1.5 text-xs">
                        <input type="checkbox" checked={!!edit[c]} onChange={(e) => setEdit({ ...edit, [c]: e.target.checked })} /> {c}
                      </label>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <F label="max_turns Plan"><input type="number" value={edit.max_turns_planning} onChange={(e) => setEdit({ ...edit, max_turns_planning: +e.target.value })} className={inp} /></F>
                    <F label="max_turns Exec"><input type="number" value={edit.max_turns_execution} onChange={(e) => setEdit({ ...edit, max_turns_execution: +e.target.value })} className={inp} /></F>
                    <F label="max_tokens"><input type="number" value={edit.max_tokens} onChange={(e) => setEdit({ ...edit, max_tokens: +e.target.value })} className={inp} /></F>
                  </div>
                  <F label={tr("agents_panel.denk_tiefe_leer_standard_low_medium_high")}>
                    <input value={edit.effort || ""} onChange={(e) => setEdit({ ...edit, effort: e.target.value })} placeholder="leer = Default" className={inp} />
                  </F>
                </div>
              )}

              <button onClick={() => save.mutate(edit)} className="w-full rounded bg-brand py-2 text-white">{tr("agents_panel.speichern")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const inp = "mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink";
function F({ label, children }: { label: string; children: any }) {
  return <label className="block flex-1 text-xs text-muted">{label}{children}</label>;
}
function Sec({ title, children }: { title: string; children: any }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</div>
      {children}
    </div>
  );
}

/** Choose skills (which the agent gets) plus an auto-load toggle per skill. */
function SkillPicker({ skills, allowed, autoload, onChange }: {
  skills: any[]; allowed: string[]; autoload: string[];
  onChange: (allowed: string[], autoload: string[]) => void;
}) {
  const toggle = (key: string) => {
    const has = allowed.includes(key);
    const nextAllowed = has ? allowed.filter((k) => k !== key) : [...allowed, key];
    const nextAuto = has ? autoload.filter((k) => k !== key) : autoload;
    onChange(nextAllowed, nextAuto);
  };
  const toggleAuto = (key: string) => {
    onChange(allowed, autoload.includes(key) ? autoload.filter((k) => k !== key) : [...autoload, key]);
  };
  return (
    <div>
      <div className="text-xs text-muted">{tr("agents_panel.skills_der_agent_bekommt_sie_auto_immer_")}</div>
      <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded border border-line bg-surface p-2">
        {skills.length === 0 && <div className="text-xs text-muted">{tr("agents_panel.keine_skills_angelegt")}</div>}
        {skills.map((s) => {
          const on = allowed.includes(s.key);
          return (
            <div key={s.key} className="flex items-center gap-2 text-sm">
              <label className="flex flex-1 items-center gap-1.5">
                <input type="checkbox" checked={on} onChange={() => toggle(s.key)} />
                <span>{s.name} <span className="font-mono text-xs text-muted">{s.key}</span></span>
              </label>
              {on && (
                <label className="flex items-center gap-1 text-xs text-muted">
                  <input type="checkbox" checked={autoload.includes(s.key)} onChange={() => toggleAuto(s.key)} /> auto
                </label>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Per agent: MCP instances (choose a server, then fill in the variables). */
function AgentMcp({ agentId, servers }: { agentId: number; servers: any[] }) {
  const qc = useQueryClient();
  const { data: instances } = useQuery({
    queryKey: ["mcp-instances", agentId], queryFn: () => api.get<any[]>(`/agents/${agentId}/mcp-instances`),
  });
  const [serverId, setServerId] = useState<number | "">("");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const inv = () => qc.invalidateQueries({ queryKey: ["mcp-instances", agentId] });
  const server = servers.find((s) => s.id === serverId);
  const add = async () => {
    if (!serverId) return;
    await api.post(`/agents/${agentId}/mcp-instances`, { server_id: serverId, name, values });
    setServerId(""); setName(""); setValues({}); inv();
  };
  const del = async (id: number) => { await api.del(`/agents/${agentId}/mcp-instances/${id}`); inv(); };
  const srvName = (id: number) => servers.find((s) => s.id === id)?.name || `#${id}`;

  return (
    <div>
      <div className="text-xs text-muted">MCP-Server (Instanzen dieses Agenten)</div>
      <div className="mt-1 space-y-1">
        {instances?.map((i) => (
          <div key={i.id} className="flex items-center gap-2 text-sm">
            <span className="font-mono text-xs">{srvName(i.server_id)}</span>
            <span>{i.name}</span>
            {i.set_keys?.length > 0 && <span className="text-xs text-muted">({i.set_keys.length} Variable(n))</span>}
            <div className="flex-1" />
            <button onClick={() => del(i.id)} className="text-muted hover:text-red-400">✕</button>
          </div>
        ))}
        {instances?.length === 0 && <div className="text-xs text-muted">{tr("agents_panel.keine_mcp_server_freigegeben")}</div>}
      </div>
      <div className="mt-2 rounded border border-line bg-surface p-2">
        <select value={serverId} onChange={(e) => { setServerId(e.target.value ? +e.target.value : ""); setValues({}); }}
          className="w-full rounded border border-line bg-card px-2 py-1 text-sm text-ink">
          <option value="">{tr("agents_panel.mcp_server_waehlen")}</option>
          {servers.filter((s) => s.transport !== "stdio").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {server && (
          <div className="mt-2 space-y-1">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("agents_panel.instanz_name_z_b_dkb")}
              className="w-full rounded border border-line bg-card px-2 py-1 text-sm" />
            {(server.variables || []).map((v: any) => (
              <input key={v.key} type={v.secret ? "password" : "text"}
                value={values[v.key] || ""} onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
                placeholder={`${v.label || v.key}${v.required ? " *" : ""}`}
                className="w-full rounded border border-line bg-card px-2 py-1 text-sm" />
            ))}
            {(server.variables || []).length === 0 && <div className="text-xs text-muted">{tr("agents_panel.dieser_server_braucht_keine_variablen")}</div>}
            <button onClick={add} className="rounded bg-brand px-3 py-1 text-xs text-white">{tr("agents_panel.instanz_hinzufuegen")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
