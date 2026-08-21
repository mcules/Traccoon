import { useEffect, useMemo } from "react";
import { tr } from "../../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import "@xyflow/react/dist/style.css";
import { workflowApi, getToken, type WorkflowInstance } from "../../api";
import WorkflowCanvas from "./WorkflowCanvas";
import { graphToFlow } from "./convert";
import { runtimeStates } from "./runtimeState";
import Steplog from "./StepLog";
import { needsLayout, layoutGraph, DEFAULT_GAP } from "./layout";

const STATUS_LABEL: Record<string, string> = {
  running: "instance.running",
  waiting: "instance.waiting",
  completed: "instance.completed",
  failed: "instance.failed",
  cancelled: "instance.cancelled",
};
const STATUS_COLOR: Record<string, string> = {
  running: "text-sky-400",
  waiting: "text-yellow-400",
  completed: "text-green-400",
  failed: "text-red-400",
  cancelled: "text-muted",
};

/** Read-only graph of a running or finished instance with the progress highlighted. */
export default function WorkflowInstanceView({
  iid,
  projectId,
  height = "360px",
  compact,
}: {
  iid: number;
  projectId?: number | null;
  height?: string;
  compact?: boolean;
}) {
  const qc = useQueryClient();
  const { data: instance } = useQuery({
    queryKey: ["workflow-instance", iid],
    queryFn: () => workflowApi.instance(iid),
    refetchInterval: 6000, // Fallback, falls WS nicht greift
  });

  // WS-Live: Projekt-Kanal invalidiert diese Instanz-Query.
  useEffect(() => {
    if (!projectId) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${proto}://${location.host}/api/projects/${projectId}/ws?token=${getToken()}`);
      ws.onmessage = () => qc.invalidateQueries({ queryKey: ["workflow-instance", iid] });
    } catch {
      /* Polling-Fallback greift */
    }
    return () => ws?.close();
  }, [projectId, iid]);

  const flow = useMemo(() => {
    if (!instance) return null;
    let graph = instance.graph;
    if (needsLayout(graph)) graph = layoutGraph(graph);
    return graphToFlow(graph, runtimeStates(instance as WorkflowInstance));
  }, [instance]);

  if (!instance || !flow) return <div className="text-xs text-muted">{tr("workflow_instance_view.loading")}</div>;

  return (
    <div>
      {!compact && (
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span className="text-muted">Prozess-Instanz #{instance.id}</span>
          <span className={STATUS_COLOR[instance.status] || "text-muted"}>
            ● {STATUS_LABEL[instance.status] ? tr(STATUS_LABEL[instance.status]) : instance.status}
          </span>
          {instance.error && <span className="text-red-400">— {instance.error}</span>}
        </div>
      )}
      {/* The graph shows where the run stands — the log shows what came out of it.
          Expanded in a list the log counts: "where does it stand?" is answered by the
          line above has long done, "what came back?" nobody so far. */}
      {compact ? (
        <>
          <Steplog steps={instance.steps} maxHeight="16rem"
            emptyText={tr("instance.no_step_finished_yet")} />
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-muted">{tr("workflow_instance_view.flow_graph")}</summary>
            <div className="mt-1 overflow-hidden rounded-lg border border-line" style={{ height }}>
              <WorkflowCanvas nodes={flow.nodes} edges={flow.edges} readOnly />
            </div>
          </details>
        </>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-line" style={{ height }}>
            <WorkflowCanvas nodes={flow.nodes} edges={flow.edges} readOnly />
          </div>
          <details className="mt-2" open>
            <summary className="cursor-pointer text-xs text-muted">
              Verlauf — {instance.steps.length} Schritt{instance.steps.length === 1 ? "" : "e"}
            </summary>
            <div className="mt-1">
              <Steplog steps={instance.steps} maxHeight="20rem"
                emptyText={tr("instance.no_step_finished_yet")} />
            </div>
          </details>
        </>
      )}
    </div>
  );
}
