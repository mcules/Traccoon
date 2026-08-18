import { useState } from "react";
import { tr } from "../i18n";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import AgentsPanel from "../components/AgentsPanel";
import SkillsPanel from "../components/SkillsPanel";
import McpPanel from "../components/McpPanel";
import PreferencesPanel from "../components/PreferencesPanel";
import MySetPanel from "../components/workflow/MySetPanel";
import DestinationsPanel from "../components/DestinationsPanel";
import WebhooksPanel from "../components/WebhooksPanel";
import JobsPanel from "../components/JobsPanel";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";

type Tab = "secrets" | "prefs" | "processes" | "destinations" | "agents" | "mcp"
  | "jobs" | "webhooks" | "skills";
const TABS: [Tab, string][] = [
  ["secrets", "settings.tabs.vault"], ["prefs", "settings.tabs.personal"], ["processes", "settings.tabs.my_flows"],
  ["destinations", "Ziele"], ["agents", "Mein Assistent"], ["mcp", "MCP-Server"],
  ["jobs", "Jobs"], ["webhooks", "Webhooks"], ["skills", "Skills"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Settings() {
  const { tab: tabParam } = useParams();
  // Aktiven Tab aus der URL ableiten; unbekannt → Default "secrets".
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "secrets") as Tab;
  const { user } = useAuth();
  usePageChrome(tr("nav.settings"), TABS.map(([key, label]) => ({
    key, label: tr(label), to: `/settings/${key}`,
    icon: { secrets: "🔐", prefs: "👤", processes: "🔀", destinations: "🎯", agents: "🤖",
            mcp: "🧩", jobs: "⏱️", webhooks: "🪝", skills: "✨" }[key],
  })));
  return (
    <div className="mx-auto max-w-3xl">
      {tab === "secrets" && <Secrets />}
      {tab === "prefs" && <PreferencesPanel isAdmin={user?.global_role === "admin"} />}
      {tab === "processes" && (
        <div className="space-y-4">
          <MySetPanel />
          {/* Eigene Abläufe stehen jetzt unter Prozesse — hier bleibt nur die Einstellung,
              welchen Satz man fährt. Der Verweis, damit niemand sie an der alten Stelle sucht. */}
          <p className="rounded-lg border border-line bg-card p-4 text-sm text-muted">
            Eigene Abläufe anlegen und bearbeiten: <Link to="/processes/eigene"
              className="text-ink underline">{tr("settings.prozesse_eigene")}</Link>.
          </p>
        </div>
      )}
      {tab === "destinations" && user && (
        <DestinationsPanel scope="user" userId={user.id} />
      )}
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
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border border-line bg-card p-4">
        <p className="text-sm text-muted">{tr("settings.keys_einleitung")}</p>
        <ProviderTokens />
      </div>
      <div className="space-y-3 rounded-lg border border-line bg-card p-4">
        <p className="text-sm text-muted">Allgemeiner <b>{tr("settings.secret_tresor")}</b>: beliebige Tokens/Geheimnisse (API-Keys,
          Webhook-Secrets …). Verschlüsselt gespeichert, der Wert wird nie wieder angezeigt. Referenzierbar
          als <code className="rounded bg-surface px-1">secret:&lt;name&gt;</code>.</p>
        <NamedSecrets />
      </div>
    </div>
  );
}

function NamedSecrets() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["named-secrets"], queryFn: () => api.get<any>("/me/secrets"),
  });
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["named-secrets"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };
  const save = () => {
    const n = name.trim();
    if (!n || !value.trim()) { setErr(tr("settings.name_und_wert_noetig")); return; }
    guard(async () => {
      await api.put(`/me/secrets/${encodeURIComponent(n)}`, { value: value.trim(), description: description.trim() });
      setName(""); setValue(""); setDescription("");
    });
  };
  const remove = (n: string) =>
    guard(() => api.put(`/me/secrets/${encodeURIComponent(n)}`, { value: "", description: "" }));
  const prefill = (s: { name: string; description: string }) => {
    setName(s.name); setDescription(s.description); setValue("");
  };
  const vault: { name: string; description: string }[] = data?.vault || [];

  return (
    <div>
      {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
      <div className="mb-2 space-y-1">
        {vault.map((s) => (
          <div key={s.name} className="flex items-center gap-2 text-sm">
            <code className="rounded bg-surface px-1.5 py-0.5 text-xs text-brand">secret:{s.name}</code>
            {s.description && <span className="text-xs text-muted">{s.description}</span>}
            <div className="flex-1" />
            <button onClick={() => prefill(s)} className="text-xs text-muted hover:text-brand">{tr("settings.wert_ersetzen")}</button>
            <button onClick={() => remove(s.name)} className="text-muted hover:text-red-400">✕</button>
          </div>
        ))}
        {vault.length === 0 && <div className="text-xs text-muted">{tr("settings.noch_keine_secrets_im_tresor")}</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("settings.name_z_b_github_pat")}
          className="w-40 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder={tr("settings.wert_token")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={tr("settings.beschreibung_optional")}
          className="w-48 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("settings.speichern")}</button>
      </div>
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
  const [editing, setEditing] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["provider-tokens"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };
  const reset = () => {
    setName(""); setToken(""); setBaseUrl(""); setIsDefault(false); setEditing(null); setEditingId(null);
  };
  const save = () => {
    if (editingId !== null) {
      // Bearbeiten per ID: Base-URL/Default ändern; Token nur, wenn neu eingegeben.
      guard(async () => {
        await api.patch(`/me/provider-tokens/${editingId}`, {
          token: token.trim() || undefined,
          base_url: provider === "openai" ? baseUrl.trim() || null : null,
          is_default: isDefault,
        });
        reset();
      });
      return;
    }
    if (!token.trim()) { setErr(tr("settings.token_wert_eingeben")); return; }
    guard(async () => {
      await api.post("/me/provider-tokens", {
        provider, name, token, is_default: isDefault,
        base_url: provider === "openai" ? baseUrl.trim() || null : null,
      });
      reset();
    });
  };
  const edit = (t: any) => {
    setProvider(t.provider); setName(t.name === "Standard" ? "" : t.name);
    setBaseUrl(t.base_url || ""); setToken(""); setIsDefault(t.is_default);
    setEditing(t.name); setEditingId(t.id); setErr("");
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
              ? <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">{tr("settings.standard")}</span>
              : <button onClick={() => makeDefault(t.id)}
                  className="text-xs text-muted hover:text-brand">{tr("settings.als_standard")}</button>}
            <div className="flex-1" />
            <button onClick={() => edit(t)} className="text-xs text-muted hover:text-brand">{tr("settings.bearbeiten")}</button>
            <button onClick={() => del(t.id)} className="text-muted hover:text-red-400">✕</button>
          </div>
        ))}
        {toks?.length === 0 && <div className="text-xs text-muted">{tr("settings.noch_keine_keys_hinterlegt")}</div>}
      </div>
      {editing !== null && (
        <div className="mb-2 rounded border border-brand/40 bg-brand/10 px-2 py-1 text-xs text-muted">
          {tr("settings.bearbeiten_hinweis", { provider: PROVIDER_LABEL[provider] || provider, name: editing })}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={editing !== null}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink disabled:opacity-50">
          {Object.entries(PROVIDER_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("settings.name_optional")}
          disabled={editing !== null}
          className="w-32 rounded border border-line bg-surface px-2 py-1.5 text-sm disabled:opacity-50" />
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
          placeholder={editing !== null ? tr("settings.neuer_wert_leer_behalten") : tr("settings.token_platzhalter")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        {provider === "openai" && (
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={tr("settings.base_url_optional_z_b_http_litellm_4000_")}
            className="w-64 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
        )}
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} /> {tr("settings.standard")}
        </label>
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          {editing !== null ? "Speichern" : "+ Token"}
        </button>
        {editing !== null && (
          <button onClick={reset} className="text-xs text-muted hover:text-ink">{tr("settings.abbrechen")}</button>
        )}
      </div>
    </div>
  );
}
