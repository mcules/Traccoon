import { useState } from "react";
import { tr } from "../i18n";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import AgentsPanel from "../components/AgentsPanel";
import SkillsPanel from "../components/SkillsPanel";
import McpPanel from "../components/McpPanel";
import { DestinationsArea } from "../components/DestinationsPanel";
import WebhooksPanel from "../components/WebhooksPanel";
import PluginsPanel from "../components/PluginsPanel";
import JobsPanel from "../components/JobsPanel";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton,
  Tag, Listing, ListingEmpty, ListenLine, DeleteDialog, BUTTON } from "../components/ui";

/**
 * The settings hold resources, not a person.
 *
 * They used to hold both: the vault and the MCP servers next to the runner limit, the night
 * window and the switches of one human, and a link saying that the own flows had moved
 * elsewhere. What belongs to the person now stands on `/account`, the flows under `/processes`,
 * and what remains has one thing in common: they are things an agent works with.
 */
type Tab = "secrets" | "destinations" | "agents" | "mcp" | "jobs" | "webhooks" | "skills"
  | "plugins";
const TABS: [Tab, string, string][] = [
  ["secrets", "settings.tabs.vault", "\u{1F510}"],
  ["destinations", "settings.tabs.ziele", "\u{1F3AF}"],
  ["agents", "settings.tabs.assistent", "\u{1F916}"],
  ["mcp", "settings.tabs.mcp", "\u{1F9E9}"],
  ["jobs", "settings.tabs.jobs", "\u{23F1}"],
  ["webhooks", "settings.tabs.webhooks", "\u{1FA9D}"],
  ["skills", "settings.tabs.skills", "\u{2728}"],
  // Nur für Admins: Wer ein Plugin einspielt, entscheidet auch, was es sehen darf.
  ["plugins", "settings.tabs.plugins", "\u{1F9E9}"],
];
const TAB_KEYS = TABS.map(([k]) => k);
const ONLY_ADMIN: Tab[] = ["plugins"];

export default function Settings() {
  const { tab: tabParam } = useParams();
  // Derive the active tab from the URL; unknown becomes the default "secrets".
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "secrets") as Tab;
  const { user } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const visible = TABS.filter(([key]) => isAdmin || !ONLY_ADMIN.includes(key));
  usePageChrome(tr("nav.settings"), visible.map(([key, label, icon]) => ({
    key, label: tr(label), to: `/settings/${key}`, icon,
  })), tab, "seite");
  return (
    <div className="max-w-3xl">
      {tab === "secrets" && <Secrets />}
      {tab === "destinations" && <DestinationsArea />}
      {tab === "agents" && <AgentsPanel />}
      {tab === "mcp" && <McpPanel />}
      {tab === "jobs" && <JobsPanel />}
      {tab === "webhooks" && <WebhooksPanel />}
      {tab === "skills" && <SkillsPanel />}
      {tab === "plugins" && isAdmin && <PluginsPanel />}
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude (Subscription)", codex: "Codex (ChatGPT)", openai: "OpenAI (API-Key)",
};

