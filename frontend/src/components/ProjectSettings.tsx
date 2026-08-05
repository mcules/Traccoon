import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError, Project } from "../api";
import StatusManager from "./StatusManager";
import DestinationsPanel from "./DestinationsPanel";
import ProjectFields from "./ProjectFields";
import AgentsPanel from "./AgentsPanel";
import Members from "./Members";
import ResourceGrants from "./ResourceGrants";
import DeploymentsPanel from "./DeploymentsPanel";

const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude", codex: "Codex", openai: "OpenAI",
};

type Tab = "allgemein" | "mitglieder" | "agenten" | "board" | "felder" | "git" | "testenv"
  | "deploy" | "destinations";
const TABS: [Tab, string][] = [
  ["allgemein", "Allgemein"], ["mitglieder", "Mitglieder"], ["agenten", "Agenten"], ["board", "Board"],
  ["felder", "Felder"],
  ["git", "Git"], ["testenv", "Testumgebung"], ["deploy", "Deployment"], ["destinations", "Ziele"],
];

type Settings = {
  managed: boolean; has_hardware: boolean; pm_chat_enabled: boolean; verify_command: string; review_enabled: boolean;
  auto_continue: boolean; auto_deploy: boolean; screenshot_enabled: boolean;
  plan_agent: string; exec_agent: string; default_provider: string; default_token_name: string;
  vault_moc_path: string; system_prompt: string;
  workspace_dir: string; git_enabled: boolean; github_repo: string; work_in_branches: boolean;
  merge_target: string; push_after_merge: boolean; use_pull_request: boolean;
  testenv_enabled: boolean; testenv_mode: string; testenv_container_port: number;
  testenv_compose_file: string; testenv_dockerfile: string; testenv_url_template: string;
  testenv_prestart: string; testenv_demo_login: string;
  git_token_set: boolean; testenv_env_set: boolean;
};

