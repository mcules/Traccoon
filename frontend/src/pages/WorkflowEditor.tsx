import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
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
import { verfuegbareFelder } from "../components/workflow/contextFields";
import { graphToFlow, flowToGraph } from "../components/workflow/convert";
import { needsLayout, layoutGraph, DEFAULT_GAP } from "../components/workflow/layout";
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
      return { agent_role: "exec_agent", phase: "execution" };
    case "wait_event":
      return { events: ["comment", "manual"] };
    case "subflow":
      return { inherit_context: true };
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
  const ort = useLocation();
  const { data: katalog } = useQuery({
    queryKey: ["workflow-context-fields"], queryFn: workflowApi.contextFields,
    staleTime: 60 * 60 * 1000,   // ändert sich nur mit einem Deploy
  });
  const wfId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = useMemo(() => projects?.find((p) => p.key === key), [projects, key]);

  const { data: def } = useQuery({ queryKey: ["workflow", wfId], queryFn: () => workflowApi.get(wfId) });
  // Bearbeitbare Draft-Version. Fehlt das Schreibrecht (z. B. ein Ablauf aus dem
  // ausgelieferten Satz, den nur ein Admin ändern darf), fällt die Ansicht auf die
  // veröffentlichte Version zurück — schauen darf jeder, ändern nicht.
  const { data: version, error: versionError } = useQuery({
    queryKey: ["workflow-editable", wfId],
    queryFn: () => workflowApi.editable(wfId),
    retry: false,
  });
  const nurLesen = versionError instanceof ApiError && versionError.status === 403;
  const { data: veroeffentlicht } = useQuery({
    queryKey: ["workflow-versions", wfId],
    queryFn: () => workflowApi.versions(wfId),
    enabled: nurLesen,
  });
  const ansicht = version
    || (nurLesen ? veroeffentlicht?.find((v) => v.id === def?.current_version_id) : undefined);
  const { data: meta } = useQuery({
    queryKey: ["meta", project?.id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${project!.id}/meta`),
    enabled: !!project,
  });
  const members = meta?.members || [];

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  /** Wird ein Knoten per Taste gelöscht, müssen seine Kanten AUS DEM VOLLEN Graphen weg —
   *  in der gefilterten Sicht kennt React Flow die ausgeblendeten Kanten nicht und ließe
   *  sonst Verweise ins Leere zurück. */
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      const weg = changes.filter((c) => c.type === "remove").map((c) => c.id);
      if (weg.length) {
        setEdges((eds) => eds.filter((e) => !weg.includes(e.source) && !weg.includes(e.target)));
        setMsg(`${weg.length === 1 ? "Schritt" : `${weg.length} Schritte`} gelöscht — noch nicht gespeichert.`);
      }
      onNodesChange(changes);
    },
    [onNodesChange, setEdges],
  );

  /** Gelöschte Verbindungen sind eine echte Änderung — das muss man auch sehen. */
  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      const weg = changes.filter((c) => c.type === "remove").length;
      if (weg) setMsg(`${weg === 1 ? "Verbindung" : `${weg} Verbindungen`} gelöscht — noch nicht gespeichert.`);
      onEdgesChange(changes);
    },
    [onEdgesChange],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** „Hauptweg" blendet die Störungs-Zweige aus — der rote Faden bleibt übrig. */
  const [nurHauptweg, setNurHauptweg] = useState(false);
  /** Ziel für den Blick nach dem Anordnen (Start-Knoten, oben mittig). */
  const [fokus, setFokus] = useState<{ x: number; y: number; token: number } | undefined>();
  const [errors, setErrors] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const seeded = useRef(false);

  // Abstand für „Anordnen" — global vom Admin gesetzt.
  const { data: layoutCfg } = useQuery({
    queryKey: ["workflow-layout"],
    queryFn: workflowApi.layout,
    staleTime: 5 * 60_000,
  });
  const gap = layoutCfg?.gap ?? DEFAULT_GAP;

  // Graph einmalig aus der Draft-Version übernehmen.
  useEffect(() => {
    if (ansicht && !seeded.current) {
      const graph = needsLayout(ansicht.graph) ? layoutGraph(ansicht.graph, { gap }) : ansicht.graph;
      const flow = graphToFlow(graph);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      seeded.current = true;
    }
  }, [ansicht]);

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

  /** Alle Knoten neu von oben nach unten anordnen (auch alte LR-Graphen).
   *  Rechnet mit den GEMESSENEN Kartengrößen, damit der Abstand überall gleich ausfällt. */
  const autoLayout = useCallback(() => {
    const sizes = new Map(
      nodes
        .filter((n) => n.measured?.width && n.measured?.height)
        .map((n) => [n.id, { width: n.measured!.width!, height: n.measured!.height! }]),
    );
    const laid = layoutGraph(flowToGraph(nodes, edges), { gap, sizes });
    const pos = new Map(laid.nodes.map((n) => [n.id, n.position]));
    setNodes((ns) => ns.map((n) => (pos.has(n.id) ? { ...n, position: pos.get(n.id)! } : n)));
    // Blick auf den Anfang setzen: die neuen Koordinaten stehen hier schon fest, deshalb
    // brauchen wir nicht auf das Neuzeichnen zu warten.
    const start = nodes.find((n) => n.type === "start");
    const ziel = start && pos.get(start.id);
    if (ziel) {
      const breite = sizes.get(start!.id)?.width ?? 220;
      setFokus({ x: ziel.x + breite / 2, y: ziel.y, token: Date.now() });
    }
    setMsg("Neu angeordnet — noch nicht gespeichert.");
  }, [nodes, edges, setNodes, gap]);

  const clientErrors = useMemo(() => validateGraph(flowToGraph(nodes, edges)), [nodes, edges]);

  // Gefilterte Sicht: ausgeblendete Knoten samt ihrer Kanten verschwinden nur optisch —
  // gespeichert wird IMMER der vollständige Graph (`nodes`/`edges`).
  const hatGruppen = useMemo(() => nodes.some((n) => n.data.config.group), [nodes]);
  const sichtbar = useMemo(() => {
    if (!nurHauptweg) return { nodes, edges };
    const weg = new Set(nodes.filter((n) => n.data.config.group === "stoerung").map((n) => n.id));
    return {
      nodes: nodes.filter((n) => !weg.has(n.id)),
      edges: edges.filter((e) => !weg.has(e.source) && !weg.has(e.target)),
    };
  }, [nurHauptweg, nodes, edges]);
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
  // Welche Kontextfelder dieser Ablauf hat, ergibt sich aus seinem Auslöser und seinen
  // Schritten — der Katalog dazu kommt aus dem Backend, damit er nicht auseinanderläuft.
  const kontextFelder = useMemo(
    () => verfuegbareFelder(nodes, katalog), [nodes, katalog]);
  const herkunft = (ort.state as { from?: string } | null)?.from
    || (key ? `/projects/${key}?tab=workflows`
            : def?.slot ? "/processes/standard" : "/processes/eigene");

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-surface">
      {/* Kopfzeile */}
      <div className="flex items-center gap-3 border-b border-line bg-card px-4 py-2">
        <button
          // Zurück dorthin, wo man hergekommen ist. Der Aufrufer gibt seine eigene Adresse
          // als `state.from` mit; nur wenn sie fehlt (Lesezeichen, neu geladene Seite), wird
          // sie aus dem Ablauf selbst erschlossen — Projekt-Ablauf zur Projektübersicht,
          // Slot-Ablauf zum Standard-Satz, freier Ablauf zu den eigenen Prozessen. Vorher
          // landete ein Slot-Ablauf in den Einstellungen, wo er gar nicht steht.
          onClick={() => nav(herkunft)}
          className="rounded border border-line px-2 py-1 text-sm text-muted hover:text-ink"
        >
          ← Zurück zu den Prozessen
        </button>
        <span className="font-mono text-xs text-muted">{def?.key}</span>
        <h1 className="text-sm font-semibold">{def?.name || "Prozess"}</h1>
        {nurLesen && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
            title="Dieser Ablauf gehört zu einem Prozess-Satz. Zum Ändern im Projekt „Anpassen“ wählen.">
            nur ansehen
          </span>
        )}
        <div className="flex-1" />
        {msg && <span className="text-xs text-muted">{msg}</span>}
        <button
          onClick={autoLayout}
          disabled={nodes.length === 0}
          hidden={nurLesen}
          title={`Knoten von oben nach unten anordnen (Abstand ${gap} px)`}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          Anordnen
        </button>
        <button
          onClick={save}
          disabled={saving || !version}
          hidden={nurLesen}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          {saving ? "Speichert…" : "Speichern"}
        </button>
        <button
          onClick={validateServer}
          disabled={!version}
          hidden={nurLesen}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          Validieren
        </button>
        <button
          onClick={publish}
          disabled={!version || clientErrors.length > 0}
          hidden={nurLesen}
          title={clientErrors.length ? "Erst Fehler beheben" : "Veröffentlichen"}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50"
        >
          Veröffentlichen
        </button>
      </div>

      {/* Arbeitsfläche */}
      <div className="flex min-h-0 flex-1">
        {!nurLesen && (
          <div className="w-52 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
            <NodePalette />
            <p className="mt-4 border-t border-line pt-3 text-[10px] leading-relaxed text-muted">
              Verbindung ziehen: von einem Ausgang auf den Eingang des nächsten Knotens.<br />
              Verbindung löschen: Linie überfahren und auf ✕ klicken — oder anklicken und Entf.
            </p>
          </div>
        )}

        <div className="relative min-w-0 flex-1">
          <WorkflowCanvas
            nodes={sichtbar.nodes}
            edges={sichtbar.edges}
            readOnly={nurLesen}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            onNodeClick={setSelectedId}
            onDropNode={onDropNode}
            fokus={fokus}
          />
        </div>

        <div className="flex w-80 shrink-0 flex-col overflow-y-auto border-l border-line bg-card">
          <div className="border-b border-line px-3 py-2 text-xs font-medium text-muted">Konfiguration</div>
          {nurLesen ? (
            <p className="p-3 text-sm text-muted">
              Dieser Ablauf gehört zu einem Prozess-Satz und wird hier nur angezeigt. Zum Ändern
              im Projekt unter <b>Prozesse</b> auf <b>Anpassen</b> gehen — das legt eine Kopie
              für dieses Projekt an.
            </p>
          ) : (
            <NodeConfigPanel node={selected} members={members} onChange={updateConfig}
              onDelete={deleteNode} projectId={project?.id}
              subjectKind={def?.subject_kind} kontextFelder={kontextFelder} />
          )}

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
