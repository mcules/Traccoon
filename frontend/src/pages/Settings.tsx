import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import AgentsPanel from "../components/AgentsPanel";
import SkillsPanel from "../components/SkillsPanel";
import McpPanel from "../components/McpPanel";
import PreferencesPanel from "../components/PreferencesPanel";
import WebhooksPanel from "../components/WebhooksPanel";
import JobsPanel from "../components/JobsPanel";
import { useAuth } from "../auth";

type Tab = "secrets" | "prefs" | "agents" | "mcp" | "jobs" | "webhooks" | "skills";
const TABS: [Tab, string][] = [
  ["secrets", "Secret-Tresor"], ["prefs", "Persönlich"], ["agents", "Mein Assistent"],
  ["mcp", "MCP-Server"], ["jobs", "Jobs"], ["webhooks", "Webhooks"], ["skills", "Skills"],
];

export default function Settings() {
  const [tab, setTab] = useState<Tab>("secrets");
  const { user } = useAuth();
  return (
    <div className="max-w-3xl">
      <h1 className="mb-4 text-lg font-semibold">Einstellungen</h1>
      <div className="mb-4 flex gap-1 border-b border-line">
        {TABS.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {label}</button>
        ))}
      </div>
      {tab === "secrets" && <Secrets />}
      {tab === "prefs" && <PreferencesPanel isAdmin={user?.global_role === "admin"} />}
      {tab === "agents" && <AgentsPanel />}
      {tab === "mcp" && <McpPanel />}
      {tab === "jobs" && <JobsPanel />}
      {tab === "webhooks" && <WebhooksPanel />}
      {tab === "skills" && <SkillsPanel />}
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude (Subscription)", codex: "Codex (ChatGPT)", openai: "OpenAI (API-Key)",
};

function Secrets() {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-sm text-muted">Hinterlege deine <b>LLM-Keys</b>: Provider wählen, Key eingeben, optional
        einen Namen vergeben (um mehrere Keys je Provider zu unterscheiden). Pro Agent wählst du dann,
        welcher Key genutzt wird. Der als <b>Standard</b> markierte Key gilt, wenn ein Agent keinen bestimmten wählt.</p>
      <ProviderTokens />
    </div>
  );
}

function ProviderTokens() {
  const qc = useQueryClient();
  const { data: toks } = useQuery({
    queryKey: ["provider-tokens"], queryFn: () => api.get<any[]>("/me/provider-tokens"),
  });
  const [provider, setProvider] = useState("claude_code");
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["provider-tokens"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };
  const add = () => {
    if (!token.trim()) return;
    guard(async () => {
      await api.post("/me/provider-tokens", {
        provider, name, token, is_default: isDefault,
        base_url: provider === "openai" ? baseUrl.trim() || null : null,
      });
      setName(""); setToken(""); setBaseUrl(""); setIsDefault(false);
    });
  };
  const del = (id: number) => guard(() => api.del(`/me/provider-tokens/${id}`));
  const makeDefault = (id: number) => guard(() => api.post(`/me/provider-tokens/${id}/default`));

  return (
    <div>
      {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
      <div className="mb-2 space-y-1">
        {toks?.map((t) => (
          <div key={t.id} className="flex items-center gap-2 text-sm">
            <span className="w-40 text-muted">{PROVIDER_LABEL[t.provider] || t.provider}</span>
            <span className="font-medium">{t.name}</span>
            {t.base_url && <span className="text-xs text-muted">→ {t.base_url}</span>}
            {t.is_default
              ? <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">Standard</span>
              : <button onClick={() => makeDefault(t.id)}
                  className="text-xs text-muted hover:text-brand">als Standard</button>}
            <div className="flex-1" />
            <button onClick={() => del(t.id)} className="text-muted hover:text-red-400">✕</button>
          </div>
        ))}
        {toks?.length === 0 && <div className="text-xs text-muted">Noch keine Keys hinterlegt.</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
          {Object.entries(PROVIDER_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)"
          className="w-32 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Token / sk-…"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        {provider === "openai" && (
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="Base-URL (optional, z. B. http://litellm:4000/v1)"
            className="w-64 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        )}
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} /> Standard
        </label>
        <button onClick={add} className="rounded bg-brand px-3 py-1.5 text-sm text-white">+ Token</button>
      </div>
    </div>
  );
}
