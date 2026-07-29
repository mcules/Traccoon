import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, workflowApi } from "../api";
import { usePageChrome } from "../pageChrome";
import DestinationsPanel from "../components/DestinationsPanel";
import ArtifactTypesPanel from "../components/ArtifactTypesPanel";
import ProviderModelsPanel from "../components/ProviderModelsPanel";

type Tab = "users" | "cost" | "models" | "maintenance" | "mail" | "destinations" | "artifacts";
const TABS: [Tab, string][] = [
  ["users", "Nutzer"], ["cost", "Kosten"], ["models", "Modelle"], ["maintenance", "Wartung"],
  ["mail", "E-Mail"], ["destinations", "Ziele"], ["artifacts", "Artefakte"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Admin() {
  const { tab: tabParam } = useParams();
  // Aktiven Tab aus der URL ableiten; unbekannt → Default "users".
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "users") as Tab;
  usePageChrome("Admin", TABS.map(([key, label]) => ({
    key, label, to: `/admin/${key}`,
    icon: { users: "👥", cost: "💶", models: "🧠", maintenance: "🔧", mail: "✉️",
            destinations: "🎯", artifacts: "📦" }[key],
  })));
  return (
    <div>
      {tab === "users" && <Users />}
      {tab === "cost" && <Cost />}
      {tab === "models" && <ProviderModelsPanel />}
      {tab === "maintenance" && <Maintenance />}
      {tab === "mail" && <MailConfig />}
      {tab === "destinations" && <DestinationsPanel scope="global" />}
      {tab === "artifacts" && <ArtifactTypesPanel />}
    </div>
  );
}

function MailConfig() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["mail-config"], queryFn: () => api.get<any>("/admin/mail-config") });
  const [form, setForm] = useState<any>({});
  const [msg, setMsg] = useState("");
  const val = (k: string) => form[k] ?? data?.[k] ?? (k === "smtp_port" ? 587 : "");
  const save = useMutation({
    mutationFn: () => api.put("/admin/mail-config", form),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setForm({});
      qc.invalidateQueries({ queryKey: ["mail-config"] });
    },
  });
  return (
    <div className="max-w-xl space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-xs text-muted">
        SMTP-Server für ausgehende Mails (z. B. Projekt-Einladungen). Ohne Host wird nicht
        versendet — nur geloggt.
      </p>
      <label className="block text-xs text-muted">Host
        <input value={val("smtp_host")} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      </label>
      <label className="block text-xs text-muted">Port
        <input type="number" value={val("smtp_port")}
          onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      </label>
      <label className="block text-xs text-muted">Benutzer
        <input value={val("smtp_user")} onChange={(e) => setForm({ ...form, smtp_user: e.target.value })}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      </label>
      <label className="block text-xs text-muted">Passwort {data?.smtp_password_set && <span className="text-green-400">(gesetzt)</span>}
        <input type="password" placeholder="unverändert lassen = leer"
          value={form.smtp_password ?? ""} onChange={(e) => setForm({ ...form, smtp_password: e.target.value })}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      </label>
      <label className="block text-xs text-muted">Absender (From)
        <input value={val("smtp_from")} onChange={(e) => setForm({ ...form, smtp_from: e.target.value })}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={val("smtp_use_tls")}
          onChange={(e) => setForm({ ...form, smtp_use_tls: e.target.checked })} />
        STARTTLS verwenden
      </label>
      <button onClick={() => save.mutate()} className="rounded bg-brand px-3 py-1.5 text-white">Speichern</button>
      {msg && <span className="ml-3 text-sm text-green-400">{msg}</span>}
    </div>
  );
}

function Maintenance() {
  const qc = useQueryClient();
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<any[]>("/projects") });
  const { data: status } = useQuery({ queryKey: ["admin-status"], queryFn: () => api.get<any>("/admin/status") });
  const [msg, setMsg] = useState("");
  const save = useMutation({
    mutationFn: (project_id: number | null) => api.put("/admin/maintenance", { project_id }),
    onSuccess: () => { setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); qc.invalidateQueries({ queryKey: ["admin-status"] }); },
  });

  return (
    <div className="max-w-xl space-y-4 rounded-lg border border-line bg-card p-4">
      <div>
        <div className="text-sm font-medium">Wartungsprojekt</div>
        <p className="mt-1 text-xs text-muted">
          Nur das hier gewählte Projekt darf sich selbst deployen — und ausschließlich über den
          Update-Button (🤖-Icon oben). Agenten und Auto-Deploy lösen <b>niemals</b> einen Self-Deploy aus.
        </p>
      </div>
      <div>
        <label className="text-xs text-muted">Projekt, das den laufenden Traccoon-Stack aktualisiert</label>
        <select value={status?.maintenance_project_id ?? ""}
          onChange={(e) => save.mutate(e.target.value ? Number(e.target.value) : null)}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="">— keins (Self-Deploy komplett aus) —</option>
          {projects?.map((p) => <option key={p.id} value={p.id}>{p.key} · {p.name}</option>)}
        </select>
      </div>
      {msg && <div className="text-sm text-green-400">{msg}</div>}
      <div className="border-t border-line pt-3 text-xs text-muted">
        Aktuell laufende Agenten: <b>{status?.running_agents ?? 0}</b>
        {status?.update_pending && " · Update eingereiht"}
        {status?.update_in_progress && " · Update läuft"}
      </div>
      <RunRetention />
      <WorkflowLayout />
      <TestenvConfig />
    </div>
  );
}

