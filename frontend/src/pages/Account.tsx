import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { tr, setLanguage } from "../i18n";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";
import MailAccountsPanel from "../components/MailAccountsPanel";
import TokensPanel from "../components/TokensPanel";
import {
  AgentsOperationPanel, AssistantNoticesPanel, MemoryPanel, MySwitchPanel, TimezonePanel,
  NightWindowPanel,
} from "../components/AccountPanels";
import { BUTTON } from "../components/ui";

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
  ["person", "account.person", "\u{1F464}"],
  ["appearance", "account.appearance", "\u{1F3A8}"],
  ["notifications", "account.notifications", "\u{1F514}"],
  ["mail", "account.mail_accounts", "\u{2709}\uFE0F"],
  ["agents", "account.agents", "\u{1F916}"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Account() {
  const { tab: tabParam } = useParams();
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "person") as Tab;
  // Beside the content, like the settings and the administration: the account is one of the
  // configuring pages, and those all carry their sections in a column on the left.
  usePageChrome(tr("nav.account"), TABS.map(([key, label, icon]) => ({
    key, label: tr(label), to: `/account/${key}`, icon,
  })), tab, "side");

  return (
    <div className="max-w-2xl space-y-4">
      {/* The time zone belongs to the person, not to the agents: it decides what stands on
          "8 o'clock" means in this UI, and thereby in the night window and in the schedule too.
          The tokens stand beside the password: both are how this person proves who they are,
          only one of them is meant for a client that runs for months. */}
      {tab === "person" && (
        <><LanguagePanel /><TimezonePanel /><EmailPanel /><PasswordPanel /><TokensPanel /></>
      )}
      {tab === "appearance" && <><ThemePanel /><TicketOpenPanel /><PmChatStylePanel /></>}
      {tab === "notifications" && <><NotificationsPanel /><AssistantNoticesPanel /></>}
      {tab === "mail" && <MailAccountsPanel />}
      {tab === "agents" && (
        <><AgentsOperationPanel /><NightWindowPanel /><MemoryPanel /><MySwitchPanel /></>
      )}
    </div>
  );
}

