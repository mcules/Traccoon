import { useState } from "react";
import { tr } from "../i18n";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, workflowApi } from "../api";
import { usePageChrome } from "../pageChrome";
import ArtifactTypesPanel from "../components/ArtifactTypesPanel";
import { SystemSchalterPanel } from "../components/KontoPanels";
import {
  Aktionen, Bereich, BestaetigenDialog, Dialog, DialogFuss, EINGABE, Etikett, Feld,
  Fehlerzeile, ICON, IconKnopf, Liste, ListeLeer, ListenZeile, KNOPF } from "../components/ui";
import ProviderModelsPanel from "../components/ProviderModelsPanel";
import TranslationsPanel from "../components/TranslationsPanel";

// Destinations no longer have a tab of their own: they stand under the settings with a
// scope switch (global | me | project), because it was the same panel three times over.
type Tab = "users" | "cost" | "models" | "maintenance" | "mail" | "artifacts" | "translations";
const TABS: [Tab, string][] = [
  ["users", "admin.tabs.users"], ["cost", "admin.tabs.cost"], ["models", "admin.tabs.models"],
  ["maintenance", "admin.tabs.maintenance"], ["mail", "admin.tabs.mail"],
  ["artifacts", "admin.tabs.artifacts"], ["translations", "admin.tabs.translations"],
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
    <div className="max-w-xl"><Bereich>
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
        <input type="password" placeholder={tr("admin.unveraendert_lassen_leer")}
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
      <button onClick={() => save.mutate()} className="rounded bg-brand px-3 py-1.5 text-white">{tr("admin.speichern")}</button>
      {msg && <span className="ml-3 text-sm text-green-400">{msg}</span>}
    </Bereich></div>
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
    <div className="max-w-xl"><Bereich>
      <div>
        <div className="text-sm font-medium">{tr("admin.wartungsprojekt")}</div>
        <p className="mt-1 text-xs text-muted">
          Nur das hier gewählte Projekt darf sich selbst deployen — und ausschließlich über den
          Update-Button (🤖-Icon oben). Agenten und Auto-Deploy lösen <b>niemals</b> einen Self-Deploy aus.
        </p>
      </div>
      <div>
        <label className="text-xs text-muted">{tr("admin.projekt_das_den_laufenden_traccoon_stack")}</label>
        <select value={status?.maintenance_project_id ?? ""}
          onChange={(e) => save.mutate(e.target.value ? Number(e.target.value) : null)}
          className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="">— keins (Self-Deploy komplett aus) —</option>
          {projects?.map((p) => <option key={p.id} value={p.id}>{p.key} · {p.name}</option>)}
        </select>
      </div>
      {msg && <div className="text-sm text-green-400">{msg}</div>}
      <div className="border-t border-line pt-3 text-xs text-muted">
        {tr("admin.laufende_agenten")}: <b>{status?.running_agents ?? 0}</b>
        {status?.update_pending && ` · ${tr("admin.update_eingereiht")}`}
        {status?.update_in_progress && ` · ${tr("admin.update_laeuft")}`}
      </div>
      <RunRetention />
      <WorkflowLayout />
      <TestenvConfig />
      {/* System wide switches: they belong to the installation, not between the settings of
          one person, where they used to stand. */}
      <SystemSchalterPanel />
    </Bereich></div>
  );
}