/** Abstand der Knoten beim „Anordnen" im Prozess-Editor. */
function WorkflowLayout() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["workflow-layout"], queryFn: workflowApi.layout });
  const [gap, setGap] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const wert = gap ?? data?.gap ?? 40;
  const save = useMutation({
    mutationFn: () => workflowApi.setLayout(wert),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setGap(null);
      qc.invalidateQueries({ queryKey: ["workflow-layout"] });
    },
  });
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">Prozess-Editor: Knotenabstand</div>
      <p className="mt-1 text-xs text-muted">
        Gilt für „Anordnen" — derselbe Abstand waagerecht wie senkrecht, gemessen zwischen den
        Kartenrändern. Kleinere Werte packen lange Abläufe enger zusammen.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={8} max={400} value={wert}
          onChange={(e) => setGap(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">px</span>
        <button onClick={() => save.mutate()} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

/** Globale Grenzen der Testumgebungen — zur Laufzeit wirksam (TRA-18). */
function TestenvConfig() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["testenv-config"], queryFn: () => api.get<Record<string, string>>("/admin/testenv-config"),
  });
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const val = (k: string) => form[k] ?? data?.[k] ?? "";
  const save = useMutation({
    mutationFn: () => api.put("/admin/testenv-config", {
      ...form,
      ...Object.fromEntries(["testenv_port_lo", "testenv_port_hi", "testenv_max_concurrent",
                             "testenv_max_builds"]
        .filter((k) => k in form).map((k) => [k, Number(form[k])])),
    }),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setForm({});
      qc.invalidateQueries({ queryKey: ["testenv-config"] });
    },
  });
  const feld = (k: string, label: string, hint?: string) => (
    <label className="block text-xs text-muted">{label}
      <input value={val(k)} onChange={(e) => setForm({ ...form, [k]: e.target.value })}
        className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      {hint && <span className="mt-0.5 block text-[11px] opacity-70">{hint}</span>}
    </label>
  );
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">Testumgebungen (global)</div>
      <p className="mt-1 text-xs text-muted">
        Gilt für alle Projekte. Änderungen greifen sofort — kein Neustart nötig.
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {feld("testenv_host", "Erreichbarer Host", "füllt {host} in der URL-Vorlage")}
        {feld("testenv_max_concurrent", "Max. parallele Umgebungen")}
        {feld("testenv_port_lo", "Portbereich von")}
        {feld("testenv_port_hi", "Portbereich bis")}
        {feld("testenv_mem_limit", "RAM je Umgebung", "z. B. 2g")}
        {feld("testenv_cpus", "CPUs je Umgebung", "z. B. 2")}
        {feld("testenv_max_builds", "Gleichzeitige Builds")}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button onClick={() => save.mutate()} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
        {save.error && (
          <span className="text-sm text-red-400">
            {save.error instanceof ApiError ? save.error.message : "Fehler"}</span>
        )}
      </div>
    </div>
  );
}