/** Presentation of the PM chat; applies globally across all projects. */
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
      setErr(e instanceof ApiError ? e.message : tr("common.not_saved"));
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
        {tr("profile.how_project_manager_chat")}
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
      setErr(e instanceof ApiError ? e.message : tr("common.not_saved"));
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
      <div className="text-sm font-medium text-ink">{tr("profile.opening_tickets")}</div>
      <p className="text-xs text-muted">
        {tr("profile.what_left_click_ticket")}
      </p>
      <div className="flex gap-2">
        {btn("popup", tr("profile.als_popup"))}
        {btn("page", tr("profile.whole_page"))}
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
function LanguagePanel() {
  const { user, refresh } = useAuth();
  const [locale, setLocale] = useState(user?.locale || "de");
  const [ok, setOk] = useState("");
  const [err, setErr] = useState("");
  const { data: languages } = useQuery({
    queryKey: ["i18n-locales"],
    queryFn: () => api.get<{ locale: string; name: string; enabled: boolean }[]>("/i18n/locales"),
  });

  const save = async () => {
    setErr(""); setOk("");
    try {
      await api.put("/me/locale", { value: locale });
      await setLanguage(locale);
      await refresh();
      setOk(tr("profile.saved"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("profile.saving_failed"));
    }
  };

  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.language")}</div>
      <p className="text-xs text-muted">{tr("profile.german_source_when_text")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <select value={locale} onChange={(e) => setLocale(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink">
          {(languages || [{ locale: "de", name: "Deutsch", enabled: true },
                         { locale: "en", name: "English", enabled: true }])
            .filter((s) => s.enabled)
            .map((s) => <option key={s.locale} value={s.locale}>{s.name}</option>)}
        </select>
        <button onClick={save} className={BUTTON.primary}>
          {tr("profile.save")}
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
      setOk(email.trim() ? tr("account.email_saved") : tr("account.email_removed"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.save_failed"));
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">E-Mail</div>
      <p className="text-xs text-muted">
        {tr("profile.email_address_logging_clearing")}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className={BUTTON.primary}>{tr("profile.save")}</button>
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
function NotificationsPanel() {
  const { user, refresh } = useAuth();
  const [standard, setStandard] = useState(user?.notify_default ?? "telegram");
  const [chat, setChat] = useState(user?.telegram_chat_id ?? "");
  const [mail, setMail] = useState(user?.notify_email ?? "");
  const [targetId, setTargetId] = useState(String(user?.notify_destination_id ?? ""));
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  // Destinations carry a base URL and a login already; what sits behind them (ntfy, Matrix,
  // Gotify, a bot of one's own) Traccoon need not know.
  const { data: targets } = useQuery({
    queryKey: ["destinations"],
    queryFn: () => api.get<{ id: number; name: string }[]>("/destinations"),
  });

  const save = async () => {
    setErr(""); setOk("");
    try {
      await api.put("/me/notify", {
        notify_default: standard, telegram_chat_id: chat.trim(), notify_email: mail.trim(),
        notify_destination_id: targetId ? +targetId : 0,
      });
      await refresh();
      setOk(tr("common.saved"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.save_failed"));
    }
  };

  const field = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
  const missing = standard === "telegram" ? !chat.trim()
    : standard === "destination" ? !targetId
    : !(mail.trim() || user?.email);

  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.notifications")}</div>
      <p className="text-xs text-muted">
        {tr("profile.see_everything_interface_anyway")}
      </p>

      <label className="block text-xs font-medium text-muted">
        {tr("profile.default_way")}
        <select value={standard} onChange={(e) => setStandard(e.target.value)} className={`mt-1 ${field}`}>
          <option value="telegram">{tr("profile.telegram")}</option>
          <option value="email">{tr("profile.email")}</option>
          <option value="destination">{tr("profile.destination_own_service")}</option>
        </select>
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("profile.destination")}
        <select value={targetId} onChange={(e) => setTargetId(e.target.value)} className={`mt-1 ${field}`}>
          <option value="">—</option>
          {targets?.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
        </select>
        <span className="mt-1 block text-[11px] text-muted">
          {tr("profile.destination_hint")}
        </span>
      </label>

      <label className="block text-xs font-medium text-muted">
        Telegram-Chat-ID
        <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="z. B. 277928204"
          className={`mt-1 ${field}`} />
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("profile.email_notifications")}
        <input value={mail} onChange={(e) => setMail(e.target.value)}
          placeholder={user?.email || "name@example.com"} className={`mt-1 ${field}`} />
        <span className="mt-1 block text-[11px] text-muted">
          {tr("profile.leave_empty_and_your_sign_in_address_applies")}{user?.email ? ` (${user.email})` : ""}.
        </span>
      </label>

      {missing && (
        <div className="text-xs text-amber-300">
          {tr("profile.nothing_file_chosen_default")}
        </div>
      )}
      <button onClick={save} className={BUTTON.primary}>{tr("profile.save")}</button>
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
    if (newPassword.length < 8) { setErr(tr("profile.new_password_needs_least")); return; }
    try {
      await api.post("/auth/me/password", { old_password: oldPassword, new_password: newPassword });
      setOldPassword(""); setNewPassword("");
      setOk(tr("profile.password_changed"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("profile.change_failed"));
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">{tr("profile.change_password")}</div>
      <div className="flex flex-wrap items-center gap-2">
        <input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)}
          placeholder={tr("profile.current_password")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
          placeholder={tr("profile.new_password_at_least_8_characters")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className={BUTTON.primary}>{tr("profile.change")}</button>
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
      setErr(e instanceof ApiError ? e.message : tr("common.not_saved"));
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
      <p className="text-xs text-muted">{tr("profile.look_interface")}</p>
      <div className="flex gap-2">
        {btn("dark", "Dunkel")}
        {btn("light", "Hell")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}
