import { FormEvent, useEffect, useState } from "react";
import { tr } from "../i18n";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, Project } from "../api";
import { useAuth } from "../auth";
import { BUTTON, BUTTON_TEXT} from "../components/ui";

interface Preview {
  project_key: string; project_name: string; email: string; role: string;
  valid: boolean; reason: string | null;
}

/** Target of the invitation link: /accept-invite?token=… It works for logged-in AND not
 * logged-in users (who first log in or register and then join automatically). */
export default function AcceptInvite() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const { user, login } = useAuth();
  const navigate = useNavigate();

  const [preview, setPreview] = useState<Preview | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Project | null>(null);

  useEffect(() => {
    if (!token) { setLoadErr(tr("accept_invite.no_invitation_token_address")); return; }
    api.get<Preview>(`/invitations/by-token/${token}`)
      .then(setPreview)
      .catch((e) => setLoadErr(e instanceof ApiError ? e.message : tr("accept_invite.invitation_not_found")));
  }, [token]);

  async function accept() {
    setBusy(true); setErr("");
    try {
      const p = await api.post<Project>(`/invitations/by-token/${token}/accept`);
      setDone(p);
      setTimeout(() => navigate(`/projects/${p.key}`), 1200);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Beitritt fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  // Already logged in plus a valid invitation: accept directly.
  useEffect(() => {
    if (user && preview?.valid && !done && !busy) accept();
  }, [user, preview]);

  async function submitAuth(e: FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      if (mode === "login") {
        await login(preview!.email, password);
      } else {
        await api.post("/auth/register", {
          email: preview!.email, username, password,
        });
        await login(preview!.email, password);
      }
      // The useEffect above takes over the accept() as soon as `user` is set.
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.error"));
    }
  }

  if (loadErr) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm rounded-lg border border-line bg-card p-6 text-center">
          <div className="mb-2 text-xl">🦝 Traccoon</div>
          <div className="text-red-400">{loadErr}</div>
        </div>
      </div>
    );
  }
  if (!preview) {
    return <div className="p-8 text-muted">{tr("accept_invite.loading")}</div>;
  }
  if (!preview.valid) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm rounded-lg border border-line bg-card p-6 text-center">
          <div className="mb-2 text-xl">🦝 Traccoon</div>
          <div className="text-red-400">{preview.reason || tr("accept_invite.invitation_not_valid")}</div>
        </div>
      </div>
    );
  }
  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm rounded-lg border border-line bg-card p-6 text-center">
          <div className="mb-2 text-xl">🦝 Traccoon</div>
          <div className="text-green-400">Willkommen bei {done.name}! Du wirst weitergeleitet…</div>
        </div>
      </div>
    );
  }
  if (user) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-sm rounded-lg border border-line bg-card p-6 text-center">
          <div className="mb-2 text-xl">🦝 Traccoon</div>
          <div className="text-muted">Trete {preview.project_name} bei…</div>
          {err && <div className="mt-3 text-sm text-red-400">{err}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submitAuth} className="w-full max-w-sm rounded-lg border border-line bg-card p-6">
        <div className="mb-2 text-center text-xl font-semibold">🦝 Traccoon</div>
        <p className="mb-4 text-center text-sm text-muted">
          Einladung zum Projekt <b className="text-ink">{preview.project_name}</b> ({preview.project_key})
          als <b className="text-ink">{preview.role}</b>.
        </p>
        <div className="space-y-3">
          <input disabled value={preview.email}
            className="w-full rounded border border-line bg-surface px-3 py-2 text-muted outline-none" />
          {mode === "register" && (
            <input
              className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
              placeholder={tr("accept_invite.username")} value={username} onChange={(e) => setUsername(e.target.value)} />
          )}
          <input type="password"
            className="w-full rounded border border-line bg-surface px-3 py-2 outline-none"
            placeholder={tr("accept_invite.password")} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <div className="mt-3 text-sm text-red-400">{err}</div>}
        <button className={`mt-4 w-full ${BUTTON.primary}`}>
          {tr(mode === "login" ? "accept_invite.sign_join" : "accept_invite.register_join")}
        </button>
        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}
          className={BUTTON_TEXT.secondary}>
          {tr(mode === "login" ? "accept_invite.new_here_register" : "accept_invite.already_registered_sign")}
        </button>
      </form>
    </div>
  );
}