/** Aufbewahrung archivierter Agentenläufe (TRA-29). */
function RunRetention() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["run-retention"], queryFn: () => api.get<{ days: number }>("/admin/run-retention"),
  });
  const [days, setDays] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const wert = days ?? data?.days ?? 30;
  const save = useMutation({
    mutationFn: () => api.put("/admin/run-retention", { days: wert }),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setDays(null);
      qc.invalidateQueries({ queryKey: ["run-retention"] });
    },
  });
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">Agentenläufe aufbewahren</div>
      <p className="mt-1 text-xs text-muted">
        Wird ein Ticket archiviert, wandern seine Agentenläufe mit ins Archiv. Nach dieser
        Frist werden sie samt Schritten endgültig gelöscht. <b>0 = nie löschen.</b>
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={0} value={wert}
          onChange={(e) => setDays(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">Tage</span>
        <button onClick={() => save.mutate()} className="rounded bg-brand px-3 py-1.5 text-sm text-white">
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

function Users() {
  const qc = useQueryClient();
  const { data: users } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<any[]>("/users") });
  const act = useMutation({
    mutationFn: (v: { id: number; path: string }) => api.post(`/users/${v.id}/${v.path}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const role = useMutation({
    mutationFn: (v: { id: number; role: string }) => api.post(`/users/${v.id}/role?role=${v.role}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const [mcpFor, setMcpFor] = useState<number | null>(null);
  const [editUser, setEditUser] = useState<any | null>(null);
  return (
    <>
    <CreateUserForm onCreated={() => qc.invalidateQueries({ queryKey: ["admin-users"] })} />
    <table className="w-full text-sm">
      <thead><tr className="border-b border-line text-left text-xs uppercase text-muted">
        <th className="py-2">Nutzer</th><th>Rolle</th><th>Status</th><th></th></tr></thead>
      <tbody>
        {users?.map((u) => (
          <>
          <tr key={u.id} className="border-b border-line">
            <td className="py-2">{u.display_name} <span className="text-muted">({u.email})</span></td>
            <td>
              <select value={u.global_role} onChange={(e) => role.mutate({ id: u.id, role: e.target.value })}
                className="rounded border border-line bg-surface px-1 py-0.5 text-ink">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </td>
            <td>{u.status}</td>
            <td className="space-x-3 text-right">
              <button onClick={() => setEditUser(u)} className="text-muted hover:text-ink">bearbeiten</button>
              <button onClick={() => setMcpFor(mcpFor === u.id ? null : u.id)} className="text-muted hover:text-ink">MCP</button>
              {u.status === "pending" && <button onClick={() => act.mutate({ id: u.id, path: "approve" })} className="text-brand">freischalten</button>}
              {u.status === "active" && <button onClick={() => act.mutate({ id: u.id, path: "disable" })} className="text-muted hover:text-red-400">sperren</button>}
              {u.status === "disabled" && <button onClick={() => act.mutate({ id: u.id, path: "approve" })} className="text-brand">entsperren</button>}
            </td>
          </tr>
          {mcpFor === u.id && (
            <tr><td colSpan={4} className="bg-card px-2 py-2"><McpAssign userId={u.id} /></td></tr>
          )}
          </>
        ))}
      </tbody>
    </table>
    {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)}
      onSaved={() => { setEditUser(null); qc.invalidateQueries({ queryKey: ["admin-users"] }); }} />}
    </>
  );
}

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [globalRole, setGlobalRole] = useState("user");
  const [statusVal, setStatusVal] = useState("active");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const reset = () => {
    setEmail(""); setUsername(""); setDisplayName(""); setPassword("");
    setGlobalRole("user"); setStatusVal("active"); setErr("");
  };
  const submit = async () => {
    setErr(""); setOk("");
    // Pflicht ist nur noch der Benutzername (≥1). E-Mail + Passwort sind optional;
    // wenn ein Passwort gesetzt wird, muss es ≥8 Zeichen haben.
    if (username.trim().length < 1) {
      setErr("Benutzername erforderlich."); return;
    }
    if (password && password.length < 8) {
      setErr("Passwort muss mindestens 8 Zeichen haben (oder leer lassen)."); return;
    }
    try {
      const u = await api.post<any>("/users", {
        username: username.trim(), display_name: displayName.trim(),
        global_role: globalRole, status: statusVal,
        ...(email.trim() ? { email: email.trim() } : {}),
        ...(password ? { password } : {}),
      });
      setOk(`Nutzer „${u.username}" angelegt.`); reset(); onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler beim Anlegen");
    }
  };
  const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
  return (
    <div className="mb-4 rounded-lg border border-line bg-card p-3">
      <button onClick={() => setOpen(!open)} className="text-sm font-medium text-brand">
        {open ? "▾" : "▸"} Nutzer anlegen
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          {err && <div className="text-sm text-red-400">{err}</div>}
          {ok && <div className="text-sm text-brand">{ok}</div>}
          <div className="flex flex-wrap gap-2">
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="E-Mail (optional)" className={`flex-1 ${inp}`} />
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Benutzername" className={`w-40 ${inp}`} />
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Anzeigename (optional)" className={`w-48 ${inp}`} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="Passwort (optional, ≥8 Zeichen)" className={`flex-1 ${inp}`} />
            <select value={globalRole} onChange={(e) => setGlobalRole(e.target.value)} className={inp}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
            <select value={statusVal} onChange={(e) => setStatusVal(e.target.value)} className={inp}>
              <option value="active">aktiv</option>
              <option value="pending">wartend</option>
            </select>
            <button onClick={submit} className="rounded bg-brand px-3 py-1.5 text-sm text-white">Anlegen</button>
          </div>
          <p className="text-xs text-muted">Aktive Nutzer können sich sofort anmelden; „wartend" muss noch freigeschaltet werden.
            Ohne Passwort kann sich der Nutzer nicht anmelden. Ohne E-Mail keine E-Mail-Anmeldung.</p>
        </div>
      )}
    </div>
  );
}

