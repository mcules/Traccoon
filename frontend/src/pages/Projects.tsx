import { FormEvent, useState } from "react";
import { tr } from "../i18n";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import Onboarding from "../components/Onboarding";
import MyWork from "../components/MyWork";
import { Tag, BUTTON} from "../components/ui";
import { usePageChrome } from "../pageChrome";

export default function Projects() {
  // Title without a sub-menu; otherwise the one of the last visited page would stay.
  usePageChrome(tr("nav.projects"), []);
  const qc = useQueryClient();
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [managed, setManaged] = useState(false);
  const [parentId, setParentId] = useState("");
  const [err, setErr] = useState("");

  const create = useMutation({
    mutationFn: () => api.post<Project>("/projects", {
      name, managed, parent_id: parentId ? Number(parentId) : null,
    }),  // Key generiert der Server
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setShow(false); setName(""); setManaged(false); setParentId(""); setErr("");
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")),
  });

  function submit(e: FormEvent) { e.preventDefault(); create.mutate(); }
  const parentName = (id?: number | null) => (id ? projects?.find((p) => p.id === id)?.name : null);

  return (
    <div>
      <Onboarding />
      <MyWork />
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">{tr("projects.projects")}</h1>
        <button onClick={() => setShow(!show)} className={BUTTON.primary}>
          {tr("projects.new_project")}
        </button>
      </div>

      {show && (
        <form onSubmit={submit} className="mb-5 flex flex-wrap items-end gap-3 rounded-lg border border-line bg-card p-4">
          <label className="flex flex-1 flex-col text-xs text-muted">Name
            <input className="mt-1 rounded border border-line bg-surface px-2 py-1.5 text-ink"
              value={name} onChange={(e) => setName(e.target.value)} placeholder={tr("projects.project_name")} />
          </label>
          <label className="flex flex-col text-xs text-muted">{tr("projects.parent_project")}
            <select value={parentId} onChange={(e) => setParentId(e.target.value)}
              className="mt-1 rounded border border-line bg-surface px-2 py-1.5 text-ink">
              <option value="">{tr("projects.standalone_option")}</option>
              {projects?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={managed} onChange={(e) => setManaged(e.target.checked)} />
            KI-gemanagt
          </label>
          <button className={BUTTON.primary}>{tr("projects.create")}</button>
          {err && <span className="text-sm text-red-400">{err}</span>}
        </form>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {projects?.map((p) => (
          <Link key={p.id} to={`/projects/${p.key}`}
            className="block rounded-lg border border-line bg-card p-4 hover:border-brand">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-muted">{p.key}</span>
              <div className="flex gap-1">
                {p.is_new && <Tag color="green">{tr("projects.new")}</Tag>}
                {!p.is_member && <Tag color="yellow">{tr("projects.foreign")}</Tag>}
                {p.managed && <Tag color="brand">{tr("projects.ai_managed")}</Tag>}
                <Tag>
                  {p.my_role}{p.my_role_inherited ? ` (${tr("projects.inherited")})` : ""}
                </Tag>
              </div>
            </div>
            <div className="mt-1 font-medium">{p.name}</div>
            {parentName(p.parent_id) && (
              <div className="text-xs text-muted">↳ {tr("projects.subproject_parent", { parent: parentName(p.parent_id) || "" })}</div>
            )}
            {!p.my_ai_assign && (
              <div className="mt-2 text-xs text-muted">{tr("projects.ticket_system_no_ai_permission")}</div>
            )}
          </Link>
        ))}
        {projects?.length === 0 && <div className="text-muted">{tr("projects.no_projects_yet")}</div>}
      </div>
    </div>
  );
}