export default function ProjectSettings({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { data, refetch } = useQuery({
    queryKey: ["project-settings", project.id],
    queryFn: () => api.get<Settings>(`/projects/${project.id}/settings`),
  });
  const { data: myTokens } = useQuery({
    queryKey: ["provider-tokens"],
    queryFn: () => api.get<{ id: number; provider: string; name: string; is_default: boolean }[]>("/me/provider-tokens"),
  });
  const [s, setS] = useState<Settings | null>(null);
  const [tab, setTab] = useState<Tab>("allgemein");
  const [token, setToken] = useState("");
  const [envText, setEnvText] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [delConfirm, setDelConfirm] = useState("");
  const [inheritMembers, setInheritMembers] = useState(project.inherit_members ?? true);
  const nav = useNavigate();
  const delProject = async () => {
    try { await api.del(`/projects/${project.id}`); nav("/"); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Löschen fehlgeschlagen"); }
  };
  const saveInherit = async (v: boolean) => {
    setInheritMembers(v);
    try {
      await api.put(`/projects/${project.id}`, { inherit_members: v });
      qc.invalidateQueries({ queryKey: ["projects"] });
      flash("Gespeichert.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen"); }
  };

  useEffect(() => { if (data) setS(data); }, [data]);
  useEffect(() => { setInheritMembers(project.inherit_members ?? true); }, [project.id, project.inherit_members]);
  if (!s) return <div className="text-muted">Lädt…</div>;

  const set = (patch: Partial<Settings>) => setS({ ...s, ...patch });
  const flash = (t: string) => { setMsg(t); setErr(""); setTimeout(() => setMsg(""), 2500); };

  const save = async () => {
    try {
      const { git_token_set, testenv_env_set, ...body } = s;
      await api.put(`/projects/${project.id}/settings`, body);
      await refetch();
      // has_hardware/managed/pm_chat steuern die Tabs in ProjectView (aus ["projects"]).
      qc.invalidateQueries({ queryKey: ["projects"] });
      flash("Gespeichert.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen"); }
  };
  const saveEnv = async () => {
    const env: Record<string, string> = {};
    for (const line of envText.split("\n")) {
      const i = line.indexOf("=");
      if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    try {
      await api.put(`/projects/${project.id}/testenv-env`, { env });
      setEnvText(""); await refetch(); flash("Umgebungsvariablen verschlüsselt gespeichert.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };
  const saveToken = async () => {
    try {
      await api.put(`/projects/${project.id}/git-token`, { token });
      setToken(""); await refetch(); flash("Git-Token gespeichert.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };

  const showSave = tab !== "board" && tab !== "agenten" && tab !== "mitglieder"
    && tab !== "destinations";
  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-4 flex flex-wrap gap-1 border-b border-line">
        {(TABS as [Tab, string][]).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {label}</button>
        ))}
      </div>

      <div className="space-y-4">
      {tab === "mitglieder" && (
        <div className="space-y-8">
          <Members project={project} />
          {s.has_hardware && (
            <div>
              <h2 className="mb-1 text-sm font-semibold">Granulare Freigaben</h2>
              <p className="mb-2 text-xs text-muted">Einzelne Orte/Exemplare für jemanden freigeben,
                ohne ihn ins ganze Projekt aufzunehmen.</p>
              <ResourceGrants project={project} />
            </div>
          )}
        </div>
      )}
      {tab === "allgemein" && (
      <Section title="Allgemein">
        <Check label="Projekt hat Hardware" hint="Blendet den Hardware-Tab (Katalog, Exemplare, Beschaffung) ein."
          on={s.has_hardware} onChange={(v) => set({ has_hardware: v })} />
        <Check label="Betreutes Projekt" hint="Ergebnisse gehen erst zur Abnahme, nicht direkt auf fertig."
          on={s.managed} onChange={(v) => set({ managed: v })} />
        <Check label="PM-Chat" hint="Chat-Tab zum Delegieren an den Projektmanager."
          on={s.pm_chat_enabled} onChange={(v) => set({ pm_chat_enabled: v })} />
        <Check label="Review-Gate" hint="Ein Prüf-Agent liest den Diff, bevor abgenommen wird."
          on={s.review_enabled} onChange={(v) => set({ review_enabled: v })} />
        <Check label="Automatisch fortsetzen" hint="Erschöpfte Läufe laufen weiter, bis die Obergrenze greift."
          on={s.auto_continue} onChange={(v) => set({ auto_continue: v })} />
        <Check label="Screenshots erlauben" hint="Agenten dürfen die Testumgebung ansehen."
          on={s.screenshot_enabled} onChange={(v) => set({ screenshot_enabled: v })} />
        <Field label="Prüfbefehl (verify_command)"
          hint="Muss grün sein, bevor ein Lauf abschließt — z. B. „npm run build“."
          value={s.verify_command} onChange={(v) => set({ verify_command: v })} />
        <Field label="Arbeitsverzeichnis" hint="Stack-Ordner auf dem Host (für Deploy)."
          value={s.workspace_dir} onChange={(v) => set({ workspace_dir: v })} />
        <Field label="Vault-MOC-Pfad" hint="Obsidian-Notiz, die Agenten als Projektkontext lesen."
          value={s.vault_moc_path} onChange={(v) => set({ vault_moc_path: v })} />
        <Field label="Zusätzlicher System-Prompt" textarea
          hint="Projektregeln, die jedem Agenten vorangestellt werden."
          value={s.system_prompt} onChange={(v) => set({ system_prompt: v })} />
      </Section>
      )}

      {tab === "allgemein" && project.parent_id != null && (
        <Section title="Vererbung">
          <Check label="Rechte vom übergeordneten Projekt übernehmen"
            hint="Aus: Mitglieder des Eltern-Projekts sehen dieses Sub-Projekt NICHT automatisch — nur direkt hinzugefügte Mitglieder. An: Owner-Rechte werden dabei auf Maintainer begrenzt."
            on={inheritMembers} onChange={saveInherit} />
        </Section>
      )}

      {tab === "allgemein" && project.my_role === "owner" && (
        <div className="rounded-lg border border-red-500/40 bg-card p-4">
          <div className="mb-2 text-sm font-medium text-red-400">Gefahrenzone</div>
          <p className="mb-2 text-xs text-muted">Projekt <b>{project.name}</b> mit allen Tickets, Agenten und
            Einstellungen unwiderruflich löschen. Zum Bestätigen den Projektschlüssel
            <span className="font-mono"> {project.key}</span> eintippen.</p>
          <div className="flex items-center gap-2">
            <input value={delConfirm} onChange={(e) => setDelConfirm(e.target.value)} placeholder={project.key}
              className="w-40 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
            <button onClick={delProject} disabled={delConfirm !== project.key}
              className="rounded bg-red-500 px-3 py-1.5 text-sm text-white disabled:opacity-40">
              Projekt löschen</button>
          </div>
        </div>
      )}

      {tab === "agenten" && (
        <div className="space-y-4">
          <Section title="Rollen-Zuordnung">
            <p className="text-xs text-muted">Welche Rolle plant bzw. führt aus. Danach speichern.</p>
            <div className="grid grid-cols-2 gap-2">
              <Select label="Planender Agent" value={s.plan_agent} onChange={(v) => set({ plan_agent: v })} />
              <Select label="Ausführender Agent" value={s.exec_agent} onChange={(v) => set({ exec_agent: v })} />
            </div>
            <div>
              <label className="text-xs text-muted">Standard-Subscription (überschreibt deinen persönlichen Default)</label>
              <select
                value={s.default_provider && s.default_token_name ? `${s.default_provider}|${s.default_token_name}` : ""}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) { set({ default_provider: "", default_token_name: "" }); return; }
                  const i = v.indexOf("|");
                  set({ default_provider: v.slice(0, i), default_token_name: v.slice(i + 1) });
                }}
                className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
                <option value="">— Persönlicher Default —</option>
                {myTokens?.map((t) => (
                  <option key={t.id} value={`${t.provider}|${t.name}`}>
                    {PROVIDER_LABEL[t.provider] || t.provider} · {t.name}{t.is_default ? " (dein Default)" : ""}
                  </option>
                ))}
              </select>
              <div className="mt-0.5 text-xs text-muted">
                Welche Subscription/Token dieses Projekt nutzt, wenn ein Agent keinen eigenen wählt.
              </div>
            </div>
            <button onClick={save} className="rounded bg-brand px-4 py-2 text-sm text-white">Zuordnung speichern</button>
            {msg && <span className="ml-2 text-sm text-green-400">{msg}</span>}
          </Section>
          <div className="rounded-lg border border-line bg-card p-4">
            <AgentsPanel projectId={project.id} />
          </div>
        </div>
      )}

      {tab === "board" && <StatusManager project={project} />}
      {tab === "felder" && <ProjectFields project={project} />}

      {tab === "destinations" && <DestinationsPanel scope="project" projectId={project.id} />}

      {tab === "git" && (
      <Section title="Git">
        <Check label="Git aktiv" hint="Agenten arbeiten im Repo statt nur im Ticket."
          on={s.git_enabled} onChange={(v) => set({ git_enabled: v })} />
        <Field label="Repository" hint="z. B. https://github.com/nutzer/repo.git"
          value={s.github_repo} onChange={(v) => set({ github_repo: v })} />
        <div className="grid grid-cols-2 gap-2">
          <Field label="Standard-Branch (Basis + Ziel)"
            hint="Ticket-Branches zweigen hiervon ab und werden hierhin gemergt. Meist „main“."
            value={s.merge_target} onChange={(v) => set({ merge_target: v })} />
          <div>
            <label className="text-xs text-muted">Git-Token
              {s.git_token_set && <span className="ml-1 text-green-400">· gesetzt</span>}</label>
            <div className="mt-1 flex gap-1">
              <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_…" className="w-full rounded border border-line bg-surface px-2 py-1.5" />
              <button onClick={saveToken} className="rounded border border-line px-2 text-sm text-muted hover:text-ink">OK</button>
            </div>
          </div>
        </div>
        <Check label="In Branches arbeiten"
          hint="Jedes Ticket bekommt einen eigenen Branch/Worktree. Sub-Tickets basieren auf dem Branch ihres Sammeltickets und mergen dorthin."
          on={s.work_in_branches} onChange={(v) => set({ work_in_branches: v })} />
        <Check label="Nach dem Merge pushen" hint="Ergebnis landet direkt im Remote."
          on={s.push_after_merge} onChange={(v) => set({ push_after_merge: v })} />
        <Check label="Pull Request statt Merge"
          hint="Bei Abnahme wird der Branch gepusht und ein PR geöffnet (nur GitHub). Kein Auto-Deploy."
          on={s.use_pull_request} onChange={(v) => set({ use_pull_request: v })} />
      </Section>
      )}

      {tab === "testenv" && (
      <Section title="Testumgebung">
        <Check label="Testumgebungs-Schritt"
          hint="An: Fertige Umsetzungen landen auf „Testen“ statt direkt gemergt zu werden — der Merge passiert erst bei „Auf Fertig setzen“. Aus: altes Verhalten (Auto-Merge → Fertig)."
          on={s.testenv_enabled} onChange={(v) => set({ testenv_enabled: v })} />
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs text-muted">Startart</label>
            <select value={s.testenv_mode} onChange={(e) => set({ testenv_mode: e.target.value })}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
              <option value="compose">compose.preview.yml</option>
              <option value="dockerfile">Dockerfile bauen</option>
            </select>
          </div>
          {s.testenv_mode === "dockerfile" && (
            <div>
              <label className="text-xs text-muted">Port im Container</label>
              <input type="number" value={s.testenv_container_port}
                onChange={(e) => set({ testenv_container_port: +e.target.value })}
                className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Compose-Datei" hint="Relativ zum Worktree."
            value={s.testenv_compose_file} onChange={(v) => set({ testenv_compose_file: v })} />
          <Field label="Dockerfile" hint="Nur im Dockerfile-Modus."
            value={s.testenv_dockerfile} onChange={(v) => set({ testenv_dockerfile: v })} />
        </div>
        <Field label="URL-Vorlage" hint="Platzhalter {host} (globales Setting) und {port} (zugeteilt)."
          value={s.testenv_url_template} onChange={(v) => set({ testenv_url_template: v })} />
        <Field label="Vorbereitungsbefehle" textarea
          hint="Laufen im Worktree vor dem Bauen — eine Zeile je Befehl, #-Kommentare erlaubt. Der erste Fehlschlag bricht ab."
          value={s.testenv_prestart} onChange={(v) => set({ testenv_prestart: v })} />
        <Field label="Demo-Login (JSON)" hint='Für Screenshots, z. B. {"user":"demo","pass":"demo"}.'
          value={s.testenv_demo_login} onChange={(v) => set({ testenv_demo_login: v })} />
        <div>
          <label className="text-xs text-muted">Umgebungsvariablen
            {s.testenv_env_set && <span className="ml-1 text-green-400">· hinterlegt</span>}</label>
          <textarea value={envText} onChange={(e) => setEnvText(e.target.value)} rows={3}
            placeholder={"KEY=wert\nANDERER=wert"}
            className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs" />
          <div className="mt-1 flex items-center gap-2">
            <button onClick={saveEnv} className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
              Verschlüsselt speichern</button>
            <span className="text-xs text-muted">Wird nach dem Speichern nicht mehr angezeigt.</span>
          </div>
        </div>
      </Section>
      )}

      {tab === "deploy" && (
      <>
      <Section title="Deployment">
        <Check label="Automatisch deployen" hint="Nach Abnahme und Merge baut und startet der Deployer."
          on={s.auto_deploy} onChange={(v) => set({ auto_deploy: v })} />
        {s.auto_deploy && s.use_pull_request && (
          <div className="text-xs text-yellow-400">
            Hinweis: Mit „Pull Request statt Merge“ wird nichts automatisch deployt — erst nach dem Merge auf GitHub.
          </div>
        )}
      </Section>
      {/* Der Schalter und seine Folgen auf einer Seite — die volle Liste direkt darunter. */}
      <Section title="Bisherige Deployments">
        <DeploymentsPanel projectId={project.id} variante="voll" />
      </Section>
      </>
      )}
      </div>

      {showSave && (
        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} className="rounded bg-brand px-4 py-2 text-white">Speichern</button>
          {msg && <span className="text-sm text-green-400">{msg}</span>}
          {err && <span className="text-sm text-red-400">{err}</span>}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <div className="text-sm font-medium">{title}</div>
      {children}
    </div>
  );
}

function Check({ label, hint, on, onChange }: {
  label: string; hint: string; on: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" checked={!!on} onChange={(e) => onChange(e.target.checked)} className="mt-1" />
      <span><span className="text-ink">{label}</span><span className="block text-xs text-muted">{hint}</span></span>
    </label>
  );
}

function Field({ label, hint, value, onChange, textarea }: {
  label: string; hint?: string; value: string; onChange: (v: string) => void; textarea?: boolean;
}) {
  return (
    <div>
      <label className="text-xs text-muted">{label}</label>
      {textarea ? (
        <textarea value={value || ""} onChange={(e) => onChange(e.target.value)} rows={3}
          className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
      ) : (
        <input value={value || ""} onChange={(e) => onChange(e.target.value)}
          className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
      )}
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Select({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="text-xs text-muted">{label}</label>
      <select value={value || ""} onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
        <option value="">— Standard —</option>
        {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
      </select>
    </div>
  );
}
