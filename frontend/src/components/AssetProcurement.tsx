import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

type Step = {
  id: number; name: string; order: number; assignee_id: number | null;
  status: string; note: string | null; completed_at: string | null; completed_by_id: number | null;
};

/** Beschaffungsschritte eines Exemplars: abhaken + optionale Übergabe an die nächste Person. */
export default function AssetProcurement({
  assetId, project, onChange,
}: {
  assetId: number; project: Project; onChange: () => void;
}) {
  const qc = useQueryClient();
  const { data: steps } = useQuery({
    queryKey: ["hw-steps", assetId], queryFn: () => api.get<Step[]>(`/hardware/assets/${assetId}/steps`),
  });
  const { data: meta } = useQuery({
    queryKey: ["meta", project.id], queryFn: () => api.get<any>(`/projects/${project.id}/meta`),
  });
  const [note, setNote] = useState("");
  const [nextAssignee, setNextAssignee] = useState("");
  const [err, setErr] = useState("");
  const members: any[] = meta?.members || [];
  const name = (id: number | null) => members.find((m) => m.user_id === id)?.display_name
    || members.find((m) => m.user_id === id)?.username || (id ? `#${id}` : "—");

  const complete = useMutation({
    mutationFn: (stepId: number) => api.post(`/hardware/assets/${assetId}/steps/${stepId}/complete`, {
      note: note || null, next_assignee: nextAssignee ? Number(nextAssignee) : null,
    }),
    onSuccess: () => {
      setNote(""); setNextAssignee("");
      qc.invalidateQueries({ queryKey: ["hw-steps", assetId] });
      onChange();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  if (!steps) return <div className="text-xs text-muted">Lädt…</div>;
  const naechsterOffen = steps.find((s) => s.status !== "DONE");

  return (
    <div className="space-y-2">
      <ol className="space-y-1.5">
        {steps.map((s) => {
          const done = s.status === "DONE";
          const aktiv = naechsterOffen?.id === s.id;
          return (
            <li key={s.id} className="flex items-start gap-2 text-xs">
              <span className={done ? "text-green-400" : aktiv ? "text-yellow-400" : "text-muted"}>
                {done ? "✓" : aktiv ? "◉" : "○"}
              </span>
              <div className="flex-1">
                <span className={done ? "text-muted line-through" : "text-ink"}>{s.name}</span>
                {s.assignee_id && !done && (
                  <span className="ml-2 rounded bg-surface px-1.5 text-muted">→ {name(s.assignee_id)}</span>
                )}
                {done && (
                  <span className="ml-2 text-muted">
                    {name(s.completed_by_id)}{s.completed_at ? ` · ${s.completed_at.slice(0, 10)}` : ""}
                    {s.note ? ` · „${s.note}“` : ""}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {naechsterOffen ? (
        <div className="rounded border border-line bg-surface p-2">
          <div className="mb-1 text-xs text-muted">Nächster Schritt: <b className="text-ink">{naechsterOffen.name}</b></div>
          <div className="flex flex-wrap items-center gap-2">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Notiz (optional)"
              className="flex-1 rounded border border-line bg-card px-2 py-1 text-xs" />
            <select value={nextAssignee} onChange={(e) => setNextAssignee(e.target.value)}
              className="rounded border border-line bg-card px-2 py-1 text-xs text-ink" title="Übergabe an">
              <option value="">Übergabe an… (optional)</option>
              {members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name || m.username}</option>)}
            </select>
            <button onClick={() => complete.mutate(naechsterOffen.id)}
              className="rounded bg-brand px-3 py-1 text-xs text-white">✓ {naechsterOffen.name} erledigt</button>
          </div>
        </div>
      ) : (
        <div className="text-xs text-green-400">Beschaffung abgeschlossen.</div>
      )}
      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}
