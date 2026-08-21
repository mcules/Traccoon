import { useMemo } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api, ApiError, workflowApi,
  type ProjectMeta, type WorkflowInstance, type WorkflowStepRun, type NodeConfig,
} from "../api";
import WorkflowInstanceView from "./workflow/WorkflowInstanceView";
import WorkflowTaskForm from "./workflow/WorkflowTaskForm";
import { BUTTON } from "./ui";

const STATUS_LABEL: Record<string, string> = {
  running: "instance.running", waiting: "instance.waiting", completed: "instance.completed",
  failed: "fehlgeschlagen", cancelled: "abgebrochen",
};
const STATUS_COLOR: Record<string, string> = {
  running: "text-sky-400", waiting: "text-yellow-400", completed: "text-green-400",
  failed: "text-red-400", cancelled: "text-muted",
};

/**
 * Workflow-Ansicht eines Hardware-Exemplars (Etappe 4, Dual-Run — additiv neben AssetProcurement).
 * - Without an instance and with a project: the start button for the procurement workflow instance.
 * - Without an instance and without a project (stock, storage): a hint.
 * - With an instance: the read-only graph (WorkflowInstanceView) plus open steps (WorkflowTaskForm).
 * Assumption: with several instances the newest one (the highest id) counts.
 */
export default function AssetWorkflow({
  assetId, projectId, assetLabel,
}: {
  assetId: number;
  projectId: number | null;
  assetLabel?: string;
}) {
  const qc = useQueryClient();

  const { data: instances, isLoading, error } = useQuery({
    queryKey: ["asset-workflow", assetId],
    queryFn: () => workflowApi.instancesForSubject(`hardware_asset:${assetId}`),
    refetchInterval: 8000, // fallback; the instance itself is additionally invalidated by WS.
  });

  // Neueste Instanz gewinnt.
  const latest = useMemo<WorkflowInstance | null>(() => {
    if (!instances || instances.length === 0) return null;
    return [...instances].sort((a, b) => b.id - a.id)[0];
  }, [instances]);

  const start = useMutation({
    mutationFn: () =>
      api.post<{ instance_id: number; status: string; definition_id: number }>(
        `/hardware/assets/${assetId}/workflow`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["asset-workflow", assetId] }),
  });

  if (isLoading) return <div className="text-xs text-muted">{tr("asset_workflow.loading")}</div>;
  if (error) {
    return (
      <div className="text-xs text-red-400">
        {error instanceof ApiError ? error.message : tr("asset_workflow.process_not_loaded")}
      </div>
    );
  }

  // ── No instance ──────────────────────────────────────────────────────────────
  if (!latest) {
    if (projectId == null) {
      return (
        <div className="rounded border border-line bg-surface p-2 text-xs text-muted">
          {tr("asset_workflow.stock_storage_without_project")}
        </div>
      );
    }
    return (
      <div className="space-y-2">
        <button
          onClick={() => start.mutate()}
          disabled={start.isPending}
          className={BUTTON.primary}
        >
          🧭 {tr("asset_workflow.start_procurement_process")}
        </button>
        {start.error && (
          <div className="text-xs text-red-400">
            {start.error instanceof ApiError ? start.error.message : "Start fehlgeschlagen."}
          </div>
        )}
        <p className="text-xs text-muted">
          {tr("asset_workflow.starts_project_wide_procurement")}
          {assetLabel ? ` (${assetLabel})` : ""}.
        </p>
      </div>
    );
  }

  return <InstancePanel instance={latest} projectId={projectId} assetId={assetId} />;
}

/** Read-only-Graph + offene Schritte einer konkreten Instanz. */
function InstancePanel({
  instance: initial, projectId, assetId,
}: {
  instance: WorkflowInstance;
  projectId: number | null;
  assetId: number;
}) {
  const qc = useQueryClient();
  const iid = initial.id;

  // Live instance (a query key shared with WorkflowInstanceView, so WS plus polling take hold).
  const { data: live } = useQuery({
    queryKey: ["workflow-instance", iid],
    queryFn: () => workflowApi.instance(iid),
    initialData: initial,
    refetchInterval: 6000,
  });
  const instance = live ?? initial;

  const { data: meta } = useQuery({
    queryKey: ["meta", projectId],
    queryFn: () => api.get<ProjectMeta>(`/projects/${projectId}/meta`),
    enabled: projectId != null,
  });

  // node_id to NodeConfig from the pinned graph.
  const configByNode = useMemo(() => {
    const m: Record<string, NodeConfig> = {};
    for (const n of instance.graph?.nodes ?? []) m[n.id] = n.data.config;
    return m;
  }, [instance.graph]);

  // Open, editable steps: waiting human_task/approval steps.
  const openSteps = useMemo(
    () =>
      (instance.steps ?? []).filter(
        (s): s is WorkflowStepRun =>
          s.status === "waiting" && (s.node_type === "human_task" || s.node_type === "approval"),
      ),
    [instance.steps],
  );

  const onDone = () => qc.invalidateQueries({ queryKey: ["asset-workflow", assetId] });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-muted">Prozess-Instanz #{instance.id}</span>
        <span className={STATUS_COLOR[instance.status] || "text-muted"}>
          ● {STATUS_LABEL[instance.status] || instance.status}
        </span>
        {instance.error && <span className="text-red-400">— {instance.error}</span>}
      </div>

      <WorkflowInstanceView iid={iid} projectId={projectId} height="260px" compact />

      {openSteps.length > 0 ? (
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted">{tr("asset_workflow.open_steps")}</div>
          {openSteps.map((s) => {
            const cfg = configByNode[s.node_id] ?? {};
            return (
              <div key={s.id} className="rounded border border-line bg-surface p-2.5">
                <div className="mb-1.5 text-sm text-ink">
                  {cfg.label || (s.node_type === "approval" ? "Freigabe" : tr("asset_workflow.task"))}
                </div>
                <WorkflowTaskForm
                  iid={iid}
                  sid={s.id}
                  nodeType={s.node_type as "human_task" | "approval"}
                  config={cfg}
                  members={meta?.members || []}
                  onDone={onDone}
                />
              </div>
            );
          })}
        </div>
      ) : (
        instance.status !== "completed" &&
        instance.status !== "cancelled" && (
          <div className="text-xs text-muted">{tr("asset_workflow.no_step_is_waiting_for_you_right_now")}</div>
        )
      )}
    </div>
  );
}
