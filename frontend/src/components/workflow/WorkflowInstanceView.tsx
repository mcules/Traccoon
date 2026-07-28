import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import "@xyflow/react/dist/style.css";
import { workflowApi, getToken, type WorkflowInstance } from "../../api";
import WorkflowCanvas from "./WorkflowCanvas";
import { graphToFlow } from "./convert";
import { runtimeStates } from "./runtimeState";
import { needsLayout, layoutGraph, DEFAULT_GAP } from "./layout";

const STATUS_LABEL: Record<string, string> = {
  running: "läuft",
  waiting: "wartet",
  completed: "abgeschlossen",
  failed: "fehlgeschlagen",
  cancelled: "abgebrochen",
};
const STATUS_COLOR: Record<string, string> = {
  running: "text-sky-400",
  waiting: "text-yellow-400",
  completed: "text-green-400",
  failed: "text-red-400",
  cancelled: "text-muted",
};

/** Read-only-Graph einer laufenden/erledigten Instanz mit hervorgehobenem Fortschritt. */
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

  if (!instance || !flow) return <div className="text-xs text-muted">Lädt…</div>;

  return (
    <div>
      {!compact && (
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span className="text-muted">Prozess-Instanz #{instance.id}</span>
          <span className={STATUS_COLOR[instance.status] || "text-muted"}>
            ● {STATUS_LABEL[instance.status] || instance.status}
          </span>
          {instance.error && <span className="text-red-400">— {instance.error}</span>}
        </div>
      )}
      <div className="overflow-hidden rounded-lg border border-line" style={{ height }}>
        <WorkflowCanvas nodes={flow.nodes} edges={flow.edges} readOnly />
      </div>
    </div>
  );
}
