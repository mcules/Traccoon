import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import Onboarding from "../components/Onboarding";

export default function Projects() {
  const qc = useQueryClient();
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [managed, setManaged] = useState(false);
  const [err, setErr] = useState("");

  const create = useMutation({
    mutationFn: () => api.post<Project>("/projects", { name, managed }),  // Key generiert der Server
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setShow(false); setName(""); setManaged(false); setErr("");
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  function submit(e: FormEvent) { e.preventDefault(); create.mutate(); }

  return (
    <div>
      <Onboarding />
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Projekte</h1>
        <button onClick={() => setShow(!show)} className="rounded bg-brand px-3 py-1.5 text-white">
          + Projekt
        </button>
      </div>

      {show && (
        <form onSubmit={submit} className="mb-5 flex flex-wrap items-end gap-3 rounded-lg border border-line bg-card p-4">
          <label className="flex flex-1 flex-col text-xs text-muted">Name
            <input className="mt-1 rounded border border-line bg-surface px-2 py-1.5 text-ink"
              value={name} onChange={(e) => setName(e.target.value)} placeholder="Projektname" />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={managed} onChange={(e) => setManaged(e.target.checked)} />
            KI-gemanagt
          </label>
          <button className="rounded bg-brand px-3 py-1.5 text-white">Anlegen</button>
          {err && <span className="text-sm text-red-400">{err}</span>}
        </form>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {projects?.map((p) => (
          <Link key={p.id} to={`/projects/${p.key}`}
            className="rounded-lg border border-line bg-card p-4 hover:border-brand">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-muted">{p.key}</span>
              <div className="flex gap-1">
                {p.managed && <span className="rounded bg-brand/20 px-1.5 py-0.5 text-xs text-brand">KI</span>}
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{p.my_role}</span>
              </div>
            </div>
            <div className="mt-1 font-medium">{p.name}</div>
            {!p.my_ai_assign && (
              <div className="mt-2 text-xs text-muted">Ticketsystem (kein KI-Recht)</div>
            )}
          </Link>
        ))}
        {projects?.length === 0 && <div className="text-muted">Noch keine Projekte.</div>}
      </div>
    </div>
  );
}
