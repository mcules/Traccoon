import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

type Status = {
  running_agents: number; update_pending: boolean; update_in_progress: boolean;
  maintenance_project_id: number | null; maintenance_project_key: string | null;
};

export default function AgentsBadge() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const isAdmin = user?.global_role === "admin";

  const { data } = useQuery({
    queryKey: ["admin-status"], queryFn: () => api.get<Status>("/admin/status"),
    refetchInterval: 5000,
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["admin-status"] });
  const guard = async (fn: () => Promise<any>) => {
    try { setErr(""); await fn(); inv(); } catch (e) { setErr(e instanceof ApiError ? e.message : "Fehler"); }
  };
  const update = useMutation({ mutationFn: () => api.post("/admin/update"), onSuccess: inv,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler") });
  const cancel = useMutation({ mutationFn: () => api.post("/admin/update/cancel"), onSuccess: inv,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler") });

  const n = data?.running_agents ?? 0;
  const busy = data?.update_pending || data?.update_in_progress;

  return (
    <div className="relative">
      <button onClick={() => setOpen((v) => !v)} title={tr("agents_badge.laufende_agenten")}
        className={`flex h-10 items-center gap-1 rounded px-2 py-1 text-sm md:h-8 ${busy ? "text-brand" : "text-muted hover:text-ink"}`}>
        <span>{busy ? "🔄" : "🤖"}</span>
        <span className="tabular-nums">{n}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-2 w-72 rounded-lg border border-line bg-card p-3 text-sm shadow-2xl">
            <div className="mb-2 font-medium">
              {n === 0 ? "Keine Agenten laufen" : `${n} Agent${n === 1 ? "" : "en"} ${n === 1 ? "läuft" : "laufen"}`}
            </div>

            {data?.update_in_progress ? (
              <div className="rounded bg-brand/10 px-2 py-1.5 text-brand">{tr("agents_badge.update_laeuft_stack_wird_neu_deployt")}</div>
            ) : data?.update_pending ? (
              <div className="space-y-2">
                <div className="rounded bg-yellow-500/10 px-2 py-1.5 text-yellow-300">
                  ⏳ Update eingereiht. Es startet, sobald alle Agenten fertig sind
                  {n > 0 ? ` (noch ${n}).` : "."}
                </div>
                {isAdmin && (
                  <button onClick={() => guard(() => cancel.mutateAsync())}
                    className="text-xs text-muted hover:text-red-400">{tr("agents_badge.update_abbrechen")}</button>
                )}
              </div>
            ) : isAdmin ? (
              data?.maintenance_project_id ? (
                <button onClick={() => guard(() => update.mutateAsync())} disabled={update.isPending}
                  className="w-full rounded bg-brand px-3 py-1.5 text-white disabled:opacity-50">
                  ⬆ Update {data.maintenance_project_key} einreihen
                </button>
              ) : (
                <div className="text-xs text-muted">
                  Kein Wartungsprojekt gesetzt. Unter <b>{tr("agents_badge.admin_wartung")}</b> auswählen, welches
                  Projekt sich beim Update selbst deployt.
                </div>
              )
            ) : (
              <div className="text-xs text-muted">{tr("agents_badge.updates_loest_ein_admin_aus")}</div>
            )}
            {err && <div className="mt-2 text-xs text-red-400">{err}</div>}
          </div>
        </>
      )}
    </div>
  );
}
