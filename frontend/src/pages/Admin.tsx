import { useState } from "react";
import { tr } from "../i18n";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, workflowApi } from "../api";
import { usePageChrome } from "../pageChrome";
import ArtifactTypesPanel from "../components/ArtifactTypesPanel";
import { SystemSwitchPanel } from "../components/AccountPanels";
import {
  Actions, Area, ConfirmDialog, Dialog, DialogFoot, INPUT_VALUE, Tag, Field,
  Errorrow, ICON, IconButton, Listing, ListingEmpty, ListenLine, BUTTON } from "../components/ui";
import ProviderModelsPanel from "../components/ProviderModelsPanel";
import TranslationsPanel from "../components/TranslationsPanel";

// Destinations no longer have a tab of their own: they stand under the settings with a
// scope switch (global | me | project), because it was the same panel three times over.
type Tab = "users" | "cost" | "models" | "maintenance" | "mail" | "artifacts" | "translations";
const TABS: [Tab, string][] = [
  ["users", "admin.users"], ["cost", "admin.costs"], ["models", "admin.models"],
  ["maintenance", "admin.maintenance"], ["mail", "admin.email"],
  ["artifacts", "admin.artifacts"], ["translations", "admin.translations"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Admin() {
  const { tab: tabParam } = useParams();
  // Derive the active tab from the URL; unknown becomes the default "users".
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "users") as Tab;
  usePageChrome(tr("nav.admin"), TABS.map(([key, label]) => ({
    key, label: tr(label), to: `/admin/${key}`,
    icon: { users: "👥", cost: "💶", models: "🧠", maintenance: "🔧", mail: "✉️",
            artifacts: "📦", translations: "🌐" }[key],
  })), tab, "seite");
  return (
    <div>
      {tab === "users" && <Users />}
      {tab === "cost" && <Cost />}
      {tab === "models" && <ProviderModelsPanel />}
      {tab === "maintenance" && <Maintenance />}
      {tab === "mail" && <MailConfig />}
      {tab === "artifacts" && <ArtifactTypesPanel />}
      {tab === "translations" && <TranslationsPanel />}
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
    <div className="max-w-xl"><Area>
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
        <input type="password" placeholder={tr("admin.leave_empty_keep_unchanged")}
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
      <button onClick={() => save.mutate()} className={BUTTON.primary}>{tr("admin.save")}</button>
      {msg && <span className="ml-3 text-sm text-green-400">{msg}</span>}
    </Area></div>
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
    <div className="max-w-xl"><Area>
      <div>
        <div className="text-sm font-medium">{tr("admin.maintenance_project")}</div>
        <p className="mt-1 text-xs text-muted">
          Nur das hier gewählte Projekt darf sich selbst deployen — und ausschließlich über den
          Update-Button (🤖-Icon oben). Agenten und Auto-Deploy lösen <b>niemals</b> einen Self-Deploy aus.
        </p>
      </div>
      <div>
        <label className="text-xs text-muted">{tr("admin.project_that_updates_the_running_traccoon_sta")}</label>
        <select value={status?.maintenance_project_id ?? ""}
          onChange={(e) => save.mutate(e.target.value ? Number(e.target.value) : null)}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="">— keins (Self-Deploy komplett aus) —</option>
          {projects?.map((p) => <option key={p.id} value={p.id}>{p.key} · {p.name}</option>)}
        </select>
      </div>
      {msg && <div className="text-sm text-green-400">{msg}</div>}
      <div className="border-t border-line pt-3 text-xs text-muted">
        {tr("admin.agents_running_right_now")}: <b>{status?.running_agents ?? 0}</b>
        {status?.update_pending && ` · ${tr("admin.update_queued")}`}
        {status?.update_in_progress && ` · ${tr("admin.update_running")}`}
      </div>
      <RunRetention />
      <WorkflowLayout />
      <TestenvConfig />
      {/* System wide switches: they belong to the installation, not between the settings of
          one person, where they used to stand. */}
      <SystemSwitchPanel />
    </Area></div>
  );
}

/** Spacing of the nodes on "arrange" in the process editor. */
function WorkflowLayout() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["workflow-layout"], queryFn: workflowApi.layout });
  const [gap, setGap] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const value = gap ?? data?.gap ?? 40;
  const save = useMutation({
    mutationFn: () => workflowApi.setLayout(value),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setGap(null);
      qc.invalidateQueries({ queryKey: ["workflow-layout"] });
    },
  });
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">{tr("admin.flow_editor_node_spacing")}</div>
      <p className="mt-1 text-xs text-muted">
        Gilt für „Anordnen" — derselbe Abstand waagerecht wie senkrecht, gemessen zwischen den
        Kartenrändern. Kleinere Werte packen lange Abläufe enger zusammen.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={8} max={400} value={value}
          onChange={(e) => setGap(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">px</span>
        <button onClick={() => save.mutate()} className={BUTTON.primary}>
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

/** Global limits of the test environments, effective at runtime (ABC-18). */
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
  // Label and hint come as keys: the field is built in the language it is currently shown
  // in.
  const field = (k: string, label: string, hint?: string) => (
    <label className="block text-xs text-muted">{tr(label)}
      <input value={val(k)} onChange={(e) => setForm({ ...form, [k]: e.target.value })}
        className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      {hint && <span className="mt-0.5 block text-[11px] opacity-70">{tr(hint)}</span>}
    </label>
  );
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">{tr("admin.test_environments_global")}</div>
      <p className="mt-1 text-xs text-muted">{tr("admin.applies_all_projects_changes")}</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {field("testenv_host", "admin.reachable_host", "admin.fills_host_in_the_address_template")}
        {field("testenv_max_concurrent", "admin.max_parallel_environments")}
        {field("testenv_port_lo", "admin.port_range_from")}
        {field("testenv_port_hi", "admin.port_range_to")}
        {field("testenv_mem_limit", "admin.memory_per_environment", "admin.e_g_2g")}
        {field("testenv_cpus", "admin.cpus_per_environment", "admin.e_g_2")}
        {field("testenv_max_builds", "admin.concurrent_builds")}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button onClick={() => save.mutate()} className={BUTTON.primary}>
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

/** Retention of archived agent runs (ABC-29). */
function RunRetention() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["run-retention"], queryFn: () => api.get<{ days: number }>("/admin/run-retention"),
  });
  const [days, setDays] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const value = days ?? data?.days ?? 30;
  const save = useMutation({
    mutationFn: () => api.put("/admin/run-retention", { days: value }),
    onSuccess: () => {
      setMsg("Gespeichert."); setTimeout(() => setMsg(""), 2000); setDays(null);
      qc.invalidateQueries({ queryKey: ["run-retention"] });
    },
  });
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">{tr("admin.keep_agent_runs")}</div>
      <p className="mt-1 text-xs text-muted">
        Wird ein Ticket archiviert, wandern seine Agentenläufe mit ins Archiv. Nach dieser
        Frist werden sie samt Schritten endgültig gelöscht. <b>{tr("admin.0_never_delete")}</b>
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={0} value={value}
          onChange={(e) => setDays(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">{tr("admin.days")}</span>
        <button onClick={() => save.mutate()} className={BUTTON.primary}>
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

/**
 * User administration.
 *
 * The card carries the state, the actions are icons: unlocking somebody and locking them
 * out are one click apart, and as words in a row ("edit · MCP · approve · lock") they were
 * a sentence one had to read to the end before daring to click. Locking asks back, because
 * it takes the login away from a person, and no undo stands beside it.
 */
function Users() {
  const qc = useQueryClient();
  const { data: users } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<any[]>("/users") });
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const act = useMutation({
    mutationFn: (v: { id: number; path: string }) => api.post(`/users/${v.id}/${v.path}`),
    onSuccess: () => { setErr(""); setLock(null); inv(); }, onError: fail,
  });
  const role = useMutation({
    mutationFn: (v: { id: number; role: string }) => api.post(`/users/${v.id}/role?role=${v.role}`),
    onSuccess: inv, onError: fail,
  });
  const [mcpUser, setMcpUser] = useState<any | null>(null);
  const [editUser, setEditUser] = useState<any | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [lock, setLock] = useState<any | null>(null);

  return (
    <>
    <Errorrow text={err} />
    {/* Keine Tabelle: vier Spalten auf 390 px hießen ein Wort je Zeile, und die drei
        Knöpfe stapelten sich rechts übereinander. Eine Zeile je Nutzer bricht sauber um
        und liest sich auf jeder Breite gleich. */}
    <Area>
    <Listing>
      {users?.map((u) => (
        <ListenLine key={u.id}>
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{u.display_name}</span>
              {u.email && <span className="text-xs text-muted">{u.email}</span>}
              <Tag color={u.status === "active" ? "green"
                : u.status === "pending" ? "yellow" : "neutral"}>{u.status}</Tag>
              <select value={u.global_role} onChange={(e) => role.mutate({ id: u.id, role: e.target.value })}
                className="rounded border border-line bg-surface px-1 py-0.5 text-xs text-ink">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <Actions>
              <IconButton icon="🧩" title={tr("admin.assign_mcp_servers")} onClick={() => setMcpUser(u)} />
              <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => setEditUser(u)} />
              {u.status === "pending" && (
                <IconButton icon="✅" title={tr("admin.activate")}
                  onClick={() => act.mutate({ id: u.id, path: "approve" })} />
              )}
              {u.status === "active" && (
                <IconButton icon="🔒" title={tr("admin.block")} danger onClick={() => setLock(u)} />
              )}
              {u.status === "disabled" && (
                <IconButton icon="🔓" title={tr("admin.unblock")}
                  onClick={() => act.mutate({ id: u.id, path: "approve" })} />
              )}
            </Actions>
          </div>
        </ListenLine>
      ))}
    </Listing>
    <button onClick={() => { setErr(""); setNewOpen(true); }}
      className={BUTTON.primary}>
      {ICON.fresh} {tr("admin.new_user")}
    </button>

    {newOpen && (
      <CreateUserDialog onClose={() => setNewOpen(false)}
        onCreated={() => { setNewOpen(false); inv(); }} />
    )}
    {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)}
      onSaved={() => { setEditUser(null); inv(); }} />}
    {mcpUser && (
      <Dialog title={tr("admin.mcp_servers_name", { name: mcpUser.display_name || mcpUser.username })}
        onClose={() => setMcpUser(null)}>
        <McpAssign userId={mcpUser.id} />
      </Dialog>
    )}
    {lock && (
      <ConfirmDialog title={tr("admin.block")} runs={act.isPending}
        text={tr("admin.really_lock_name", { name: lock.display_name || lock.username })}
        hint={tr("admin.logging_blocked_afterwards_running")} confirmText={tr("admin.block")}
        onClose={() => setLock(null)}
        onConfirm={() => act.mutate({ id: lock.id, path: "disable" })} />
    )}
    </Area>
    </>
  );
}

