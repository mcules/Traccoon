import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { tr, setzeSprache } from "../i18n";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";
import MailKontenPanel from "../components/MailKontenPanel";
import {
  AgentenBetriebPanel, AssistentMeldungenPanel, GedaechtnisPanel, MeineSchalterPanel, ZeitzonePanel,
  NachtFensterPanel,
} from "../components/KontoPanels";

/**
 * Everything that belongs to the person, on one page.
 *
 * There used to be two: `/profil` (language, e-mail, password, theme, how a ticket opens)
 * and `/settings/prefs` (runner limit, night window, memory, switches, and a SECOND field
 * for the Telegram chat id, writing to a different endpoint than the one on the profile).
 * Whoever looked for a setting had to guess which of the two pages the author had counted
 * it under. One page now, four subjects, and the settings keep what they are: resources
 * (vault, destinations, MCP, skills, jobs, webhooks), not a person.
 */
type Tab = "person" | "appearance" | "notifications" | "mail" | "agents";
const TABS: [Tab, string, string][] = [
  ["person", "konto.tabs.person", "\u{1F464}"],
  ["appearance", "konto.tabs.ansicht", "\u{1F3A8}"],
  ["notifications", "konto.tabs.meldungen", "\u{1F514}"],
  ["mail", "Mail-Konten", "\u{2709}\uFE0F"],
  ["agents", "konto.tabs.agenten", "\u{1F916}"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Konto() {
  const { tab: tabParam } = useParams();
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "person") as Tab;
  // Beside the content, like the settings and the administration: the account is one of the
  // configuring pages, and those all carry their sections in a column on the left.
  usePageChrome(tr("nav.konto"), TABS.map(([key, label, icon]) => ({
    key, label: tr(label), to: `/account/${key}`, icon,
  })), tab, "seite");

  return (
    <div className="max-w-2xl space-y-4">
      {/* Die Zeitzone gehört zur Person, nicht zu den Agenten: sie entscheidet, was auf
          dieser Oberfläche „8 Uhr" heißt — und damit auch im Nachtfenster und im Zeitplan. */}
      {tab === "person" && <><SprachePanel /><ZeitzonePanel /><EmailPanel /><PasswordPanel /></>}
      {tab === "appearance" && <><ThemePanel /><TicketOpenPanel /><PmChatStylePanel /></>}
      {tab === "notifications" && <><BenachrichtigungenPanel /><AssistentMeldungenPanel /></>}
      {tab === "mail" && <MailKontenPanel />}
      {tab === "agents" && (
        <><AgentenBetriebPanel /><NachtFensterPanel /><GedaechtnisPanel /><MeineSchalterPanel /></>
      )}
    </div>
  );
}

/** Presentation of the PM chat; applies globally across all projects (ABC-21). */
function PmChatStylePanel() {
  const { user, refresh } = useAuth();
  const current = user?.pm_chat_style === "cli" ? "cli" : "bubbles";
  const [err, setErr] = useState("");
  const setStyle = async (value: "bubbles" | "cli") => {
    if (value === current) return;
    setErr("");
    try {
      await api.put("/me/pm-chat-style", { value });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.speichern_fehlgeschlagen"));
    }
  };
  const btn = (value: "bubbles" | "cli", label: string) => (
    <button onClick={() => setStyle(value)}
      className={`rounded border px-3 py-1.5 text-sm ${
        current === value ? "border-brand bg-brand/20 text-ink" : "border-line bg-surface text-muted hover:text-ink"
      }`}>
      {label}
    </button>
  );
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">PM-Chat</div>
      <p className="text-xs text-muted">
        {tr("profile.pm_chat_hinweis")}
      </p>
      <div className="flex gap-2">
        {btn("bubbles", "Sprechblasen")}
        {btn("cli", "Terminal")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}

function TicketOpenPanel() {
  const { user, refresh } = useAuth();
  const current = user?.ticket_open_mode === "page" ? "page" : "popup";
  const [err, setErr] = useState("");
  const setMode = async (value: "popup" | "page") => {
    if (value === current) return;
    setErr("");
    try {
      await api.put("/me/ticket-open-mode", { value });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.speichern_fehlgeschlagen"));
    }
  };
  const btn = (value: "popup" | "page", label: string) => (
    <button onClick={() => setMode(value)}
      className={`rounded border px-3 py-1.5 text-sm ${
        current === value ? "border-brand bg-brand/20 text-ink" : "border-line bg-surface text-muted hover:text-ink"
      }`}>
      {label}
    </button>
  );
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.tickets_oeffnen")}</div>
      <p className="text-xs text-muted">
        {tr("profile.ticket_open_hinweis")}
      </p>
      <div className="flex gap-2">
        {btn("popup", tr("profile.als_popup"))}
        {btn("page", "Ganze Seite")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}

/**
 * Language of the interface.
 *
 * German is the source, everything else a translation. If a text is missing in the chosen
 * language, the German one stands there, so a half translated interface stays usable instead
 * of ending in raw keys. Which languages exist is said by the server: a new one comes into
 * being in the administration without anybody having to touch code.
 */
function SprachePanel() {
  const { user, refresh } = useAuth();
  const [locale, setLocale] = useState(user?.locale || "de");
  const [ok, setOk] = useState("");
  const [err, setErr] = useState("");
  const { data: sprachen } = useQuery({
    queryKey: ["i18n-locales"],
    queryFn: () => api.get<{ locale: string; name: string; enabled: boolean }[]>("/i18n/locales"),
  });

  const save = async () => {
    setErr(""); setOk("");
    try {
      await api.put("/me/locale", { value: locale });
      await setzeSprache(locale);
      await refresh();
      setOk(tr("profile.gespeichert"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("profile.speichern_fehlgeschlagen"));
    }
  };

  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.sprache")}</div>
      <p className="text-xs text-muted">{tr("profile.sprache_hinweis")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <select value={locale} onChange={(e) => setLocale(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
          {(sprachen || [{ locale: "de", name: "Deutsch", enabled: true },
                         { locale: "en", name: "English", enabled: true }])
            .filter((s) => s.enabled)
            .map((s) => <option key={s.locale} value={s.locale}>{s.name}</option>)}
        </select>
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          {tr("profile.speichern")}
        </button>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      {ok && <div className="text-sm text-green-400">{ok}</div>}
    </section>
  );
}

function EmailPanel() {
  const { user, refresh } = useAuth();
  const [email, setEmail] = useState(user?.email ?? "");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const save = async () => {
    setErr(""); setOk("");
    try {
      // An empty value removes the e-mail. The backend answers 409 on a collision.
      await api.put("/me/email", { value: email.trim() });
      await refresh();
      setOk(email.trim() ? "E-Mail gespeichert." : "E-Mail entfernt.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen.");
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">E-Mail</div>
      <p className="text-xs text-muted">
        {tr("profile.email_hinweis")}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("profile.speichern")}</button>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      {ok && <div className="text-sm text-green-400">{ok}</div>}
    </section>
  );
}

/**
 * Which way this human is reached.
 *
 * The way belongs to the person, not to the message: whoever triggers a notification (a
 * flow, an agent, another human) rarely knows whether the recipient uses Telegram at all.
 * That is why the default stands here, and only whoever knows better overrides it in the
 * action.
 */
function BenachrichtigungenPanel() {
  const { user, refresh } = useAuth();
  const [standard, setStandard] = useState(user?.notify_default ?? "telegram");
  const [chat, setChat] = useState(user?.telegram_chat_id ?? "");
  const [mail, setMail] = useState(user?.notify_email ?? "");
  const [zielId, setZielId] = useState(String(user?.notify_destination_id ?? ""));
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  // Ziele tragen Basis-URL und Anmeldung schon; was dahinter steckt (ntfy, Matrix, Gotify,
  // ein eigener Bot), muss Traccoon nicht wissen.
  const { data: ziele } = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api.get<{ id: number; name: string }[]>("/destinations"),
  });

  const save = async () => {
    setErr(""); setOk("");
    try {
      await api.put("/me/notify", {
        notify_default: standard, telegram_chat_id: chat.trim(), notify_email: mail.trim(),
        notify_destination_id: zielId ? +zielId : 0,
      });
      await refresh();
      setOk("Gespeichert.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen.");
    }
  };

  const feld = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
  const fehlt = standard === "telegram" ? !chat.trim()
    : standard === "ziel" ? !zielId
    : !(mail.trim() || user?.email);

  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.benachrichtigungen")}</div>
      <p className="text-xs text-muted">
        {tr("profile.benachrichtigungen_hinweis")}
      </p>

      <label className="block text-xs font-medium text-muted">
        Standard-Weg
        <select value={standard} onChange={(e) => setStandard(e.target.value)} className={`mt-1 ${feld}`}>
          <option value="telegram">{tr("profile.telegram")}</option>
          <option value="email">E-Mail</option>
          <option value="ziel">Ziel (eigener Dienst)</option>
        </select>
      </label>

      <label className="block text-xs font-medium text-muted">
        Ziel
        <select value={zielId} onChange={(e) => setZielId(e.target.value)} className={`mt-1 ${feld}`}>
          <option value="">—</option>
          {ziele?.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
        </select>
        <span className="mt-1 block text-[11px] text-muted">
          Bekommt die Nachricht als JSON (art, titel, text). Ziele stehen unter Einstellungen → Ziele.
        </span>
      </label>

      <label className="block text-xs font-medium text-muted">
        Telegram-Chat-ID
        <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="z. B. 277928204"
          className={`mt-1 ${feld}`} />
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("profile.email_fuer_benachrichtigungen")}
        <input value={mail} onChange={(e) => setMail(e.target.value)}
          placeholder={user?.email || "name@example.com"} className={`mt-1 ${feld}`} />
        <span className="mt-1 block text-[11px] text-muted">
          {tr("profile.leer_lassen_anmelde_adresse")}{user?.email ? ` (${user.email})` : ""}.
        </span>
      </label>

      {fehlt && (
        <div className="text-xs text-amber-300">
          {tr("profile.kein_weg_hinterlegt")}
        </div>
      )}
      <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("profile.speichern")}</button>
      {err && <div className="text-sm text-red-400">{err}</div>}
      {ok && <div className="text-sm text-green-400">{ok}</div>}
    </section>
  );
}

function PasswordPanel() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const save = async () => {
    setErr(""); setOk("");
    if (newPassword.length < 8) { setErr(tr("profile.passwort_zu_kurz")); return; }
    try {
      await api.post("/auth/me/password", { old_password: oldPassword, new_password: newPassword });
      setOldPassword(""); setNewPassword("");
      setOk(tr("profile.passwort_geaendert"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("profile.aendern_fehlgeschlagen"));
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.passwort_aendern")}</div>
      <div className="flex flex-wrap items-center gap-2">
        <input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)}
          placeholder={tr("profile.aktuelles_passwort")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
          placeholder={tr("profile.neues_passwort_8_zeichen")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("profile.aendern")}</button>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      {ok && <div className="text-sm text-green-400">{ok}</div>}
    </section>
  );
}

function ThemePanel() {
  const { user, refresh } = useAuth();
  const current = (user?.theme === "light" || user?.theme === "dark") ? user.theme : "dark";
  const [err, setErr] = useState("");
  const setTheme = async (value: "light" | "dark") => {
    if (value === current) return;
    setErr("");
    // Optimistic: DOM plus localStorage immediately, so that the UI reacts at once.
    document.documentElement.setAttribute("data-theme", value);
    localStorage.setItem("traccoon_theme", value);
    try {
      await api.put("/me/theme", { value });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.speichern_fehlgeschlagen"));
    }
  };
  const btn = (value: "light" | "dark", label: string) => (
    <button onClick={() => setTheme(value)}
      className={`rounded border px-3 py-1.5 text-sm ${
        current === value ? "border-brand bg-brand/20 text-ink" : "border-line bg-surface text-muted hover:text-ink"
      }`}>
      {label}
    </button>
  );
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.theme")}</div>
      <p className="text-xs text-muted">{tr("profile.erscheinungsbild_der_oberflaeche")}</p>
      <div className="flex gap-2">
        {btn("dark", "Dunkel")}
        {btn("light", "Hell")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}
