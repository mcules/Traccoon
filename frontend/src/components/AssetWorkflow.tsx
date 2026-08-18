import { useMemo } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api, ApiError, workflowApi,
  type ProjectMeta, type WorkflowInstance, type WorkflowStepRun, type NodeConfig,
} from "../api";
import WorkflowInstanceView from "./workflow/WorkflowInstanceView";
import WorkflowTaskForm from "./workflow/WorkflowTaskForm";

const STATUS_LABEL: Record<string, string> = {
  running: "läuft", waiting: "wartet", completed: "abgeschlossen",
  failed: "fehlgeschlagen", cancelled: "abgebrochen",
};
const STATUS_COLOR: Record<string, string> = {
  running: "text-sky-400", waiting: "text-yellow-400", completed: "text-green-400",
  failed: "text-red-400", cancelled: "text-muted",
};

/**
 * Workflow-Ansicht eines Hardware-Exemplars (Etappe 4, Dual-Run — additiv neben AssetProcurement).
 * - Ohne Instanz + mit Projekt: Startbutton für die Beschaffungs-Workflow-Instanz.
 * - Ohne Instanz + ohne Projekt (Vorrat/Lager): Hinweis.
 * - Mit Instanz: Read-only-Graph (WorkflowInstanceView) + offene Schritte (WorkflowTaskForm).
 * Annahme: Bei mehreren Instanzen zählt die neueste (höchste id).
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
    refetchInterval: 8000, // Fallback; die Instanz selbst wird zusätzlich per WS invalidiert.
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

  if (isLoading) return <div className="text-xs text-muted">{tr("asset_workflow.laedt")}</div>;
  if (error) {
    return (
      <div className="text-xs text-red-400">
        {error instanceof ApiError ? error.message : "Workflow konnte nicht geladen werden."}
      </div>
    );
  }

  // ── Keine Instanz ────────────────────────────────────────────────────────────
  if (!latest) {
    if (projectId == null) {
      return (
        <div className="rounded border border-line bg-surface p-2 text-xs text-muted">
          Vorrat/Lager ohne Projekt — kein Workflow.
        </div>
      );
    }
    return (
      <div className="space-y-2">
        <button
          onClick={() => start.mutate()}
          disabled={start.isPending}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50"
        >
          🧭 Beschaffung als Workflow starten
        </button>
        {start.error && (
          <div className="text-xs text-red-400">
            {start.error instanceof ApiError ? start.error.message : "Start fehlgeschlagen."}
          </div>
        )}
        <p className="text-xs text-muted">
          Startet den projektweiten Beschaffungsprozess für dieses Exemplar
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

  // Live-Instanz (geteilter Query-Key mit WorkflowInstanceView → WS + Polling greifen).
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

  // node_id → NodeConfig aus dem gepinnten Graph.
  const configByNode = useMemo(() => {
    const m: Record<string, NodeConfig> = {};
    for (const n of instance.graph?.nodes ?? []) m[n.id] = n.data.config;
    return m;
  }, [instance.graph]);

  // Offene, bearbeitbare Schritte: wartende human_task/approval-Steps.
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
          <div className="text-xs font-medium text-muted">{tr("asset_workflow.offene_schritte")}</div>
          {openSteps.map((s) => {
            const cfg = configByNode[s.node_id] ?? {};
            return (
              <div key={s.id} className="rounded border border-line bg-surface p-2.5">
                <div className="mb-1.5 text-sm text-ink">
                  {cfg.label || (s.node_type === "approval" ? "Freigabe" : "Aufgabe")}
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
          <div className="text-xs text-muted">{tr("asset_workflow.aktuell_kein_schritt_der_auf_dich_wartet")}</div>
        )
      )}
    </div>
  );
}