function EditUserModal({ user, onClose, onSaved }: { user: any; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState(user.email);
  const [username, setUsername] = useState(user.username);
  const [displayName, setDisplayName] = useState(user.display_name);
  const [maxRunners, setMaxRunners] = useState(user.max_runners);
  const [newPassword, setNewPassword] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const save = async () => {
    setErr("");
    try {
      await api.put(`/users/${user.id}`, {
        email, username, display_name: displayName, max_runners: Number(maxRunners),
      });
      onSaved();
    } catch (e: any) { setErr(e?.message || "Speichern fehlgeschlagen"); }
  };
  const resetPw = async () => {
    setErr(""); setMsg("");
    if (newPassword.length < 8) { setErr("Mindestens 8 Zeichen."); return; }
    try {
      await api.post(`/users/${user.id}/reset-password`, { new_password: newPassword });
      setNewPassword(""); setMsg("Passwort gesetzt.");
    } catch (e: any) { setErr(e?.message || "Fehlgeschlagen"); }
  };

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl border border-line bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">Nutzer bearbeiten</h2>
          <button onClick={onClose} className="text-muted hover:text-ink">✕</button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted">Anzeigename</label>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
          </div>
          <div>
            <label className="text-xs text-muted">E-Mail</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
          </div>
          <div>
            <label className="text-xs text-muted">Benutzername</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
          </div>
          <div>
            <label className="text-xs text-muted">Max. gleichzeitige Agenten-Läufe</label>
            <input type="number" min={0} max={20} value={maxRunners}
              onChange={(e) => setMaxRunners(e.target.value)}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
          </div>
          <div className="border-t border-line pt-3">
            <label className="text-xs text-muted">Neues Passwort setzen</label>
            <div className="mt-1 flex gap-2">
              <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                placeholder="mind. 8 Zeichen"
                className="flex-1 rounded border border-line bg-surface px-2 py-1.5" />
              <button onClick={resetPw} className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink">Setzen</button>
            </div>
          </div>
          {err && <div className="text-sm text-red-400">{err}</div>}
          {msg && <div className="text-sm text-green-400">{msg}</div>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink">Schließen</button>
          <button onClick={save} className="rounded bg-brand px-4 py-1.5 text-sm text-white">Speichern</button>
        </div>
      </div>
    </div>
  );
}

/** MCP-Server-Zuteilung je User (harte Trennung — Token erzeugt das provision-Skript). */
function McpAssign({ userId }: { userId: number }) {
  const { data, refetch } = useQuery({
    queryKey: ["user-mcp", userId], queryFn: () => api.get<any>(`/users/${userId}/mcp`),
  });
  const [text, setText] = useState<string | null>(null);
  const val = text ?? (data ? (data.servers || []).join(", ") : "");
  const save = async () => {
    const servers = val.split(",").map((s: string) => s.trim()).filter(Boolean);
    await api.put(`/users/${userId}/mcp-servers`, { servers });
    setText(null); refetch();
  };
  return (
    <div className="text-xs">
      <div className="mb-1 text-muted">Erlaubte MCP-Server (Komma) — z. B. obsidian, imap, paperless, banking, homeassistant, uniwar.
        {data?.provisioned
          ? <span className="ml-1 text-green-400">· provisioniert (Gruppe {data.group})</span>
          : <span className="ml-1 text-yellow-400">· noch nicht provisioniert</span>}
      </div>
      <div className="flex gap-2">
        <input value={val} onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded border border-line bg-surface px-2 py-1" />
        <button onClick={save} className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
      </div>
      <div className="mt-1 text-muted">Danach auf dem Host <code>python3 scripts/provision_mcp.py</code> ausführen —
        das legt Gruppe + Token in MCPJungle an und schreibt sie dem User zu.</div>
    </div>
  );
}

function Cost() {
  const { data } = useQuery({ queryKey: ["cost-global"], queryFn: () => api.get<any>("/costs/global") });
  return (
    <div>
      <div className="mb-3 text-2xl font-semibold">${data?.total_usd?.toFixed(4) ?? "0"}</div>
      <table className="w-full text-sm">
        <thead><tr className="border-b border-line text-left text-xs uppercase text-muted"><th className="py-2">Modell</th><th>USD</th><th>Calls</th></tr></thead>
        <tbody>{data?.by_model?.map((m: any) => (
          <tr key={m.model} className="border-b border-line"><td className="py-2">{m.model}</td><td>${m.usd}</td><td>{m.calls}</td></tr>
        ))}</tbody>
      </table>
      {(!data?.by_model || data.by_model.length === 0) && <div className="text-sm text-muted">Noch keine Kosten.</div>}
    </div>
  );
}
