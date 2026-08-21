import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, Project } from "../api";
import StatusManager from "./StatusManager";
import { DestinationsArea } from "./DestinationsPanel";
import ProjectFields from "./ProjectFields";
import AgentsPanel from "./AgentsPanel";
import Members from "./Members";
import ResourceGrants from "./ResourceGrants";
import DeploymentsPanel from "./DeploymentsPanel";
import SlotList from "./workflow/SlotList";
import WorkflowList from "./workflow/WorkflowList";
import { projectPath } from "../projectTabs";
import { BUTTON_SMALL, BUTTON} from "./ui";

const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude", codex: "Codex", openai: "OpenAI",
};

/**
 * The sections of the project settings, in the address.
 *
 * They used to hang off a `useState`: no deep link into a section, and the back button
 * jumped out of the settings instead of one section back. The flows moved in here from
 * their own top level tab, because a slot assignment is a setting, not a place of work.
 */
type Tab = "general" | "members" | "agents" | "processes" | "board" | "fields" | "git"
  | "testenv" | "deployment" | "destinations";
const TABS: [Tab, string, string][] = [
  ["general", "project_settings.general_2", "\u{2699}\u{FE0F}"],
  ["members", "project_settings.members", "\u{1F465}"],
  ["agents", "project_settings.agents", "\u{1F916}"],
  ["processes", "project_settings.flows", "\u{1F500}"],
  ["board", "project_settings.tab_board", "\u{1F5C2}\u{FE0F}"],
  ["fields", "project_settings.fields", "\u{1F4DD}"],
  ["git", "project_settings.tab_git", "\u{1F4C1}"],
  ["testenv", "project_settings.test_environment", "\u{1F9EA}"],
  ["deployment", "project_settings.tab_deployment", "\u{1F680}"],
  ["destinations", "project_settings.destinations", "\u{1F3AF}"],
];
const TAB_KEYS = TABS.map(([k]) => k);

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