/** Spacing of the nodes on "arrange" in the process editor. */
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
      <div className="text-sm font-medium">{tr("admin.prozess_editor_knotenabstand")}</div>
      <p className="mt-1 text-xs text-muted">
        Gilt für „Anordnen" — derselbe Abstand waagerecht wie senkrecht, gemessen zwischen den
        Kartenrändern. Kleinere Werte packen lange Abläufe enger zusammen.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={8} max={400} value={wert}
          onChange={(e) => setGap(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">px</span>
        <button onClick={() => save.mutate()} className={KNOPF.haupt}>
          Speichern</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

/** Global limits of the test environments, effective at runtime (TRA-18). */
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
  const feld = (k: string, label: string, hint?: string) => (
    <label className="block text-xs text-muted">{tr(label)}
      <input value={val(k)} onChange={(e) => setForm({ ...form, [k]: e.target.value })}
        className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1.5 text-ink" />
      {hint && <span className="mt-0.5 block text-[11px] opacity-70">{tr(hint)}</span>}
    </label>
  );
  return (
    <div className="border-t border-line pt-3">
      <div className="text-sm font-medium">{tr("admin.testumgebungen_global")}</div>
      <p className="mt-1 text-xs text-muted">{tr("admin.testumgebungen_hinweis")}</p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {feld("testenv_host", "admin.feld_testenv_host", "admin.feld_testenv_host_hinweis")}
        {feld("testenv_max_concurrent", "admin.feld_testenv_max_concurrent")}
        {feld("testenv_port_lo", "admin.feld_testenv_port_lo")}
        {feld("testenv_port_hi", "admin.feld_testenv_port_hi")}
        {feld("testenv_mem_limit", "admin.feld_testenv_mem_limit", "admin.feld_testenv_mem_beispiel")}
        {feld("testenv_cpus", "admin.feld_testenv_cpus", "admin.feld_testenv_cpus_beispiel")}
        {feld("testenv_max_builds", "admin.feld_testenv_max_builds")}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button onClick={() => save.mutate()} className={KNOPF.haupt}>
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

/** Retention of archived agent runs (TRA-29). */
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
      <div className="text-sm font-medium">{tr("admin.agentenlaeufe_aufbewahren")}</div>
      <p className="mt-1 text-xs text-muted">
        Wird ein Ticket archiviert, wandern seine Agentenläufe mit ins Archiv. Nach dieser
        Frist werden sie samt Schritten endgültig gelöscht. <b>{tr("admin.0_nie_loeschen")}</b>
      </p>
      <div className="mt-2 flex items-center gap-2">
        <input type="number" min={0} value={wert}
          onChange={(e) => setDays(Number(e.target.value))}
          className="w-24 rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink" />
        <span className="text-xs text-muted">{tr("admin.tage")}</span>
        <button onClick={() => save.mutate()} className={KNOPF.haupt}>
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
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
  const act = useMutation({
    mutationFn: (v: { id: number; path: string }) => api.post(`/users/${v.id}/${v.path}`),
    onSuccess: () => { setErr(""); setSperren(null); inv(); }, onError: fail,
  });
  const role = useMutation({
    mutationFn: (v: { id: number; role: string }) => api.post(`/users/${v.id}/role?role=${v.role}`),
    onSuccess: inv, onError: fail,
  });
  const [mcpUser, setMcpUser] = useState<any | null>(null);
  const [editUser, setEditUser] = useState<any | null>(null);
  const [neuOffen, setNeuOffen] = useState(false);
  const [sperren, setSperren] = useState<any | null>(null);

  return (
    <>
    <Fehlerzeile text={err} />
    {/* Keine Tabelle: vier Spalten auf 390 px hießen ein Wort je Zeile, und die drei
        Knöpfe stapelten sich rechts übereinander. Eine Zeile je Nutzer bricht sauber um
        und liest sich auf jeder Breite gleich. */}
    <Bereich>
    <Liste>
      {users?.map((u) => (
        <ListenZeile key={u.id}>
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{u.display_name}</span>
              {u.email && <span className="text-xs text-muted">{u.email}</span>}
              <Etikett farbe={u.status === "active" ? "gruen"
                : u.status === "pending" ? "gelb" : "neutral"}>{u.status}</Etikett>
              <select value={u.global_role} onChange={(e) => role.mutate({ id: u.id, role: e.target.value })}
                className="rounded border border-line bg-surface px-1 py-0.5 text-xs text-ink">
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <Aktionen>
              <IconKnopf icon="🧩" titel={tr("admin.mcp_server_zuweisen")} onClick={() => setMcpUser(u)} />
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setEditUser(u)} />
              {u.status === "pending" && (
                <IconKnopf icon="✅" titel={tr("admin.freischalten")}
                  onClick={() => act.mutate({ id: u.id, path: "approve" })} />
              )}
              {u.status === "active" && (
                <IconKnopf icon="🔒" titel={tr("admin.sperren")} gefahr onClick={() => setSperren(u)} />
              )}
              {u.status === "disabled" && (
                <IconKnopf icon="🔓" titel={tr("admin.entsperren")}
                  onClick={() => act.mutate({ id: u.id, path: "approve" })} />
              )}
            </Aktionen>
          </div>
        </ListenZeile>
      ))}
    </Liste>
    <button onClick={() => { setErr(""); setNeuOffen(true); }}
      className="mt-3 rounded bg-brand px-3 py-1.5 text-sm text-white">
      {ICON.neu} {tr("admin.nutzer_anlegen")}
    </button>

    {neuOffen && (
      <CreateUserDialog onClose={() => setNeuOffen(false)}
        onCreated={() => { setNeuOffen(false); inv(); }} />
    )}
    {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)}
      onSaved={() => { setEditUser(null); inv(); }} />}
    {mcpUser && (
      <Dialog titel={tr("admin.mcp_fuer", { name: mcpUser.display_name || mcpUser.username })}
        onClose={() => setMcpUser(null)}>
        <McpAssign userId={mcpUser.id} />
      </Dialog>
    )}
    {sperren && (
      <BestaetigenDialog titel={tr("admin.sperren")} laeuft={act.isPending}
        text={tr("admin.sperren_frage", { name: sperren.display_name || sperren.username })}
        hinweis={tr("admin.sperren_hinweis")} bestaetigenText={tr("admin.sperren")}
        onClose={() => setSperren(null)}
        onBestaetigen={() => act.mutate({ id: sperren.id, path: "disable" })} />
    )}
    </Bereich>
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
  const [laeuft, setLaeuft] = useState(false);

  const submit = async () => {
    setErr("");
    // Only the user name is mandatory (>=1). E-mail and password are optional; when a
    // password is set, it has to have at least 8 characters.
    if (username.trim().length < 1) { setErr(tr("admin.benutzername_noetig")); return; }
    if (password && password.length < 8) { setErr(tr("admin.passwort_zu_kurz")); return; }
    setLaeuft(true);
    try {
      await api.post<any>("/users", {
        username: username.trim(), display_name: displayName.trim(),
        global_role: globalRole, status: statusVal,
        ...(email.trim() ? { email: email.trim() } : {}),
        ...(password ? { password } : {}),
      });
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("admin.fehler_beim_anlegen"));
    } finally { setLaeuft(false); }
  };

  return (
    <Dialog titel={tr("admin.nutzer_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} onSpeichern={submit} laeuft={laeuft}
        deaktiviert={!username.trim()} speichernText={tr("common.anlegen")} />}>
      <Fehlerzeile text={err} />
      <div className="space-y-3">
        <Feld label={tr("admin.benutzername")}>
          <input value={username} autoFocus onChange={(e) => setUsername(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("admin.anzeigename_optional")}>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("admin.email_optional")}>
          <input value={email} onChange={(e) => setEmail(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("admin.passwort_optional_8_zeichen")}>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={EINGABE} />
        </Feld>
        <div className="grid grid-cols-2 gap-3">
          <Feld label={tr("admin.rolle")}>
            <select value={globalRole} onChange={(e) => setGlobalRole(e.target.value)} className={EINGABE}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </Feld>
          <Feld label={tr("admin.status")} hinweis={tr("admin.aktive_nutzer_koennen_sich_sofort_anmeld")}>
            <select value={statusVal} onChange={(e) => setStatusVal(e.target.value)} className={EINGABE}>
              <option value="active">{tr("admin.aktiv")}</option>
              <option value="pending">{tr("admin.wartend")}</option>
            </select>
          </Feld>
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
      <div className="mb-1 text-muted">Erlaubte MCP-Server (Komma) — z. B. obsidian, imap, paperless, banking, homeassistant, uniwar.
        {data?.provisioned
          ? <span className="ml-1 text-green-400">· provisioniert (Gruppe {data.group})</span>
          : <span className="ml-1 text-yellow-400">{tr("admin.noch_nicht_provisioniert")}</span>}
      </div>
      <div className="flex gap-2">
        <input value={val} onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded border border-line bg-surface px-2 py-1" />
        <button onClick={save} className={KNOPF.haupt}>{tr("admin.speichern")}</button>
      </div>
      <div className="mt-1 text-muted">{tr("admin.mcp_provisionieren_hinweis")}</div>
    </div>
  );
}

function Cost() {
  const { data } = useQuery({ queryKey: ["cost-global"], queryFn: () => api.get<any>("/costs/global") });
  return (
    <Bereich hinweis={<>Summe aller Läufe: <span className="text-xl font-semibold text-ink">
      ${data?.total_usd?.toFixed(4) ?? "0"}</span></>}>
      {/* Modellnamen sind lang und Zahlen kurz: als Tabelle quetschte das auf dem Handy den
          Namen auf ein Wort je Zeile. Eine Zeile je Modell, Zahlen rechts. */}
      <Liste>
        {data?.by_model?.map((m: any) => (
          <ListenZeile key={m.model}>
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="min-w-0 flex-1 break-all text-ink">{m.model}</span>
              <span className="tabular-nums text-ink">${m.usd}</span>
              <span className="tabular-nums text-xs text-muted">{tr("admin.calls_n", { anzahl: m.calls })}</span>
            </div>
          </ListenZeile>
        ))}
        {(!data?.by_model || data.by_model.length === 0) && (
          <ListeLeer>{tr("admin.noch_keine_kosten")}</ListeLeer>
        )}
      </Liste>
    </Bereich>
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
  const [laeuft, setLaeuft] = useState(false);

  const save = async () => {
    setErr("");
    setLaeuft(true);
    try {
      await api.put(`/users/${user.id}`, {
        email, username, display_name: displayName, max_runners: Number(maxRunners),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.speichern_fehlgeschlagen"));
    } finally { setLaeuft(false); }
  };
  // The password is set separately: it takes effect at once and has nothing to do with the
  // rest of the form, which is why it does not hang off "save".
  const resetPw = async () => {
    setErr(""); setMsg("");
    if (newPassword.length < 8) { setErr(tr("admin.passwort_zu_kurz")); return; }
    try {
      await api.post(`/users/${user.id}/reset-password`, { new_password: newPassword });
      setNewPassword(""); setMsg(tr("admin.passwort_gesetzt"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
    }
  };

  return (
    <Dialog titel={tr("admin.nutzer_bearbeiten")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} onSpeichern={save} laeuft={laeuft}
        deaktiviert={!username.trim()} />}>
      <Fehlerzeile text={err} />
      <div className="space-y-3">
        <Feld label={tr("admin.anzeigename")}>
          <input value={displayName} autoFocus onChange={(e) => setDisplayName(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label="E-Mail">
          <input value={email} onChange={(e) => setEmail(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("admin.benutzername")}>
          <input value={username} onChange={(e) => setUsername(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("admin.max_gleichzeitige_agenten_laeufe")}>
          <input type="number" min={0} max={20} value={maxRunners}
            onChange={(e) => setMaxRunners(e.target.value)} className={EINGABE} />
        </Feld>
        <div className="border-t border-line pt-3">
          <Feld label={tr("admin.neues_passwort_setzen")}>
            <div className="flex gap-2">
              <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                placeholder={tr("admin.mind_8_zeichen")} className={EINGABE} />
              <button onClick={resetPw} disabled={!newPassword}
                className={KNOPF.neben}>
                {tr("admin.setzen")}
              </button>
            </div>
          </Feld>
          {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
        </div>
      </div>
    </Dialog>
  );
}
