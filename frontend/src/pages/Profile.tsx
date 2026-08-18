import { useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { usePageChrome } from "../pageChrome";

export default function Profile() {
  usePageChrome("Profil", []);
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <EmailPanel />
      <BenachrichtigungenPanel />
      <PasswordPanel />
      <ThemePanel />
      <TicketOpenPanel />
      <PmChatStylePanel />
    </div>
  );
}

/** Darstellung des PM-Chats — gilt global über alle Projekte (TRA-21). */
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
      setErr(e instanceof ApiError ? e.message : "Konnte nicht gespeichert werden.");
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
        Darstellung des Projektmanager-Chats. „Terminal" zeichnet den Verlauf wie eine
        Kommandozeile (Monospace, dunkler Hintergrund, Prompt-Zeile) — unabhängig vom Theme.
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
      setErr(e instanceof ApiError ? e.message : "Konnte nicht gespeichert werden.");
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
      <div className="text-sm font-medium text-ink">Tickets öffnen</div>
      <p className="text-xs text-muted">
        Verhalten beim Linksklick auf ein Ticket. Mittelklick öffnet immer die ganze Seite in einem neuen Tab.
      </p>
      <div className="flex gap-2">
        {btn("popup", "Als Popup")}
        {btn("page", "Ganze Seite")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
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
      // Leerer Wert entfernt die E-Mail. Backend liefert 409 bei Kollision.
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
        Deine E-Mail-Adresse für die Anmeldung. Leer lassen und speichern entfernt sie —
        dann ist keine E-Mail-Anmeldung mehr möglich.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">Speichern</button>
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
      {ok && <div className="text-sm text-green-400">{ok}</div>}
    </section>
  );
}

/**
 * Auf welchem Weg dieser Mensch erreicht wird.
 *
 * Der Weg gehört zur Person, nicht zur Nachricht: wer eine Benachrichtigung auslöst —
 * ein Ablauf, ein Agent, ein anderer Mensch — weiß selten, ob der Empfänger Telegram
 * überhaupt benutzt. Deshalb steht hier der Standard, und nur wer es besser weiß,
 * übersteuert ihn in der Aktion.
 */
function BenachrichtigungenPanel() {
  const { user, refresh } = useAuth();
  const [standard, setStandard] = useState(user?.notify_default ?? "telegram");
  const [chat, setChat] = useState(user?.telegram_chat_id ?? "");
  const [mail, setMail] = useState(user?.notify_email ?? "");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  const save = async () => {
    setErr(""); setOk("");
    try {
      await api.put("/me/notify", {
        notify_default: standard, telegram_chat_id: chat.trim(), notify_email: mail.trim(),
      });
      await refresh();
      setOk("Gespeichert.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen.");
    }
  };

  const feld = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
  const fehlt = standard === "telegram" ? !chat.trim()
    : !(mail.trim() || user?.email);

  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">Benachrichtigungen</div>
      <p className="text-xs text-muted">
        In der Oberfläche siehst du ohnehin alles. Hier steht, was zusätzlich hinausgeht —
        und wohin, wenn der Absender keinen Weg nennt (der Normalfall).
      </p>

      <label className="block text-xs font-medium text-muted">
        Standard-Weg
        <select value={standard} onChange={(e) => setStandard(e.target.value)} className={`mt-1 ${feld}`}>
          <option value="telegram">Telegram</option>
          <option value="email">E-Mail</option>
        </select>
      </label>

      <label className="block text-xs font-medium text-muted">
        Telegram-Chat-ID
        <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="z. B. 277928204"
          className={`mt-1 ${feld}`} />
      </label>

      <label className="block text-xs font-medium text-muted">
        E-Mail für Benachrichtigungen
        <input value={mail} onChange={(e) => setMail(e.target.value)}
          placeholder={user?.email || "name@example.com"} className={`mt-1 ${feld}`} />
        <span className="mt-1 block text-[10px] text-muted">
          Leer lassen: es gilt deine Anmelde-Adresse{user?.email ? ` (${user.email})` : ""}.
        </span>
      </label>

      {fehlt && (
        <div className="text-xs text-amber-300">
          Für den gewählten Standard-Weg ist nichts hinterlegt — Nachrichten gehen dann auf
          dem anderen Weg hinaus, sonst nur in die Glocke.
        </div>
      )}
      <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">Speichern</button>
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
    if (newPassword.length < 8) { setErr("Neues Passwort muss mindestens 8 Zeichen haben."); return; }
    try {
      await api.post("/auth/me/password", { old_password: oldPassword, new_password: newPassword });
      setOldPassword(""); setNewPassword("");
      setOk("Passwort geändert.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Ändern fehlgeschlagen.");
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium text-ink">Passwort ändern</div>
      <div className="flex flex-wrap items-center gap-2">
        <input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)}
          placeholder="Aktuelles Passwort"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
          placeholder="Neues Passwort (≥8 Zeichen)"
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <button onClick={save} className="rounded bg-brand px-3 py-1.5 text-sm text-white">Ändern</button>
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
    // Optimistisch: DOM + localStorage sofort, damit die UI unmittelbar reagiert.
    document.documentElement.setAttribute("data-theme", value);
    localStorage.setItem("traccoon_theme", value);
    try {
      await api.put("/me/theme", { value });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Theme konnte nicht gespeichert werden.");
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
      <div className="text-sm font-medium text-ink">Theme</div>
      <p className="text-xs text-muted">Erscheinungsbild der Oberfläche.</p>
      <div className="flex gap-2">
        {btn("dark", "Dunkel")}
        {btn("light", "Hell")}
      </div>
      {err && <div className="text-sm text-red-400">{err}</div>}
    </section>
  );
}
