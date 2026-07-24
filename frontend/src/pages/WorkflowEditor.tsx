import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addEdge,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Connection,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ApiError,
  api,
  workflowApi,
  type Project,
  type ProjectMeta,
  type NodeConfig,
  type WorkflowNodeType,
} from "../api";
import WorkflowCanvas from "../components/workflow/WorkflowCanvas";
import NodePalette from "../components/workflow/NodePalette";
import NodeConfigPanel from "../components/workflow/NodeConfigPanel";
import { graphToFlow, flowToGraph } from "../components/workflow/convert";
import { validateGraph } from "../components/workflow/validate";
import type { FlowNode } from "../components/workflow/nodes/shared";

function defaultConfig(type: WorkflowNodeType): NodeConfig {
  switch (type) {
    case "end":
      return { outcome: "completed" };
    case "human_task":
      return { assignee: { mode: "role", role: "member" }, form: [], handover: false };
    case "decision":
      return { branches: [] };
    case "approval":
      return { approvers: { mode: "role", role: "maintainer" }, gate: "none", reason_required_on_reject: false };
    case "auto_action":
      return { action: { action: "notify", params: {} } };
    case "agent_task":
      return { agent_role: "", phase: "execution" };
    default:
      return {};
  }
}

/** Label einer neuen Kante aus Quell-Knoten + sourceHandle ableiten. */
function connectionLabel(nodes: FlowNode[], c: Connection): string | undefined {
  const src = nodes.find((n) => n.id === c.source);
  if (!src) return undefined;
  const h = c.sourceHandle;
  if (src.type === "approval") return h === "rejected" ? "abgelehnt" : "genehmigt";
  if (src.type === "decision") {
    const b = (src.data.config.branches || []).find((x) => x.handle === h);
    return b?.label;
  }
  return undefined;
}

export default function WorkflowEditor() {
  const { key, id } = useParams();
  const wfId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = useMemo(() => projects?.find((p) => p.key === key), [projects, key]);

  const { data: def } = useQuery({ queryKey: ["workflow", wfId], queryFn: () => workflowApi.get(wfId) });
  const { data: version } = useQuery({
    queryKey: ["workflow-editable", wfId],
    queryFn: () => workflowApi.editable(wfId),
  });
  const { data: meta } = useQuery({
    queryKey: ["meta", project?.id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${project!.id}/meta`),
    enabled: !!project,
  });
  const members = meta?.members || [];

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const seeded = useRef(false);

  // Graph einmalig aus der Draft-Version übernehmen.
  useEffect(() => {
    if (version && !seeded.current) {
      const flow = graphToFlow(version.graph);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      seeded.current = true;
    }
  }, [version]);

  const onConnect = useCallback(
    (c: Connection) => {
      const label = connectionLabel(nodes, c);
      setEdges((eds) =>
        addEdge(
          {
            ...c,
            id: `e_${c.source}_${c.sourceHandle || "out"}_${c.target}_${Date.now()}`,
            type: "condition",
            label,
            markerEnd: { type: MarkerType.ArrowClosed },
          },
          eds
        )
      );
    },
    [nodes, setEdges]
  );

  const onDropNode = useCallback(
    (type: WorkflowNodeType, pos: { x: number; y: number }) => {
      const id2 = `${type}_${Date.now()}`;
      const node: FlowNode = { id: id2, type, position: pos, data: { config: defaultConfig(type) } };
      setNodes((ns) => ns.concat(node));
      setSelectedId(id2);
    },
    [setNodes]
  );

  const updateConfig = useCallback(
    (nodeId: string, config: NodeConfig) => {
      setNodes((ns) => ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, config } } : n)));
    },
    [setNodes]
  );

  const deleteNode = useCallback(
    (nodeId: string) => {
      setNodes((ns) => ns.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
      if (selectedId === nodeId) setSelectedId(null);
    },
    [selectedId, setNodes, setEdges]
  );

  const clientErrors = useMemo(() => validateGraph(flowToGraph(nodes, edges)), [nodes, edges]);
  const selected = nodes.find((n) => n.id === selectedId) || null;

  const save = async () => {
    if (!version) return;
    setSaving(true);
    setMsg("");
    try {
      await workflowApi.saveVersion(wfId, version.id, { graph: flowToGraph(nodes, edges) });
      setMsg("Gespeichert.");
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Speichern fehlgeschlagen: ${e.message}` : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  const validateServer = async () => {
    if (!version) return;
    setMsg("");
    try {
      await workflowApi.saveVersion(wfId, version.id, { graph: flowToGraph(nodes, edges) });
      const r = await workflowApi.validate(wfId, version.id);
      setErrors(r.errors || []);
      setMsg(r.ok ? "Validierung ok." : `${r.errors.length} Problem(e) gefunden.`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Validierung fehlgeschlagen");
    }
  };

  const publish = async () => {
    if (!version) return;
    setMsg("");
    try {
      await workflowApi.saveVersion(wfId, version.id, { graph: flowToGraph(nodes, edges) });
      await workflowApi.publish(wfId, version.id);
      setErrors([]);
      setMsg("Veröffentlicht.");
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      if (project) qc.invalidateQueries({ queryKey: ["workflows", project.id] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Veröffentlichen abgelehnt: ${e.message}` : "Veröffentlichen fehlgeschlagen");
    }
  };

  const allErrors = errors.length ? errors : clientErrors;

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-surface">
      {/* Kopfzeile */}
      <div className="flex items-center gap-3 border-b border-line bg-card px-4 py-2">
        <button
          onClick={() => nav(`/projects/${key}`)}
          className="rounded border border-line px-2 py-1 text-sm text-muted hover:text-ink"
        >
          ← Zurück zum Projekt
        </button>
        <span className="font-mono text-xs text-muted">{def?.key}</span>
        <h1 className="text-sm font-semibold">{def?.name || "Prozess"}</h1>
        <div className="flex-1" />
        {msg && <span className="text-xs text-muted">{msg}</span>}
        <button
          onClick={save}
          disabled={saving || !version}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          {saving ? "Speichert…" : "Speichern"}
        </button>
        <button
          onClick={validateServer}
          disabled={!version}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          Validieren
        </button>
        <button
          onClick={publish}
          disabled={!version || clientErrors.length > 0}
          title={clientErrors.length ? "Erst Fehler beheben" : "Veröffentlichen"}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50"
        >
          Veröffentlichen
        </button>
      </div>

      {/* Arbeitsfläche */}
      <div className="flex min-h-0 flex-1">
        <div className="w-52 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
          <NodePalette />
        </div>

        <div className="relative min-w-0 flex-1">
          <WorkflowCanvas
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={setSelectedId}
            onDropNode={onDropNode}
          />
        </div>

        <div className="flex w-80 shrink-0 flex-col overflow-y-auto border-l border-line bg-card">
          <div className="border-b border-line px-3 py-2 text-xs font-medium text-muted">Konfiguration</div>
          <NodeConfigPanel node={selected} members={members} onChange={updateConfig} onDelete={deleteNode} />

          {allErrors.length > 0 && (
            <div className="mt-auto border-t border-line p-3">
              <div className="mb-1 text-xs font-medium text-red-400">
                {allErrors.length} Validierungsfehler
              </div>
              <ul className="space-y-1">
                {allErrors.map((e, i) => (
                  <li key={i} className="text-xs text-red-400">
                    • {e}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