export default function ProjectSettings({ project, area: area }: { project: Project; area?: string }) {
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
  const tab: Tab = (TAB_KEYS.includes(area as Tab) ? area : "general") as Tab;
  const [token, setToken] = useState("");
  const [envText, setEnvText] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [delConfirm, setDelConfirm] = useState("");
  const [inheritMembers, setInheritMembers] = useState(project.inherit_members ?? true);
  const nav = useNavigate();
  const delProject = async () => {
    try { await api.del(`/projects/${project.id}`); nav("/"); }
    catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.deleting_failed")); }
  };
  const saveInherit = async (v: boolean) => {
    setInheritMembers(v);
    try {
      await api.put(`/projects/${project.id}`, { inherit_members: v });
      qc.invalidateQueries({ queryKey: ["projects"] });
      flash(tr("common.saved"));
    } catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.save_failed")); }
  };

  useEffect(() => { if (data) setS(data); }, [data]);
  useEffect(() => { setInheritMembers(project.inherit_members ?? true); }, [project.id, project.inherit_members]);
  if (!s) return <div className="text-muted">{tr("project_settings.loading")}</div>;

  const set = (patch: Partial<Settings>) => setS({ ...s, ...patch });
  const flash = (t: string) => { setMsg(t); setErr(""); setTimeout(() => setMsg(""), 2500); };

  const save = async () => {
    try {
      const { git_token_set, testenv_env_set, ...body } = s;
      await api.put(`/projects/${project.id}/settings`, body);
      await refetch();
      // has_hardware/managed/pm_chat control the tabs in ProjectView (from ["projects"]).
      qc.invalidateQueries({ queryKey: ["projects"] });
      flash(tr("common.saved"));
    } catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.save_failed")); }
  };
  const saveEnv = async () => {
    const env: Record<string, string> = {};
    for (const line of envText.split("\n")) {
      const i = line.indexOf("=");
      if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    try {
      await api.put(`/projects/${project.id}/testenv-env`, { env });
      setEnvText(""); await refetch(); flash(tr("project_settings.environment_variables_saved_encrypted"));
    } catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.error")); }
  };
  const saveToken = async () => {
    try {
      await api.put(`/projects/${project.id}/git-token`, { token });
      setToken(""); await refetch(); flash("Git-Token gespeichert.");
    } catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.error")); }
  };

  const showSave = tab !== "board" && tab !== "agents" && tab !== "members"
    && tab !== "destinations" && tab !== "processes";
  return (
    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
      {/* Ten sections wrapped into three lines of pills; as a column they simply stand there. */}
      <nav className="flex shrink-0 flex-wrap gap-1 rounded-lg border border-line bg-card p-1 md:w-48 md:flex-col md:flex-nowrap">
        {TABS.map(([t, label, icon]) => (
          <Link key={t} to={projectPath(project.key, "settings", t)}
            className={`flex min-h-[36px] items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm md:min-h-0 ${
              tab === t ? "bg-surface font-medium text-ink" : "text-muted hover:bg-surface hover:text-ink"}`}>
            <span className="text-base leading-none">{icon}</span>
            <span>{tr(label)}</span>
          </Link>
        ))}
      </nav>

      <div className="min-w-0 max-w-2xl flex-1 space-y-4">
      {tab === "members" && (
        <div className="space-y-8">
          <Members project={project} />
          {s.has_hardware && (
            <div>
              <h2 className="mb-1 text-sm font-semibold">{tr("project_settings.fine_grained_grants")}</h2>
              <p className="mb-2 text-xs text-muted">{tr("project_settings.grant_single_locations_or_items_to_somebody_w")}</p>
              <ResourceGrants project={project} />
            </div>
          )}
        </div>
      )}
      {tab === "general" && (
      <Section title={tr("project_settings.general")}>
        <Check label={tr("project_settings.project_hardware")} hint={tr("project_settings.shows_the_hardware_tab_catalog_items_procurem")}
          on={s.has_hardware} onChange={(v) => set({ has_hardware: v })} />
        <Check label={tr("project_settings.managed_project")} hint={tr("project_settings.results_go_to_review_first_instead_of_straigh")}
          on={s.managed} onChange={(v) => set({ managed: v })} />
        <Check label="PM-Chat" hint={tr("project_settings.chat_tab_for_delegating_to_the_project_manage")}
          on={s.pm_chat_enabled} onChange={(v) => set({ pm_chat_enabled: v })} />
        <Check label={tr("project_settings.review_gate")} hint={tr("project_settings.a_reviewing_agent_reads_the_diff_before_the_w")}
          on={s.review_enabled} onChange={(v) => set({ review_enabled: v })} />
        <Check label={tr("project_settings.continue_automatically")} hint={tr("project_settings.exhausted_runs_continue_until_the_limit_kicks")}
          on={s.auto_continue} onChange={(v) => set({ auto_continue: v })} />
        <Check label={tr("project_settings.allow_screenshots")} hint={tr("project_settings.agents_may_look_at_the_test_environment")}
          on={s.screenshot_enabled} onChange={(v) => set({ screenshot_enabled: v })} />
        <Field label={tr("project_settings.check_command_verify_command")}
          hint={tr("project_settings.hint_verify")}
          value={s.verify_command} onChange={(v) => set({ verify_command: v })} />
        <Field label={tr("project_settings.working_directory")} hint={tr("project_settings.stack_directory_on_the_host_for_deploying")}
          value={s.workspace_dir} onChange={(v) => set({ workspace_dir: v })} />
        <Field label={tr("project_settings.vault_moc_path")} hint={tr("project_settings.vault_note_agents_read_as_project_context")}
          value={s.vault_moc_path} onChange={(v) => set({ vault_moc_path: v })} />
        <Field label={tr("project_settings.additional_system_prompt")} textarea
          hint={tr("project_settings.hint_system_prompt")}
          value={s.system_prompt} onChange={(v) => set({ system_prompt: v })} />
      </Section>
      )}

      {tab === "general" && project.parent_id != null && (
        <Section title={tr("project_settings.inheritance")}>
          <Check label={tr("project_settings.inherit_permissions_from_the_parent_project")}
            hint={tr("project_settings.off_members_of_the_parent_project_do_not_see")}
            on={inheritMembers} onChange={saveInherit} />
        </Section>
      )}

      {tab === "general" && project.my_role === "owner" && (
        <div className="rounded-lg border border-red-500/40 bg-card p-4">
          <div className="mb-2 text-sm font-medium text-red-400">{tr("project_settings.danger_zone")}</div>
          <p className="mb-2 text-xs text-muted">
            {tr("project_settings.delete_project_project_all", { project: project.name, key: project.key })}
          </p>
          <div className="flex items-center gap-2">
            <input value={delConfirm} onChange={(e) => setDelConfirm(e.target.value)} placeholder={project.key}
              className="w-40 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
            <button onClick={delProject} disabled={delConfirm !== project.key}
              className={BUTTON.danger}>
              {tr("project_settings.delete_project")}</button>
          </div>
        </div>
      )}

      {tab === "agents" && (
        <div className="space-y-4">
          <Section title={tr("project_settings.role_mapping")}>
            <p className="text-xs text-muted">{tr("project_settings.which_role_plans_and_which_executes_save_afte")}</p>
            <div className="grid grid-cols-2 gap-2">
              <Select label={tr("project_settings.planning_agent")} value={s.plan_agent} onChange={(v) => set({ plan_agent: v })} />
              <Select label={tr("project_settings.execution_agent")} value={s.exec_agent} onChange={(v) => set({ exec_agent: v })} />
            </div>
            <div>
              <label className="text-xs text-muted">{tr("project_settings.default_subscription_overrides_your_personal")}</label>
              <select
                value={s.default_provider && s.default_token_name ? `${s.default_provider}|${s.default_token_name}` : ""}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) { set({ default_provider: "", default_token_name: "" }); return; }
                  const i = v.indexOf("|");
                  set({ default_provider: v.slice(0, i), default_token_name: v.slice(i + 1) });
                }}
                className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
                <option value="">{tr("project_settings.personal_default")}</option>
                {myTokens?.map((t) => (
                  <option key={t.id} value={`${t.provider}|${t.name}`}>
                    {PROVIDER_LABEL[t.provider] || t.provider} · {t.name}{t.is_default ? ` (${tr("project_settings.default")})` : ""}
                  </option>
                ))}
              </select>
              <div className="mt-0.5 text-xs text-muted">
                {tr("project_settings.which_subscription_token_project")}
              </div>
            </div>
            <button onClick={save} className={BUTTON.primary}>{tr("project_settings.save_mapping")}</button>
            {msg && <span className="ml-2 text-sm text-green-400">{msg}</span>}
          </Section>
          <div className="rounded-lg border border-line bg-card p-4">
            <AgentsPanel projectId={project.id} />
          </div>
        </div>
      )}

      {tab === "board" && <StatusManager project={project} />}
      {tab === "fields" && <ProjectFields project={project} />}

      {tab === "destinations" && <DestinationsArea projectId={project.id} />}
      {/* Slots and own flows of the project: which graph runs on which occasion. */}
      {tab === "processes" && (
        <div className="space-y-8">
          <SlotList project={project} />
          <div>
            <h3 className="mb-2 text-sm font-semibold">{tr("project_view.own_flows")}</h3>
            <WorkflowList project={project} />
          </div>
        </div>
      )}

      {tab === "git" && (
      <Section title={tr("project_settings.git")}>
        <Check label={tr("project_settings.git_enabled")} hint={tr("project_settings.agents_work_in_the_repository_not_only_in_the")}
          on={s.git_enabled} onChange={(v) => set({ git_enabled: v })} />
        <Field label={tr("project_settings.repository")} hint={tr("project_settings.e_g_https_github_com_user_repo_git")}
          value={s.github_repo} onChange={(v) => set({ github_repo: v })} />
        <div className="grid grid-cols-2 gap-2">
          <Field label={tr("project_settings.default_branch_base_and_target")}
            hint={tr("project_settings.hint_branch")}
            value={s.merge_target} onChange={(v) => set({ merge_target: v })} />
          <div>
            <label className="text-xs text-muted">Git-Token
              {s.git_token_set && <span className="ml-1 text-green-400">· gesetzt</span>}</label>
            <div className="mt-1 flex gap-1">
              <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_…" className="w-full rounded border border-line bg-surface px-2 py-1.5" />
              <button onClick={saveToken} className={BUTTON_SMALL.secondary}>OK</button>
            </div>
          </div>
        </div>
        <Check label={tr("project_settings.work_branches")}
          hint={tr("project_settings.every_ticket_gets_its_own_branch_and_worktree")}
          on={s.work_in_branches} onChange={(v) => set({ work_in_branches: v })} />
        <Check label={tr("project_settings.push_after_the_merge")} hint={tr("project_settings.the_result_lands_in_the_remote_directly")}
          on={s.push_after_merge} onChange={(v) => set({ push_after_merge: v })} />
        <Check label={tr("project_settings.pull_request_instead_of_merge")}
          hint={tr("project_settings.hint_pr")}
          on={s.use_pull_request} onChange={(v) => set({ use_pull_request: v })} />
      </Section>
      )}

      {tab === "testenv" && (
      <Section title={tr("project_settings.test_environment_2")}>
        <Check label={tr("project_settings.test_environment_step")}
          hint={tr("project_settings.finished_work_lands_testing")}
          on={s.testenv_enabled} onChange={(v) => set({ testenv_enabled: v })} />
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs text-muted">{tr("project_settings.start_type")}</label>
            <select value={s.testenv_mode} onChange={(e) => set({ testenv_mode: e.target.value })}
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 text-ink">
              <option value="compose">compose.preview.yml</option>
              <option value="dockerfile">{tr("project_settings.build_dockerfile")}</option>
            </select>
          </div>
          {s.testenv_mode === "dockerfile" && (
            <div>
              <label className="text-xs text-muted">{tr("project_settings.port_inside_container")}</label>
              <input type="number" value={s.testenv_container_port}
                onChange={(e) => set({ testenv_container_port: +e.target.value })}
                className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5" />
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label={tr("project_settings.compose_file")} hint={tr("project_settings.relative_worktree")}
            value={s.testenv_compose_file} onChange={(v) => set({ testenv_compose_file: v })} />
          <Field label={tr("project_settings.dockerfile")} hint={tr("project_settings.only_dockerfile_mode")}
            value={s.testenv_dockerfile} onChange={(v) => set({ testenv_dockerfile: v })} />
        </div>
        <Field label={tr("project_settings.url_template")} hint={tr("project_settings.placeholders_host_global_setting")}
          value={s.testenv_url_template} onChange={(v) => set({ testenv_url_template: v })} />
        <Field label={tr("project_settings.preparation_commands")} textarea
          hint={tr("project_settings.run_inside_the_worktree_before_the_build_one")}
          value={s.testenv_prestart} onChange={(v) => set({ testenv_prestart: v })} />
        <Field label={tr("project_settings.demo_login_json")} hint={tr("project_settings.hint_demo_login")}
          value={s.testenv_demo_login} onChange={(v) => set({ testenv_demo_login: v })} />
        <div>
          <label className="text-xs text-muted">{tr("project_settings.environment_variables")}
            {s.testenv_env_set && <span className="ml-1 text-green-400">· {tr("project_settings.stored")}</span>}</label>
          <textarea value={envText} onChange={(e) => setEnvText(e.target.value)} rows={3}
            placeholder={"KEY=wert\nANDERER=wert"}
            className="mt-1 w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs" />
          <div className="mt-1 flex items-center gap-2">
            <button onClick={saveEnv} className={BUTTON_SMALL.secondary}>
              {tr("project_settings.save_encrypted")}</button>
            <span className="text-xs text-muted">{tr("project_settings.not_shown_again_after_saving")}</span>
          </div>
        </div>
      </Section>
      )}

      {tab === "deployment" && (
      <>
      <Section title={tr("project_settings.deployment")}>
        <Check label={tr("project_settings.deploy_automatically")} hint={tr("project_settings.hint_auto_deploy")}
          on={s.auto_deploy} onChange={(v) => set({ auto_deploy: v })} />
        {s.auto_deploy && s.use_pull_request && (
          <div className="text-xs text-yellow-400">
            {tr("project_settings.note_pull_request_instead")}
          </div>
        )}
      </Section>
      {/* The switch and what follows from it on one page — the full list right below it.
          The button stands here on purpose and not in the dashboard: whoever rolls out by hand
          should have the auto-deploy switch and the past runs in the same glance.
          `workspace_dir` comes out of the loaded settings (not out of `project`), which
          role out of the project — the server checks both again. */}
      <Section title={tr("project_settings.past_deployments")}>
        <DeploymentsPanel projectId={project.id} variant="voll"
          fire={{
            stackDir: s.workspace_dir,
            allowed: project.my_role === "maintainer" || project.my_role === "owner",
          }} />
      </Section>
      </>
      )}
      </div>

      {showSave && (
        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} className={BUTTON.primary}>{tr("project_settings.save")}</button>
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
