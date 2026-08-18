import { FormEvent, useState } from "react";
import { tr } from "../i18n";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr("");
    setInfo("");
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await api.post("/auth/register", { email, username, password });
        setInfo("Registriert. Konto muss ggf. von einem Admin freigeschaltet werden.");
        setMode("login");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-line bg-card p-6">
        <div className="mb-5 text-center text-xl font-semibold">🦝 Traccoon</div>
        <div className="space-y-3">
          <input
            className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
            placeholder="E-Mail" value={email} onChange={(e) => setEmail(e.target.value)} />
          {mode === "register" && (
            <input
              className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
              placeholder={tr("login.benutzername")} value={username} onChange={(e) => setUsername(e.target.value)} />
          )}
          <input type="password"
            className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
            placeholder={tr("login.passwort")} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <div className="mt-3 text-sm text-red-400">{err}</div>}
        {info && <div className="mt-3 text-sm text-green-400">{info}</div>}
        <button className="mt-4 w-full rounded bg-brand py-2 font-medium text-white">
          {mode === "login" ? "Anmelden" : "Registrieren"}
        </button>
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}
          className="mt-3 w-full text-sm text-muted hover:text-ink">
          {mode === "login" ? "Neu hier? Registrieren" : "Zurück zum Login"}
        </button>
      </form>
    </div>
  );
}
