import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Issue, Project, ProjectMeta } from "../api";
import { waitInfo } from "../lib/waitReason";
import { ticketOpenHandlers, type OnOpenTicket } from "../ticketOpen";

const PRIO_FARBE: Record<string, string> = {
  highest: "text-red-400", high: "text-orange-400",
  medium: "text-muted", low: "text-muted", lowest: "text-muted",
};

export default function Backlog({
  project, meta, issues, onOpen,
}: {
  project: Project; meta: ProjectMeta; issues: Issue[]; onOpen: OnOpenTicket;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["issues", project.id] });
    qc.invalidateQueries({ queryKey: ["meta", project.id] });
  };
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const setSprint = useMutation({
    mutationFn: (v: { key: string; sprint_id: number | null }) =>
      api.put(`/issues/${v.key}`, { sprint_id: v.sprint_id }),
    onSuccess: inv, onError: fehler,
  });
  const neu = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/sprints`, { name }),
    onSuccess: () => { setName(""); inv(); }, onError: fehler,
  });
  const aktion = useMutation({
    mutationFn: (v: { id: number; was: "start" | "complete" }) =>
      api.post(`/projects/${project.id}/sprints/${v.id}/${v.was}`),
    onSuccess: inv, onError: fehler,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/projects/${project.id}/sprints/${id}`),
    onSuccess: inv, onError: fehler,
  });

  const offeneSprints = (meta.sprints || []).filter((s: any) => s.state !== "closed");
  const backlog = issues.filter((i) => !i.sprint_id);

  const Zeile = (i: Issue) => (
    <div key={i.id} className="flex items-center gap-3 rounded border border-line bg-card px-2 py-1.5 text-sm">
      <button {...ticketOpenHandlers(i.key, onOpen)} className="font-mono text-xs text-muted hover:text-brand">{i.key}</button>
      <span className="flex-1 truncate">{i.summary}</span>
      {(() => { const w = waitInfo(i); return w && (
        <span title={`${w.title}: ${w.label}`} className="text-xs">{w.icon}</span>
      ); })()}
      {i.assigned_agent && <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">🤖 {i.assigned_agent}</span>}
      <span className={`text-xs ${PRIO_FARBE[i.priority] || "text-muted"}`}>{i.priority}</span>
      <select value={i.sprint_id ?? ""} onChange={(e) =>
        setSprint.mutate({ key: i.key, sprint_id: e.target.value ? +e.target.value : null })}
        className="rounded border border-line bg-surface px-1.5 py-0.5 text-xs text-ink">
        <option value="">Backlog</option>
        {offeneSprints.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
    </div>
  );

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {err && <div className="text-sm text-red-400">{err}</div>}

      {offeneSprints.map((s: any) => {
        const drin = issues.filter((i) => i.sprint_id === s.id);
        const fertig = drin.filter((i) => i.resolved_at).length;
        return (
          <section key={s.id} className="rounded-lg border border-line p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="font-medium">{s.name}</span>
              {s.state === "active"
                ? <span className="rounded bg-green-500/20 px-1.5 text-xs text-green-400">läuft</span>
                : <span className="rounded bg-surface px-1.5 text-xs text-muted">geplant</span>}
              <span className="text-xs text-muted">{drin.length} Tickets · {fertig} fertig</span>
              <div className="flex-1" />
              {s.state === "active" ? (
                <button onClick={() => aktion.mutate({ id: s.id, was: "complete" })}
                  className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
                  Abschließen</button>
              ) : (
                <button onClick={() => aktion.mutate({ id: s.id, was: "start" })}
                  className="rounded bg-brand px-2 py-1 text-xs text-white">Starten</button>
              )}
              {!drin.length && (
                <button onClick={() => loeschen.mutate(s.id)}
                  className="text-xs text-muted hover:text-red-400">löschen</button>
              )}
            </div>
            <div className="space-y-1">
              {drin.length ? drin.map(Zeile)
                : <div className="text-xs text-muted">Noch nichts zugeordnet.</div>}
            </div>
          </section>
        );
      })}

      <section className="rounded-lg border border-line p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="font-medium">Backlog</span>
          <span className="text-xs text-muted">{backlog.length} Tickets</span>
          <div className="flex-1" />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Neuer Sprint"
            className="rounded border border-line bg-surface px-2 py-1 text-xs" />
          <button onClick={() => name.trim() && neu.mutate()}
            className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">+ Sprint</button>
        </div>
        <div className="space-y-1">
          {backlog.length ? backlog.map(Zeile)
            : <div className="text-xs text-muted">Backlog ist leer.</div>}
        </div>
      </section>
    </div>
  );
}
