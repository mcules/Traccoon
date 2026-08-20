import { useState } from "react";
import { tr } from "../i18n";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import AgentsPanel from "../components/AgentsPanel";
import SkillsPanel from "../components/SkillsPanel";
import McpPanel from "../components/McpPanel";
import { DestinationsBereich } from "../components/DestinationsPanel";
import WebhooksPanel from "../components/WebhooksPanel";
import JobsPanel from "../components/JobsPanel";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf,
  Etikett, Liste, ListeLeer, ListenZeile, LoeschDialog, KNOPF } from "../components/ui";

/**
 * The settings hold resources, not a person.
 *
 * They used to hold both: the vault and the MCP servers next to the runner limit, the night
 * window and the switches of one human, and a link saying that the own flows had moved
 * elsewhere. What belongs to the person now stands on `/account`, the flows under `/processes`,
 * and what remains has one thing in common: they are things an agent works with.
 */
type Tab = "secrets" | "destinations" | "agents" | "mcp" | "jobs" | "webhooks" | "skills";
const TABS: [Tab, string, string][] = [
  ["secrets", "settings.tabs.vault", "\u{1F510}"],
  ["destinations", "settings.tabs.ziele", "\u{1F3AF}"],
  ["agents", "settings.tabs.assistent", "\u{1F916}"],
  ["mcp", "settings.tabs.mcp", "\u{1F9E9}"],
  ["jobs", "settings.tabs.jobs", "\u{23F1}"],
  ["webhooks", "settings.tabs.webhooks", "\u{1FA9D}"],
  ["skills", "settings.tabs.skills", "\u{2728}"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Settings() {
  const { tab: tabParam } = useParams();
  // Derive the active tab from the URL; unknown becomes the default "secrets".
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "secrets") as Tab;
  const { user } = useAuth();
  usePageChrome(tr("nav.settings"), TABS.map(([key, label, icon]) => ({
    key, label: tr(label), to: `/settings/${key}`, icon,
  })), tab, "seite");
  return (
    <div className="max-w-3xl">
      {tab === "secrets" && <Secrets />}
      {tab === "destinations" && <DestinationsBereich />}
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
      <Bereich hinweis={tr("settings.keys_einleitung")}>
        <ProviderTokens />
      </Bereich>
      <Bereich hinweis={<>
        Allgemeiner <b>{tr("settings.secret_tresor")}</b>: beliebige Tokens/Geheimnisse (API-Keys,
        {tr("settings.tresor_hinweis")}
      </>}>
        <NamedSecrets />
      </Bereich>
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
  const [loeschen, setLoeschen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["named-secrets"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); return true; }
    catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.fehler")); return false; }
  };
  const vault: { name: string; description: string }[] = data?.vault || [];

  return (
    <div>
      <Fehlerzeile text={err} />
      <Liste className="mb-3">
        {vault.map((s) => (
          <ListenZeile key={s.name}>
            <div className="flex items-center gap-2">
              <code className="shrink-0 font-mono text-xs text-brand">secret:{s.name}</code>
              {s.description && <span className="min-w-0 flex-1 truncate text-xs text-muted">{s.description}</span>}
              <div className="flex-1" />
              <Aktionen>
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                  onClick={() => setDialog({ name: s.name, description: s.description })} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                  onClick={() => setLoeschen(s.name)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {vault.length === 0 && <ListeLeer>{tr("settings.noch_keine_secrets_im_tresor")}</ListeLeer>}
      </Liste>
      <button onClick={() => setDialog({ name: "", description: "" })}
        className={KNOPF.haupt}>
        {ICON.neu} {tr("settings.secret_anlegen")}
      </button>

      {dialog && (
        <SecretDialog vorhanden={dialog.name ? dialog : null} start={dialog}
          onClose={() => setDialog(null)}
          onSpeichern={async (name, wert, beschreibung) => {
            const ok = await guard(() => api.put(`/me/secrets/${encodeURIComponent(name)}`,
              { value: wert, description: beschreibung }));
            if (ok) setDialog(null);
          }} />
      )}
      {loeschen && (
        <LoeschDialog was={`secret:${loeschen}`} hinweis={tr("settings.secret_loeschen_hinweis")}
          onClose={() => setLoeschen(null)}
          onLoeschen={async () => {
            await guard(() => api.put(`/me/secrets/${encodeURIComponent(loeschen)}`,
              { value: "", description: "" }));
            setLoeschen(null);
          }} />
      )}
    </div>
  );
}

function SecretDialog({ vorhanden, start, onClose, onSpeichern }: {
  vorhanden: { name: string } | null;
  start: { name: string; description: string };
  onClose: () => void;
  onSpeichern: (name: string, wert: string, beschreibung: string) => void;
}) {
  const [name, setName] = useState(start.name);
  const [wert, setWert] = useState("");
  const [beschreibung, setBeschreibung] = useState(start.description);
  const kann = !!name.trim() && !!wert.trim();

  return (
    <Dialog titel={vorhanden ? tr("settings.secret_bearbeiten") : tr("settings.secret_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!kann}
        onSpeichern={() => onSpeichern(name.trim(), wert.trim(), beschreibung.trim())} />}>
      <div className="space-y-3">
        <Feld label={tr("settings.name")} hinweis={tr("settings.secret_name_hinweis")}>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!vorhanden}
            autoFocus={!vorhanden} placeholder={tr("settings.name_z_b_github_pat")}
            className={`${EINGABE} disabled:opacity-60`} />
        </Feld>
        <Feld label={tr("settings.wert_token")}
          hinweis={vorhanden ? tr("settings.wert_ersetzt_hinweis") : undefined}>
          <input type="password" value={wert} onChange={(e) => setWert(e.target.value)}
            autoFocus={!!vorhanden} placeholder={tr("settings.wert_token")} className={EINGABE} />
        </Feld>
        <Feld label={tr("settings.beschreibung_optional")}>
          <input value={beschreibung} onChange={(e) => setBeschreibung(e.target.value)} className={EINGABE} />
        </Feld>
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
  const [loeschen, setLoeschen] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["provider-tokens"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); return true; }
    catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.fehler")); return false; }
  };

  return (
    <div>
      <Fehlerzeile text={err} />
      <Liste className="mb-3">
        {toks?.map((t) => (
          <ListenZeile key={t.id}>
            <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium text-ink">{t.name}</span>
                <span className="text-xs text-muted">{PROVIDER_LABEL[t.provider] || t.provider}</span>
                {t.is_default && <Etikett farbe="brand">{tr("settings.standard")}</Etikett>}
              </div>
              {t.base_url && <div className="truncate text-xs text-muted">→ {t.base_url}</div>}
            </div>
            <Aktionen>
              {!t.is_default && (
                <IconKnopf icon={ICON.standard} titel={tr("common.als_standard")}
                  onClick={() => guard(() => api.post(`/me/provider-tokens/${t.id}/default`))} />
              )}
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(t)} />
              <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschen(t)} />
            </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {toks?.length === 0 && <ListeLeer>{tr("settings.noch_keine_keys_hinterlegt")}</ListeLeer>}
      </Liste>
      <button onClick={() => setDialog({})} className={KNOPF.haupt}>
        {ICON.neu} {tr("settings.token_anlegen")}
      </button>

      {dialog && (
        <TokenDialog eintrag={dialog.id ? dialog : null} onClose={() => setDialog(null)}
          onSpeichern={async (werte) => {
            const ok = dialog.id
              ? await guard(() => api.patch(`/me/provider-tokens/${dialog.id}`, {
                  token: werte.token || undefined,
                  base_url: werte.provider === "openai" ? werte.base_url || null : null,
                  is_default: werte.is_default,
                }))
              : await guard(() => api.post("/me/provider-tokens", {
                  provider: werte.provider, name: werte.name, token: werte.token,
                  is_default: werte.is_default,
                  base_url: werte.provider === "openai" ? werte.base_url || null : null,
                }));
            if (ok) setDialog(null);
          }} />
      )}
      {loeschen && (
        <LoeschDialog was={loeschen.name} onClose={() => setLoeschen(null)}
          onLoeschen={async () => {
            await guard(() => api.del(`/me/provider-tokens/${loeschen.id}`));
            setLoeschen(null);
          }} />
      )}
    </div>
  );
}

function TokenDialog({ eintrag, onClose, onSpeichern }: {
  eintrag: any | null;
  onClose: () => void;
  onSpeichern: (werte: {
    provider: string; name: string; token: string; base_url: string; is_default: boolean;
  }) => void;
}) {
  const [provider, setProvider] = useState(eintrag?.provider || "claude_code");
  const [name, setName] = useState(eintrag && eintrag.name !== "Standard" ? eintrag.name : "");
  const [token, setToken] = useState("");
  const [baseUrl, setBaseUrl] = useState(eintrag?.base_url || "");
  const [istStandard, setIstStandard] = useState(!!eintrag?.is_default);
  // A new entry without a token would be an empty box; an existing one keeps its stored value.
  const kann = !!eintrag || !!token.trim();

  return (
    <Dialog titel={eintrag ? tr("settings.token_bearbeiten") : tr("settings.token_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!kann}
        onSpeichern={() => onSpeichern({
          provider, name, token: token.trim(), base_url: baseUrl.trim(), is_default: istStandard,
        })} />}>
      <div className="space-y-3">
        <Feld label={tr("settings.provider")}>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={!!eintrag}
            className={`${EINGABE} disabled:opacity-60`}>
            {Object.entries(PROVIDER_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Feld>
        <Feld label={tr("settings.name_optional")}>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!eintrag}
            className={`${EINGABE} disabled:opacity-60`} />
        </Feld>
        <Feld label={tr("settings.wert_token")}
          hinweis={eintrag ? tr("settings.neuer_wert_leer_behalten") : undefined}>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
            autoFocus placeholder={tr("settings.token_platzhalter")} className={EINGABE} />
        </Feld>
        {provider === "openai" && (
          <Feld label={tr("settings.base_url_optional_z_b_http_litellm_4000_")}>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={EINGABE} />
          </Feld>
        )}
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={istStandard} onChange={(e) => setIstStandard(e.target.checked)} />
          {tr("settings.standard")}
        </label>
      </div>
    </Dialog>
  );
}