/**
 * Creating a user.
 *
 * Only the user name is mandatory: an account without an e-mail is a placeholder somebody
 * can be assigned tickets under, and one without a password cannot log in yet, which is
 * exactly what the invitation flow needs.
 */
function CreateUserDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [globalRole, setGlobalRole] = useState("user");
  const [statusVal, setStatusVal] = useState("active");
  const [err, setErr] = useState("");
  const [running, setRunning] = useState(false);

  const submit = async () => {
    setErr("");
    // Only the user name is mandatory (>=1). E-mail and password are optional; when a
    // password is set, it has to have at least 8 characters.
    if (username.trim().length < 1) { setErr(tr("admin.username_required")); return; }
    if (password && password.length < 8) { setErr(tr("admin.password_needs_least_8")); return; }
    setRunning(true);
    try {
      await api.post<any>("/users", {
        username: username.trim(), display_name: displayName.trim(),
        global_role: globalRole, status: statusVal,
        ...(email.trim() ? { email: email.trim() } : {}),
        ...(password ? { password } : {}),
      });
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("admin.not_create"));
    } finally { setRunning(false); }
  };

  return (
    <Dialog title={tr("admin.new_user")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} onSave={submit} runs={running}
        disabled={!username.trim()} saveText={tr("common.create")} />}>
      <Errorrow text={err} />
      <div className="space-y-3">
        <Field label={tr("admin.username")}>
          <input value={username} autoFocus onChange={(e) => setUsername(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("admin.display_name_optional")}>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("admin.email_optional")}>
          <input value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("admin.password_optional_at_least_8_characters")}>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={tr("admin.role")}>
            <select value={globalRole} onChange={(e) => setGlobalRole(e.target.value)} className={INPUT_VALUE}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </Field>
          <Field label={tr("admin.status")} hint={tr("admin.active_users_can_log_in_right_away_pending_on")}>
            <select value={statusVal} onChange={(e) => setStatusVal(e.target.value)} className={INPUT_VALUE}>
              <option value="active">{tr("admin.active")}</option>
              <option value="pending">{tr("admin.pending")}</option>
            </select>
          </Field>
        </div>
      </div>
    </Dialog>
  );
}

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
      <div className="mb-1 text-muted">Erlaubte MCP-Server (Komma) — z. B. obsidian, imap, paperless, banking, homeassistant, gameproj.
        {data?.provisioned
          ? <span className="ml-1 text-green-400">· provisioniert (Gruppe {data.group})</span>
          : <span className="ml-1 text-yellow-400">{tr("admin.not_provisioned_yet")}</span>}
      </div>
      <div className="flex gap-2">
        <input value={val} onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded border border-line bg-surface px-2 py-1" />
        <button onClick={save} className={BUTTON.primary}>{tr("admin.save")}</button>
      </div>
      <div className="mt-1 text-muted">{tr("admin.run_python3_scripts_provision")}</div>
    </div>
  );
}

