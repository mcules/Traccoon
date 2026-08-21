import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Actions, Area, Dialog, DialogFoot, Errorrow, ICON, IconButton, Listing, ListenLine, DeleteDialog, BUTTON, BUTTON_SMALL, BUTTON_TEXT} from "./ui";

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
  const [deleteAgent, setDeleteAgent] = useState<Agent | null>(null);
  const [showAdv, setShowAdv] = useState(false);
  const inv = () => qc.invalidateQueries({ queryKey: key });
  const tokensFor = (p?: string) => (tokens || []).filter((t) => t.provider === p);
  // Deactivated models (the endpoint no longer knows them) drop out of the suggestions.
  const modelsFor = (p?: string) => (models || []).filter((m) => m.provider === p && m.enabled !== false);
  // What the model carries and how fast it writes: the choice is decided by that, not by the
  // name. With local models it is the only distinction (the price is 0).
  const modelLabel = (m: any) => {
    const parts = [m.display_name || m.model];
    if (m.context_tokens) parts.push(`${Math.round(m.context_tokens / 1000)}k Kontext`);
    if (m.speed_tps) parts.push(`≈${m.speed_tps} t/s`);
    return parts.join(" · ");
  };

  // In project mode show only the project-owned agents; project-less ones are "inherited".
  const agents = projectId ? (allAgents || []).filter((a) => a.project_id === projectId) : allAgents;
  const inherited = projectId ? (allAgents || []).filter((a) => a.project_id == null) : [];
  const newAgent = () => setEdit({ ...EMPTY, project_id: projectId ?? null });

  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const save = useMutation({
    mutationFn: (a: Partial<Agent>) => a.id ? api.put(`/agents/${a.id}`, a) : api.post("/agents", a),
    onSuccess: () => { setEdit(null); setErr(""); inv(); }, onError: fail,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/agents/${id}`),
    onSuccess: () => { setDeleteAgent(null); inv(); }, onError: fail });
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
    <Area>
      <Errorrow text={err} />
      {note && <div className="mb-2 text-sm text-green-400">{note}</div>}
      <div className="mb-3 flex items-center gap-2">
        <p className="flex-1 text-sm text-muted">
          {tr(projectId ? "agents_panel.agents_project_provider_model" : "agents_panel.agent_roles_prompt_model")}</p>
        {!projectId && (!allAgents || allAgents.length === 0) && (
          <button onClick={() => seed.mutate()} className={BUTTON.secondary}>
            {tr("agents_panel.create_default_agents")}</button>
        )}
        <button onClick={() => fetchModels.mutate()} disabled={fetchModels.isPending}
          title={tr("agents_panel.fetch_available_models_live_from_the_provider")}
          className={BUTTON.secondary}>
          {fetchModels.isPending ? tr("common.loading") : `↻ ${tr("agents_panel.fetch_models")}`}</button>
        <button onClick={newAgent} className={BUTTON.primary}>
          + Agent</button>
      </div>

      {projectId && inherited.length > 0 && (agents?.length ?? 0) === 0 && (
        <p className="mb-2 text-xs text-muted">Aktuell erben alle Rollen deine persönlichen Agenten
          ({inherited.map((a) => a.role).join(", ")}). Lege hier einen an, um sie fürs Projekt zu überschreiben.</p>
      )}

      <Listing>
        {agents?.map((a) => (
          <ListenLine key={a.id}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono font-medium">{a.role}</span>
            <span className="text-xs text-muted">{a.provider}{a.model ? ` · ${a.model}` : ""}</span>
            <div className="flex gap-1">
              {a.can_code && <span className="rounded bg-surface px-1 text-xs">code</span>}
              {a.can_read_code && <span className="rounded bg-surface px-1 text-xs">read</span>}
              {a.can_delegate && <span className="rounded bg-surface px-1 text-xs">delegate</span>}
              {a.web_search && <span className="rounded bg-surface px-1 text-xs">web</span>}
              {a.learns && <span className="rounded bg-surface px-1 text-xs" title={tr("agents_panel.reads_the_memory_and_learns_from_every_run")}>lernt</span>}
            </div>
            {a.origin_agent_id && <span className="rounded bg-surface px-1 text-xs">{tr(a.customized ? "agents_panel.edited" : "agents_panel.linked")}</span>}
            <div className="hidden flex-1 sm:block" />
            <Actions>
              {!projectId && (
                <IconButton icon="🔗" title={tr("agents_panel.update_linked_project_copies")}
                  onClick={() => syncLinked.mutate(a.id)} disabled={syncLinked.isPending} />
              )}
              <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => setEdit(a)} />
              <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => setDeleteAgent(a)} />
            </Actions>
            </div>
          </ListenLine>
        ))}
      </Listing>

      {/* Projekt-Modus: geerbte (globale) Agenten mit „In Projekt laden" */}
      {projectId && inherited.map((a) => (
        <div key={`inh-${a.id}`} className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-dashed border-line px-2.5 py-1.5 text-sm text-muted">
          <span className="font-mono">{a.role}</span><span className="text-xs">geerbt (global)</span>
          <div className="flex-1" />
          <button onClick={() => loadInto.mutate(a.id)} className={BUTTON_TEXT.secondary}>{tr("agents_panel.load_project")}</button>
        </div>
      ))}

      {edit && (
        <Dialog wide title={tr(edit.id ? "agents_panel.edit_agent" : "agents_panel.new_agent")}
          onClose={() => setEdit(null)}
          foot={<DialogFoot onCancel={() => setEdit(null)} runs={save.isPending}
            disabled={!edit.role?.trim()} onSave={() => save.mutate(edit)}
            saveText={edit.id ? undefined : tr("common.create")} />}>
          <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <F label={tr("agents_panel.role_key")}><input value={edit.role || ""} onChange={(e) => setEdit({ ...edit, role: e.target.value })} className={inp} /></F>
                <F label={tr("agents_panel.display_name")}><input value={edit.display_name || ""} onChange={(e) => setEdit({ ...edit, display_name: e.target.value })} className={inp} /></F>
              </div>

              <Sec title={tr("agents_panel.model_token")}>
                <div className="flex gap-2">
                  <F label={tr("agents_panel.provider")}><select value={edit.provider} onChange={(e) => setEdit({ ...edit, provider: e.target.value, model: "", token_name: "" })} className={inp}>{PROVIDERS.map((p) => <option key={p}>{p}</option>)}</select></F>
                  <F label={tr("agents_panel.model_empty_default")}>
                    <input list="models-primary" value={edit.model || ""} onChange={(e) => setEdit({ ...edit, model: e.target.value })} placeholder="claude-sonnet-4-5" className={inp} />
                    <datalist id="models-primary">{modelsFor(edit.provider).map((m) => <option key={m.model} value={m.model}>{modelLabel(m)}</option>)}</datalist>
                  </F>
                  <F label={tr("agents_panel.token")}><select value={edit.token_name || ""} onChange={(e) => setEdit({ ...edit, token_name: e.target.value })} className={inp}>
                    <option value="">{tr("agents_panel.default")}</option>
                    {tokensFor(edit.provider).map((t) => <option key={t.id} value={t.name}>{t.name}{t.is_default ? " (Std.)" : ""}</option>)}
                  </select></F>
                </div>
                <div className="mt-2 flex gap-2">
                  <F label={tr("agents_panel.fallback_provider")}><select value={edit.fallback || ""} onChange={(e) => setEdit({ ...edit, fallback: e.target.value || null, fallback_model: "", fallback_token_name: "" })} className={inp}>
                    <option value="">{tr("agents_panel.none")}</option>
                    {PROVIDERS.map((p) => <option key={p}>{p}</option>)}
                  </select></F>
                  <F label={tr("agents_panel.fallback_model")}>
                    <input list="models-fallback" value={edit.fallback_model || ""} onChange={(e) => setEdit({ ...edit, fallback_model: e.target.value })} disabled={!edit.fallback} className={inp} />
                    <datalist id="models-fallback">{modelsFor(edit.fallback || "").map((m) => <option key={m.model} value={m.model}>{modelLabel(m)}</option>)}</datalist>
                  </F>
                  <F label={tr("agents_panel.fallback_token")}><select value={edit.fallback_token_name || ""} onChange={(e) => setEdit({ ...edit, fallback_token_name: e.target.value })} disabled={!edit.fallback} className={inp}>
                    <option value="">{tr("agents_panel.default")}</option>
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
                  : <div className="text-xs text-muted">{tr("agents_panel.save_first_then_grant_mcp_servers")}</div>}
              </Sec>

              <button onClick={() => setShowAdv(!showAdv)} className={BUTTON_TEXT.secondary}>
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
                  <F label={tr("agents_panel.reasoning_effort_empty_default_low_medium_hig")}>
                    <input value={edit.effort || ""} onChange={(e) => setEdit({ ...edit, effort: e.target.value })} placeholder="leer = Default" className={inp} />
                  </F>
                </div>
              )}

          </div>
        </Dialog>
      )}
      {deleteAgent && (
        <DeleteDialog was={deleteAgent.role} runs={del.isPending}
          onClose={() => setDeleteAgent(null)} onDelete={() => del.mutate(deleteAgent.id)} />
      )}
    </Area>
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
      <div className="text-xs text-muted">{tr("agents_panel.skills_the_agent_receives_them_auto_always_in")}</div>
      <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded border border-line bg-surface p-2">
        {skills.length === 0 && <div className="text-xs text-muted">{tr("agents_panel.no_skills_created")}</div>}
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
            <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => del(i.id)} />
          </div>
        ))}
        {instances?.length === 0 && <div className="text-xs text-muted">{tr("agents_panel.no_mcp_servers_granted")}</div>}
      </div>
      <div className="mt-2 rounded border border-line bg-surface p-2">
        <select value={serverId} onChange={(e) => { setServerId(e.target.value ? +e.target.value : ""); setValues({}); }}
          className="w-full rounded border border-line bg-card px-2 py-1 text-sm text-ink">
          <option value="">{tr("agents_panel.choose_mcp_server")}</option>
          {servers.filter((s) => s.transport !== "stdio").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {server && (
          <div className="mt-2 space-y-1">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("agents_panel.instance_name_for_example_dkb")}
              className="w-full rounded border border-line bg-card px-2 py-1 text-sm" />
            {(server.variables || []).map((v: any) => (
              <input key={v.key} type={v.secret ? "password" : "text"}
                value={values[v.key] || ""} onChange={(e) => setValues({ ...values, [v.key]: e.target.value })}
                placeholder={`${v.label || v.key}${v.required ? " *" : ""}`}
                className="w-full rounded border border-line bg-card px-2 py-1 text-sm" />
            ))}
            {(server.variables || []).length === 0 && <div className="text-xs text-muted">{tr("agents_panel.this_server_needs_no_variables")}</div>}
            <button onClick={add} className={BUTTON_SMALL.primary}>{tr("agents_panel.add_instance")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
