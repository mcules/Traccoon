import { FormEvent, useState } from "react";
import { tr } from "../i18n";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { passkeysPossible } from "../passkeys";
import { BUTTON, BUTTON_TEXT} from "../components/ui";

export default function Login() {
  const { login, loginWithPasskey } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");
  const [waiting, setWaiting] = useState(false);
  // Only where the browser can do it at all. A button that leads to "not supported" is
  // worse than one that is not there.
  const withKey = mode === "login" && passkeysPossible();

  async function byPasskey() {
    if (!email.trim()) { setErr(tr("login.name_first")); return; }
    setErr("");
    setWaiting(true);
    try {
      await loginWithPasskey(email.trim());
    } catch (e) {
      // A cancelled dialog is no mishap: whoever closes it has decided, and an error in red
      // underneath says the opposite.
      const name = (e as any)?.name;
      if (name !== "NotAllowedError" && name !== "AbortError") {
        setErr(e instanceof ApiError ? e.message : tr("login.passkey_did_not_work"));
      }
    } finally {
      setWaiting(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setInfo("");
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await api.post("/auth/register", { email, username, password });
        setInfo(tr("login.registered_admin_may_still"));
        setMode("login");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.error"));
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-line bg-card p-6">
        <div className="mb-5 text-center text-xl font-semibold">🦝 Traccoon</div>
        <div className="space-y-3">
          {/* Beschriftung sagt beides. Ein Feld, das „E-Mail" heißt, wird als E-Mail-Feld
              gelesen — dass der kürzere Benutzername genauso geht, sieht dort niemand.
              `username` als autocomplete, nicht `email`: der Passwortspeicher des Browsers
              soll auch den Namen anbieten, den man hier tippt. */}
          <input
            autoComplete="username"
            className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
            placeholder={tr(mode === "login" ? "login.name_or_email" : "login.email")}
            value={email} onChange={(e) => setEmail(e.target.value)} />
          {mode === "register" && (
            <input
              className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
              placeholder={tr("login.username")} value={username} onChange={(e) => setUsername(e.target.value)} />
          )}
          <input type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
            placeholder={tr("login.password")} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <div className="mt-3 text-sm text-red-400">{err}</div>}
        {info && <div className="mt-3 text-sm text-green-400">{info}</div>}
        <button className={`mt-4 w-full ${BUTTON.primary}`}>
          {tr(mode === "login" ? "login.sign_in" : "login.register")}
        </button>
        {withKey && (
          <>
            {/* The password stays the first way in, deliberately. A passkey hangs on the
                domain — over an IP address or a LAN name it says nothing, and then this
                would be the only door and it would be locked. */}
            <div className="my-3 flex items-center gap-3 text-xs text-muted">
              <span className="h-px flex-1 bg-line" />
              {tr("login.or")}
              <span className="h-px flex-1 bg-line" />
            </div>
            <button type="button" disabled={waiting} onClick={() => void byPasskey()}
              className={`w-full ${BUTTON.secondary}`}>
              🔑 {tr(waiting ? "login.passkey_waiting" : "login.with_passkey")}
            </button>
          </>
        )}
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}
          className={BUTTON_TEXT.secondary}>
          {tr(mode === "login" ? "login.new_here_register" : "login.back_sign")}
        </button>
      </form>
    </div>
  );
}