function Cost() {
  const { data } = useQuery({ queryKey: ["cost-global"], queryFn: () => api.get<any>("/costs/global") });
  return (
    <Area hint={<>Summe aller Läufe: <span className="text-xl font-semibold text-ink">
      ${data?.total_usd?.toFixed(4) ?? "0"}</span></>}>
      {/* Modellnamen sind lang und Zahlen kurz: als Tabelle quetschte das auf dem Handy den
          Namen auf ein Wort je Zeile. Eine Zeile je Modell, Zahlen rechts. */}
      <Listing>
        {data?.by_model?.map((m: any) => (
          <ListenLine key={m.model}>
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="min-w-0 flex-1 break-all text-ink">{m.model}</span>
              <span className="tabular-nums text-ink">${m.usd}</span>
              <span className="tabular-nums text-xs text-muted">{tr("admin.calls_n", { count: m.calls })}</span>
            </div>
          </ListenLine>
        ))}
        {(!data?.by_model || data.by_model.length === 0) && (
          <ListingEmpty>{tr("admin.no_costs_yet")}</ListingEmpty>
        )}
      </Listing>
    </Area>
  );
}

function EditUserModal({ user, onClose, onSaved }: { user: any; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState(user.email || "");
  const [username, setUsername] = useState(user.username);
  const [displayName, setDisplayName] = useState(user.display_name);
  const [maxRunners, setMaxRunners] = useState(user.max_runners);
  const [newPassword, setNewPassword] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [running, setRunning] = useState(false);

  const save = async () => {
    setErr("");
    setRunning(true);
    try {
      await api.put(`/users/${user.id}`, {
        email, username, display_name: displayName, max_runners: Number(maxRunners),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.not_saved"));
    } finally { setRunning(false); }
  };
  // The password is set separately: it takes effect at once and has nothing to do with the
  // rest of the form, which is why it does not hang off "save".
  const resetPw = async () => {
    setErr(""); setMsg("");
    if (newPassword.length < 8) { setErr(tr("admin.password_needs_least_8")); return; }
    try {
      await api.post(`/users/${user.id}/reset-password`, { new_password: newPassword });
      setNewPassword(""); setMsg(tr("admin.password_set"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.error"));
    }
  };

  return (
    <Dialog title={tr("admin.edit_user")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} onSave={save} runs={running}
        disabled={!username.trim()} />}>
      <Errorrow text={err} />
      <div className="space-y-3">
        <Field label={tr("admin.display_name")}>
          <input value={displayName} autoFocus onChange={(e) => setDisplayName(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label="E-Mail">
          <input value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("admin.username")}>
          <input value={username} onChange={(e) => setUsername(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("admin.max_concurrent_agent_runs")}>
          <input type="number" min={0} max={20} value={maxRunners}
            onChange={(e) => setMaxRunners(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <div className="border-t border-line pt-3">
          <Field label={tr("admin.set_new_password")}>
            <div className="flex gap-2">
              <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                placeholder={tr("admin.least_8_characters")} className={INPUT_VALUE} />
              <button onClick={resetPw} disabled={!newPassword}
                className={BUTTON.secondary}>
                {tr("admin.set")}
              </button>
            </div>
          </Field>
          {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
        </div>
      </div>
    </Dialog>
  );
}