function Secrets() {
  return (
    <div className="space-y-4">
      <Area hint={tr("settings.keys_einleitung")}>
        <ProviderTokens />
      </Area>
      <Area hint={<>
        Allgemeiner <b>{tr("settings.secret_tresor")}</b>: beliebige Tokens/Geheimnisse (API-Keys,
        {tr("settings.tresor_hinweis")}
      </>}>
        <NamedSecrets />
      </Area>
    </div>
  );
}

/**
 * The vault: named secrets, referenced elsewhere only as `secret:name`.
 *
 * The value never comes back from the server, which is why editing means "replace the
 * value" and not "change the text in the field". The dialog says so instead of pretending
 * an empty field were the current secret.
 */
function NamedSecrets() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["named-secrets"], queryFn: () => api.get<any>("/me/secrets"),
  });
  const [dialog, setDialog] = useState<{ name: string; description: string } | null>(null);
  const [remove, setDelete] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["named-secrets"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); return true; }
    catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.fehler")); return false; }
  };
  const vault: { name: string; description: string }[] = data?.vault || [];

  return (
    <div>
      <Errorrow text={err} />
      <Listing className="mb-3">
        {vault.map((s) => (
          <ListenLine key={s.name}>
            <div className="flex items-center gap-2">
              <code className="shrink-0 font-mono text-xs text-brand">secret:{s.name}</code>
              {s.description && <span className="min-w-0 flex-1 truncate text-xs text-muted">{s.description}</span>}
              <div className="flex-1" />
              <Actions>
                <IconButton icon={ICON.edit} title={tr("common.bearbeiten")}
                  onClick={() => setDialog({ name: s.name, description: s.description })} />
                <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger
                  onClick={() => setDelete(s.name)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {vault.length === 0 && <ListingEmpty>{tr("settings.noch_keine_secrets_im_tresor")}</ListingEmpty>}
      </Listing>
      <button onClick={() => setDialog({ name: "", description: "" })}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("settings.secret_anlegen")}
      </button>

      {dialog && (
        <SecretDialog existing={dialog.name ? dialog : null} start={dialog}
          onClose={() => setDialog(null)}
          onSave={async (name, value, description) => {
            const ok = await guard(() => api.put(`/me/secrets/${encodeURIComponent(name)}`,
              { value: value, description: description }));
            if (ok) setDialog(null);
          }} />
      )}
      {remove && (
        <DeleteDialog was={`secret:${remove}`} hint={tr("settings.secret_loeschen_hinweis")}
          onClose={() => setDelete(null)}
          onDelete={async () => {
            await guard(() => api.put(`/me/secrets/${encodeURIComponent(remove)}`,
              { value: "", description: "" }));
            setDelete(null);
          }} />
      )}
    </div>
  );
}

function SecretDialog({ existing, start, onClose, onSave }: {
  existing: { name: string } | null;
  start: { name: string; description: string };
  onClose: () => void;
  onSave: (name: string, value: string, description: string) => void;
}) {
  const [name, setName] = useState(start.name);
  const [value, setValue] = useState("");
  const [description, setDescription] = useState(start.description);
  const can = !!name.trim() && !!value.trim();

  return (
    <Dialog title={existing ? tr("settings.secret_bearbeiten") : tr("settings.secret_anlegen")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!can}
        onSave={() => onSave(name.trim(), value.trim(), description.trim())} />}>
      <div className="space-y-3">
        <Field label={tr("settings.name")} hint={tr("settings.secret_name_hinweis")}>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!existing}
            autoFocus={!existing} placeholder={tr("settings.name_z_b_github_pat")}
            className={`${INPUT_VALUE} disabled:opacity-60`} />
        </Field>
        <Field label={tr("settings.wert_token")}
          hint={existing ? tr("settings.wert_ersetzt_hinweis") : undefined}>
          <input type="password" value={value} onChange={(e) => setValue(e.target.value)}
            autoFocus={!!existing} placeholder={tr("settings.wert_token")} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("settings.beschreibung_optional")}>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={INPUT_VALUE} />
        </Field>
      </div>
    </Dialog>
  );
}

/**
 * Provider tokens: which subscription or key an agent run uses.
 *
 * The list carries the state (default, address), the dialog the work. The token itself
 * never comes back from the server, so editing an entry leaves the field empty and keeps
 * the stored value unless something new is typed in.
 */
