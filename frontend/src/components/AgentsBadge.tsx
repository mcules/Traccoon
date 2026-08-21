import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { BUTTON_TEXT, BUTTON } from "./ui";

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
    try { setErr(""); await fn(); inv(); } catch (e) { setErr(e instanceof ApiError ? e.message : tr("common.error")); }
  };
  const update = useMutation({ mutationFn: () => api.post("/admin/update"), onSuccess: inv,
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")) });
  const cancel = useMutation({ mutationFn: () => api.post("/admin/update/cancel"), onSuccess: inv,
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")) });

  const n = data?.running_agents ?? 0;
  const busy = data?.update_pending || data?.update_in_progress;

  return (
    <div className="relative">
      <button onClick={() => setOpen((v) => !v)} title={tr("agents_badge.running_agents")}
        className={`flex h-10 items-center gap-1 rounded px-2 py-1 text-sm md:h-8 ${busy ? "text-brand" : "text-muted hover:text-ink"}`}>
        <span>{busy ? "🔄" : "🤖"}</span>
        <span className="tabular-nums">{n}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-2 w-72 rounded-lg border border-line bg-card p-3 text-sm shadow-2xl">
            <div className="mb-2 font-medium">
              {n === 0 ? tr("agents_badge.no_agents_running") : tr("agents_badge.count_agents_running", { count: n })}
            </div>

            {data?.update_in_progress ? (
              <div className="rounded bg-brand/10 px-2 py-1.5 text-brand">{tr("agents_badge.update_running_the_stack_is_being_redeployed")}</div>
            ) : data?.update_pending ? (
              <div className="space-y-2">
                <div className="rounded bg-yellow-500/10 px-2 py-1.5 text-yellow-300">
                  ⏳ {tr("agents_badge.update_queued_starts_once")}
                  {n > 0 ? ` (${tr("agents_badge.count_left", { count: n })}).` : "."}
                </div>
                {isAdmin && (
                  <button onClick={() => guard(() => cancel.mutateAsync())}
                    className={BUTTON_TEXT.danger}>{tr("agents_badge.cancel_update")}</button>
                )}
              </div>
            ) : isAdmin ? (
              data?.maintenance_project_id ? (
                <button onClick={() => guard(() => update.mutateAsync())} disabled={update.isPending}
                  className={BUTTON.primary}>
                  ⬆ {tr("agents_badge.queue_update_project", { project: data.maintenance_project_key || "" })}
                </button>
              ) : (
                <div className="text-xs text-muted">
                  {tr("agents_badge.no_maintenance_project_set")}
                </div>
              )
            ) : (
              <div className="text-xs text-muted">{tr("agents_badge.updates_are_triggered_by_an_admin")}</div>
            )}
            {err && <div className="mt-2 text-xs text-red-400">{err}</div>}
          </div>
        </>
      )}
    </div>
  );
}