function ProviderTokens() {
  const qc = useQueryClient();
  const { data: toks } = useQuery({
    queryKey: ["provider-tokens"], queryFn: () => api.get<any[]>("/me/provider-tokens"),
  });
  const [dialog, setDialog] = useState<any | null>(null);   // null = zu, {} = neu
  const [remove, setDelete] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["provider-tokens"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); return true; }
    catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.fehler")); return false; }
  };

  return (
    <div>
      <Errorrow text={err} />
      <Listing className="mb-3">
        {toks?.map((t) => (
          <ListenLine key={t.id}>
            <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium text-ink">{t.name}</span>
                <span className="text-xs text-muted">{PROVIDER_LABEL[t.provider] || t.provider}</span>
                {t.is_default && <Tag color="brand">{tr("settings.standard")}</Tag>}
              </div>
              {t.base_url && <div className="truncate text-xs text-muted">→ {t.base_url}</div>}
            </div>
            <Actions>
              {!t.is_default && (
                <IconButton icon={ICON.standard} title={tr("common.als_standard")}
                  onClick={() => guard(() => api.post(`/me/provider-tokens/${t.id}/default`))} />
              )}
              <IconButton icon={ICON.edit} title={tr("common.bearbeiten")} onClick={() => setDialog(t)} />
              <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger onClick={() => setDelete(t)} />
            </Actions>
            </div>
          </ListenLine>
        ))}
        {toks?.length === 0 && <ListingEmpty>{tr("settings.noch_keine_keys_hinterlegt")}</ListingEmpty>}
      </Listing>
      <button onClick={() => setDialog({})} className={BUTTON.primary}>
        {ICON.fresh} {tr("settings.token_anlegen")}
      </button>

      {dialog && (
        <TokenDialog entry={dialog.id ? dialog : null} onClose={() => setDialog(null)}
          onSave={async (values) => {
            const ok = dialog.id
              ? await guard(() => api.patch(`/me/provider-tokens/${dialog.id}`, {
                  token: values.token || undefined,
                  base_url: values.provider === "openai" ? values.base_url || null : null,
                  is_default: values.is_default,
                }))
              : await guard(() => api.post("/me/provider-tokens", {
                  provider: values.provider, name: values.name, token: values.token,
                  is_default: values.is_default,
                  base_url: values.provider === "openai" ? values.base_url || null : null,
                }));
            if (ok) setDialog(null);
          }} />
      )}
      {remove && (
        <DeleteDialog was={remove.name} onClose={() => setDelete(null)}
          onDelete={async () => {
            await guard(() => api.del(`/me/provider-tokens/${remove.id}`));
            setDelete(null);
          }} />
      )}
    </div>
  );
}

function TokenDialog({ entry: entry, onClose, onSave }: {
  entry: any | null;
  onClose: () => void;
  onSave: (values: {
    provider: string; name: string; token: string; base_url: string; is_default: boolean;
  }) => void;
}) {
  const [provider, setProvider] = useState(entry?.provider || "claude_code");
  const [name, setName] = useState(entry && entry.name !== "Standard" ? entry.name : "");
  const [token, setToken] = useState("");
  const [baseUrl, setBaseUrl] = useState(entry?.base_url || "");
  const [isStandard, setIsStandard] = useState(!!entry?.is_default);
  // A new entry without a token would be an empty box; an existing one keeps its stored value.
  const can = !!entry || !!token.trim();

  return (
    <Dialog title={entry ? tr("settings.token_bearbeiten") : tr("settings.token_anlegen")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!can}
        onSave={() => onSave({
          provider, name, token: token.trim(), base_url: baseUrl.trim(), is_default: isStandard,
        })} />}>
      <div className="space-y-3">
        <Field label={tr("settings.provider")}>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={!!entry}
            className={`${INPUT_VALUE} disabled:opacity-60`}>
            {Object.entries(PROVIDER_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        <Field label={tr("settings.name_optional")}>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!entry}
            className={`${INPUT_VALUE} disabled:opacity-60`} />
        </Field>
        <Field label={tr("settings.wert_token")}
          hint={entry ? tr("settings.neuer_wert_leer_behalten") : undefined}>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
            autoFocus placeholder={tr("settings.token_platzhalter")} className={INPUT_VALUE} />
        </Field>
        {provider === "openai" && (
          <Field label={tr("settings.base_url_optional_z_b_http_litellm_4000_")}>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={INPUT_VALUE} />
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={isStandard} onChange={(e) => setIsStandard(e.target.checked)} />
          {tr("settings.standard")}
        </label>
      </div>
    </Dialog>
  );
}
