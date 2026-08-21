import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tr } from "../i18n";
import { useNarrow } from "../lib/useSchmal";
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
import { availableFields } from "../components/workflow/contextFields";
import DryrunPanel from "../components/workflow/DryRunPanel";
import BuilderPanel from "../components/workflow/BuilderPanel";
import {
  graphToFlow, flowToGraph, graphSignature, contentSignature,
} from "../components/workflow/convert";
import { needsLayout, layoutGraph, DEFAULT_GAP } from "../components/workflow/layout";
import { validateGraph } from "../components/workflow/validate";
import type { FlowNode } from "../components/workflow/nodes/shared";
import { projectPath } from "../projectTabs";
import { RAIL_LEAVEBLANK } from "../nav";
import VersionsDiff from "../components/workflow/VersionsDiff";
import { ConfirmDialog, BUTTON, Button } from "../components/ui";

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
    case "loop":
      return { list: "", element: "element", index: "i" };
    case "timer":
      return { duration: 30, unit: "m" };
    default:
      return {};
  }
}

/** Derive the label of a new edge from the source node plus sourceHandle. */
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
  const place = useLocation();
  const { data: catalog } = useQuery({
    queryKey: ["workflow-context-fields"], queryFn: workflowApi.contextFields,
    staleTime: 60 * 60 * 1000,   // changes only with a deploy
  });
  const wfId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = useMemo(() => projects?.find((p) => p.key === key), [projects, key]);

  const { data: def } = useQuery({ queryKey: ["workflow", wfId], queryFn: () => workflowApi.get(wfId) });
  // Editable draft version. If the write permission is missing (for instance a flow from the
  // shipped set that only an admin may change), the view falls back to the published
  // version: looking is allowed for everyone, changing is not.
  const { data: version, error: versionError } = useQuery({
    queryKey: ["workflow-editable", wfId],
    queryFn: () => workflowApi.editable(wfId),
    retry: false,
  });
  const onlyRead = versionError instanceof ApiError && versionError.status === 403;
  // Always loaded, not only in read-only mode: it shows which version applies out there, and
  // whether the draft is ahead of it.
  const { data: published } = useQuery({
    queryKey: ["workflow-versions", wfId],
    queryFn: () => workflowApi.versions(wfId),
  });
  const view = version
    || (onlyRead ? published?.find((v) => v.id === def?.current_version_id) : undefined);
  const { data: meta } = useQuery({
    queryKey: ["meta", project?.id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${project!.id}/meta`),
    enabled: !!project,
  });
  const members = meta?.members || [];

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  /** When a node is deleted with a key, its edges have to go FROM THE FULL graph: in the
   *  filtered view React Flow does not know the hidden edges and would otherwise leave
   *  references pointing nowhere. */
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      const path = changes.filter((c) => c.type === "remove").map((c) => c.id);
      if (path.length) {
        setEdges((eds) => eds.filter((e) => !path.includes(e.source) && !path.includes(e.target)));
        setMsg(tr("editor.count_step_s_deleted", { count: path.length }));
      }
      onNodesChange(changes);
    },
    [onNodesChange, setEdges],
  );

  /** Deleted connections are a real change, and that has to be visible. */
  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      const path = changes.filter((c) => c.type === "remove").length;
      if (path) setMsg(tr("editor.count_connection_s_deleted", { count: path }));
      onEdgesChange(changes);
    },
    [onEdgesChange],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** "Main path" hides the disturbance branches, leaving the red thread. */
  const [onlyMainpath, setOnlyMainpath] = useState(false);
  /** Target for the view after arranging (start node, top centre). */
  const [focus, setFocus] = useState<{ x: number; y: number; token: number } | undefined>();
  const [errors, setErrors] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  // How one sees whether the check still holds: it belongs to ONE state of the graph. Whoever
  // moves something afterwards no longer has a checked result but an old one.
  const [checked, setChecked] = useState<{ signature: string; ok: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [questionDiscard, setQuestionDiscard] = useState(false);
  const seeded = useRef(false);
  // The last saved graph as text. On it hangs the only question one really has when leaving
  // the editor: is this saved yet? Before, there was a message that disappeared after the
  // next click, and after that nobody knew any more.
  const secured = useRef<string>("");
  const [state, setState] = useState(0);   // forces the header to recompute

  // Spacing for "arrange", set globally by the admin.
  const { data: layoutCfg } = useQuery({
    queryKey: ["workflow-layout"],
    queryFn: workflowApi.layout,
    staleTime: 5 * 60_000,
  });
  const gap = layoutCfg?.gap ?? DEFAULT_GAP;

  // Take the graph over from the draft version once.
  useEffect(() => {
    if (view && !seeded.current) {
      const graph = needsLayout(view.graph) ? layoutGraph(view.graph, { gap }) : view.graph;
      const flow = graphToFlow(graph);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      secured.current = graphSignature(flowToGraph(flow.nodes, flow.edges));
      seeded.current = true;
    }
  }, [view]);

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

  // On a phone the three columns do not fit beside each other: one is always shown, and the
  // switch hangs off this selection. On the desktop everything stays as it was.
  const narrow = useNarrow();
  const [column, setColumn] = useState<"flaeche" | "baustein">("flaeche");

  const onDropNode = useCallback(
    (type: WorkflowNodeType, pos: { x: number; y: number }, edgeId?: string) => {
      const id2 = `${type}_${Date.now()}`;
      const node: FlowNode = { id: id2, type, position: pos, data: { config: defaultConfig(type) } };
      setNodes((ns) => ns.concat(node));
      setSelectedId(id2);

      // A building block hanging in the air is a validation error and manual work on top:
      // first drag, then draw two connections. So connect where the intention is
      // unambiguous: dropped on a line means "in between", with a selected node it means
      // "behind it". Otherwise it stands free.
      setEdges((es) => {
        /** Push a building block into an existing connection: it now ends at the block, and
         *  from the block it continues. An end block closes the path. */
        const insert = (edge: typeof es[number]) => {
          const remainder = es.filter((e) => e.id !== edge.id);
          const into = { ...edge, id: `e-${edge.source}-${edge.sourceHandle || "out"}-${id2}`,
                           target: id2, targetHandle: undefined };
          const beyond = { id: `e-${id2}-out-${edge.target}`, source: id2, target: edge.target,
                           targetHandle: edge.targetHandle };
          return type === "end" ? [...remainder, into] : [...remainder, into, beyond];
        };

        if (edgeId) {
          const hits = es.find((e) => e.id === edgeId);
          return hits ? insert(hits) : es;
        }

        // No hit on a line: behind the selected node. If something already hangs there, it is
        // inserted instead of placed beside it: "behind" is the expectation, and a block in
        // the air is manual work plus a validation error.
        const source = nodes.find((n) => n.id === selectedId);
        if (!source || source.id === id2 || source.type === "end") return es;

        // Branch, approval and loop have named exits; there nothing is guessed, the first
        // still free one is taken.
        const named: Record<string, string[]> = {
          decision: (source.data.config.branches || []).map((b) => b.handle),
          approval: ["approved", "rejected"],
          loop: ["element", "fertig"],
        };
        const candidates = named[source.type || ""] || ["out"];
        const free = candidates.find(
          (h) => !es.some((e) => e.source === source.id && (e.sourceHandle || "out") === h));
        if (free) {
          return es.concat({ id: `e-${source.id}-${free}-${id2}`, source: source.id,
                             target: id2, sourceHandle: free === "out" ? undefined : free });
        }
        // Everything taken: insert into the first outgoing connection.
        const out = es.find((e) => e.source === source.id);
        return out ? insert(out) : es;
      });
    },
    [setNodes, setEdges, nodes, selectedId]
  );

  /** Building block by tap: it lands under the selected one (or under the last one), and the
   *  rest, connecting and selecting, is done by `onDropNode`. */
  const append = useCallback((type: WorkflowNodeType) => {
    const reference = nodes.find((n) => n.id === selectedId) || nodes[nodes.length - 1];
    const pos = reference
      ? { x: reference.position.x, y: reference.position.y + gap }
      : { x: 0, y: 0 };
    onDropNode(type, pos);
    if (narrow) setColumn("baustein");
  }, [nodes, selectedId, gap, onDropNode, narrow]);

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

  /** Rearrange all nodes from top to bottom (old LR graphs as well).
   *  Computes with the MEASURED card sizes so that the spacing comes out the same everywhere. */
  const autoLayout = useCallback(() => {
    const sizes = new Map(
      nodes
        .filter((n) => n.measured?.width && n.measured?.height)
        .map((n) => [n.id, { width: n.measured!.width!, height: n.measured!.height! }]),
    );
    const laid = layoutGraph(flowToGraph(nodes, edges), { gap, sizes });
    const pos = new Map(laid.nodes.map((n) => [n.id, n.position]));
    setNodes((ns) => ns.map((n) => (pos.has(n.id) ? { ...n, position: pos.get(n.id)! } : n)));
    // Set the view on the beginning: the new coordinates are already fixed here, so there is
    // no need to wait for the redraw.
    const start = nodes.find((n) => n.type === "start");
    const target = start && pos.get(start.id);
    if (target) {
      const width = sizes.get(start!.id)?.width ?? 220;
      setFocus({ x: target.x + width / 2, y: target.y, token: Date.now() });
    }
    setMsg(tr("editor.rearranged_not_saved_yet"));
  }, [nodes, edges, setNodes, gap]);

  const clientErrors = useMemo(() => validateGraph(flowToGraph(nodes, edges)), [nodes, edges]);

  // Filtered view: hidden nodes and their edges disappear only optically; what is saved is
  // ALWAYS the complete graph (`nodes`/`edges`).
  const hatGroups = useMemo(() => nodes.some((n) => n.data.config.group), [nodes]);
  const visible = useMemo(() => {
    if (!onlyMainpath) return { nodes, edges };
    const path = new Set(nodes.filter((n) => n.data.config.group === "stoerung").map((n) => n.id));
    return {
      nodes: nodes.filter((n) => !path.has(n.id)),
      edges: edges.filter((e) => !path.has(e.source) && !path.has(e.target)),
    };
  }, [onlyMainpath, nodes, edges]);
  const selected = nodes.find((n) => n.id === selectedId) || null;

  /**
   * Saving no longer means "write into version X".
   *
   * The server looks at what has changed: a pure arrangement lands in the version that applies
   * anyway (a published one included), everything else in a draft that only then exists.
   * Before, every look cost a version number, and a moved box turned "published" into
   * "deviates".
   */
  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const r = await workflowApi.saveGraph(wfId, { graph });
      secured.current = graphSignature(graph);
      setState((n) => n + 1);
      setMsg(r.hint || "Gespeichert.");
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Speichern fehlgeschlagen: ${e.message}` : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  /** Throw the draft away and back to what is running. */
  const discard = async () => {
    setMsg("");
    try {
      await workflowApi.discardDraft(wfId);
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
      seeded.current = false;      // the graph is reloaded from the live version
      setMsg("Entwurf verworfen.");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Verwerfen fehlgeschlagen");
    }
  };

  const validateServer = async () => {
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const stored = await workflowApi.saveGraph(wfId, { graph });
      secured.current = graphSignature(graph);   // Validieren speichert mit
      setState((n) => n + 1);
      const r = await workflowApi.validate(wfId, stored.version.id);
      setErrors(r.errors || []);
      setChecked({ signature: graphSignature(graph), ok: !!r.ok });
      setMsg(r.ok ? "Validierung ok." : `${r.errors.length} Problem(e) gefunden.`);
    } catch (e) {
      setChecked(null);
      setMsg(e instanceof ApiError ? e.message : "Validierung fehlgeschlagen");
    }
  };

  const publish = async () => {
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const stored = await workflowApi.saveGraph(wfId, { graph });
      // Nothing substantive changed: then there is nothing to publish either.
      if (stored.result === "layout" && stored.version.status === "published") {
        setMsg("Nichts zu veröffentlichen — nur die Anordnung war anders.");
        return;
      }
      await workflowApi.publish(wfId, stored.version.id);
      secured.current = graphSignature(graph);
      setState((n) => n + 1);
      setErrors([]);
      setMsg(tr("editor.published"));
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      if (project) qc.invalidateQueries({ queryKey: ["workflows", project.id] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Veröffentlichen abgelehnt: ${e.message}` : tr("editor.publishing_failed"));
    }
  };

  // Unsaved? The comparison runs against the last saved graph, and a moved card counts too,
  // because positions are saved along.
  const now = useMemo(() => graphSignature(flowToGraph(nodes, edges)), [nodes, edges]);
  const changed = !onlyRead && seeded.current && now !== secured.current;

  // And which version applies out there? Three situations that have to be told apart: never
  // published, published and identical, or published with the draft ahead. The third was
  // invisible until now: you rebuild, are happy, and out there the old version keeps
  // running.
  const liveVersion = published?.find((v) => v.id === def?.current_version_id);
  // What is compared is the CONTENT, not the version number: on opening, the editor creates a
  // fresh draft version that carries a new number but contains the same thing character for
  // character. Going by the number would mean marking every flow as "not published" on mere
  // viewing.
  // What is compared is the CONTENT: a different arrangement is no deviation, otherwise
  // "deviates from v7" would stand there after every tidy-up although the flow is the same.
  const contentNow = useMemo(
    () => contentSignature(flowToGraph(nodes, edges)), [nodes, edges]);
  const sameAsLive = !!liveVersion && contentNow === contentSignature(liveVersion.graph);
  const publication = !def?.current_version_id
    ? { text: tr("editor.never_published"), style: "text-muted",
        title: tr("editor.flow_runs_nowhere_yet") }
    : sameAsLive
      ? { text: `veröffentlicht (v${liveVersion?.version ?? "?"})`, style: "text-green-400",
          title: tr("editor.what_canvas_here_also") }
      : { text: `weicht von v${liveVersion?.version ?? "?"} ab`, style: "text-amber-300",
          title: tr("editor.outside_published_version_runs") };

  // Ask when leaving the window with unsaved work: the browser only allows its own text, but
  // the question itself is the point.
  useEffect(() => {
    if (!changed) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changed]);

  const allErrors = errors.length ? errors : clientErrors;
  // Which context fields this flow has follows from its trigger and its steps; the catalog
  // for that comes from the backend so that it does not drift apart.
  const contextFields = useMemo(
    () => availableFields(nodes, catalog), [nodes, catalog]);
  const origin = (place.state as { from?: string } | null)?.from
    || (key ? projectPath(key, "settings", "processes")
            : def?.slot ? "/processes/default" : "/processes/own");

  return (
    // As in the office: the area rail stays, the rest of the page disappears.
    <div className={`fixed inset-0 z-30 flex flex-col bg-surface ${RAIL_LEAVEBLANK}`}>
      {/* Kopfzeile */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-card px-3 py-2 sm:px-4">
        <button
          // Back to where one came from. The caller passes its own address as `state.from`;
          // only when that is missing (bookmark, reloaded page) is it derived from the flow
          // itself: a project flow to the project overview, a slot flow to the default set, a
          // free flow to one's own processes. Before, a slot flow landed in the settings,
          // where it does not stand at all.
          onClick={() => {
            if (changed && !confirm(tr("editor.unsaved_changes_go_back"))) return;
            nav(origin);
          }}
          className={BUTTON.secondary}
        >
          <span className="sm:hidden">←</span>
          <span className="hidden sm:inline">{tr("editor.back_flows")}</span>
        </button>
        <span className="hidden font-mono text-xs text-muted sm:inline">{def?.key}</span>
        <h1 className="text-sm font-semibold">{def?.name || "Prozess"}</h1>
        {onlyRead && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
            title={tr("workflow_editor.this_flow_belongs_to_a_process_set_to_change")}>
            {tr("editor.view_only")}
          </span>
        )}
        {!onlyRead && (
          <span className={`rounded px-1.5 py-0.5 text-xs ${
            changed ? "bg-amber-500/15 text-amber-300" : "text-muted"}`}
            title={changed
              ? tr("editor.canvas_differs_saved_version")
              : tr("editor.everything_saved")}>
            {tr(changed ? "editor.unsaved" : "editor.saved")}
          </span>
        )}
        {/* Die Abweichung ist anklickbar: „weicht von v7 ab" beantwortet nicht, WAS abweicht,
            and that is exactly what one looks for in that moment. */}
        {!sameAsLive && liveVersion && version?.id && version.id !== liveVersion.id ? (
          <button onClick={() => setShowDiff(true)}
            className={`rounded px-1.5 py-0.5 text-xs underline decoration-dotted ${publication.style}`}
            title={tr("editor.look_difference_running_version")}>
            {publication.text}
          </button>
        ) : (
          <span className={`rounded px-1.5 py-0.5 text-xs ${publication.style}`}
            title={publication.title}>
            {publication.text}
          </span>
        )}
        {/* Entwurf wegwerfen: es gab keinen Weg zurück, außer den Graphen von Hand
            back. Only visible when there really is an open draft. */}
        {!onlyRead && version?.status === "draft" && version.id > 0 && (
          <button onClick={() => setQuestionDiscard(true)}
            className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:border-red-400 hover:text-red-300"
            title={tr("editor.back_version_running")}>
            {tr("editor.discard_draft")}
          </button>
        )}
        <div className="flex-1" />
        {msg && <span className="text-xs text-muted">{msg}</span>}
        {narrow && (
          <div className="flex overflow-hidden rounded border border-line">
            {(["flaeche", "baustein"] as const).map((a) => (
              <button key={a} type="button" onClick={() => setColumn(a)}
                className={`px-2 py-1 text-xs ${column === a
                  ? "bg-brand text-white" : "text-muted hover:text-ink"}`}>
                {tr(a === "flaeche" ? "editor.canvas" : "editor.block")}
              </button>
            ))}
          </div>
        )}
        <Button onClick={autoLayout} disabled={nodes.length === 0 || onlyRead} symbol="⇅"
          title={tr("editor.arrange_blocks_top_bottom", { distance: gap })}>
          {tr("editor.arrange")}
        </Button>
        {/* Speichern kann nur, was sich geändert hat — sonst ist der Knopf ein Versprechen,
            that it does not redeem. */}
        <Button onClick={save} disabled={!changed || saving || !version || onlyRead}
          symbol="💾" title={changed ? undefined : tr("editor.nothing_changed")}>
          {tr(saving ? "editor.saving" : "common.save")}
        </Button>
        {/* Das Ergebnis bleibt am Knopf stehen: Wer geprüft hat, will es später noch sehen,
            without checking again. After the next change it is open again. */}
        <Button onClick={validateServer} disabled={!version || onlyRead} symbol="✓"
          state={checked && checked.signature === now
            ? (checked.ok ? "good" : "bad") : "open"}
          title={checked && checked.signature === now
            ? (checked.ok ? tr("editor.checked_no_problems") : tr("editor.checked_there_problems"))
            : tr("editor.version_not_been_checked")}>
          {tr("editor.check")}
        </Button>
        {/* Nichts Neues, nichts zu veröffentlichen. Vorher lud der Knopf dazu ein und
            answered afterwards "only the arrangement was different". */}
        <Button variant="primary" onClick={publish} symbol="⬆"
          disabled={!version || clientErrors.length > 0 || sameAsLive || onlyRead}
          title={clientErrors.length ? tr("editor.fix_errors_first")
            : sameAsLive ? tr("editor.published_version_already_one")
              : tr("editor.publish")}>
          {tr("editor.publish")}
        </Button>
      </div>

      {/* Arbeitsfläche */}
      <div className="flex min-h-0 flex-1">
        {!onlyRead && !narrow && (
          <div className="w-52 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
            <NodePalette onAdd={append} />
            <p className="mt-4 border-t border-line pt-3 text-[11px] leading-relaxed text-muted">
              {tr("editor.draw_connection_outlet_onto")}<br />
              {tr("editor.delete_connection_hover_line")}
            </p>
          </div>
        )}

        <div className={`relative min-w-0 flex-1 ${
          narrow && column !== "flaeche" ? "hidden" : ""}`}>
          <WorkflowCanvas
            nodes={visible.nodes}
            edges={visible.edges}
            readOnly={onlyRead}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            onNodeClick={(id) => { setSelectedId(id); if (narrow) setColumn("baustein"); }}
            onDropNode={onDropNode}
            focus={focus}
          />
        </div>

        <div className={`flex flex-col overflow-y-auto border-l border-line bg-card ${
          narrow ? `w-full ${column === "baustein" ? "" : "hidden"}` : "w-80 shrink-0"}`}>
          {/* Am Handy steht die Palette hier oben: ohne sie käme man in dieser Ansicht an
              no new building block, and the surface has no room for a bar. */}
          {narrow && !onlyRead && (
            <div className="border-b border-line p-2">
              <div className="mb-1.5 text-xs font-medium text-muted">{tr("node_palette.blocks")}</div>
              <NodePalette onAdd={append} compact />
            </div>
          )}
          <div className="border-b border-line px-3 py-2 text-xs font-medium text-muted">{tr("workflow_editor.configuration")}</div>
          {onlyRead ? (
            <p className="p-3 text-sm text-muted">
              Dieser Ablauf gehört zu einem Prozess-Satz und wird hier nur angezeigt. Zum Ändern
              im Projekt unter <b>{tr("workflow_editor.flows")}</b> auf <b>{tr("workflow_editor.customize")}</b> gehen — das legt eine Kopie
              für dieses Projekt an.
            </p>
          ) : (
            <NodeConfigPanel node={selected} members={members} onChange={updateConfig}
              onDelete={deleteNode} projectId={project?.id}
              subjectKind={def?.subject_kind} contextFields={contextFields}
              contextFilter={catalog?.filter} defId={def?.id} />
          )}

          {!onlyRead && <DryrunPanel defId={def?.id} nodes={nodes}
              graph={() => flowToGraph(nodes, edges)} />}

          {!onlyRead && <BuilderPanel defId={def?.id} nodeNumber={nodes.length}
              graph={() => flowToGraph(nodes, edges)}
              adopt={(g) => {
                // The draft brings no sizes with it: arrange first, then draw, otherwise
                // everything sticks on top of each other in the same grid.
                const flow = graphToFlow(needsLayout(g) ? layoutGraph(g, { gap }) : g);
                setNodes(flow.nodes);
                setEdges(flow.edges);
                setSelectedId(null);
                setMsg(tr("editor.draft_taken_not_saved"));
              }} />}

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
      {showDiff && liveVersion && version && (
        <VersionsDiff defId={wfId} versionId={version.id} against={liveVersion.id}
          title={tr("editor.difference_v_version_currently", { version: liveVersion.version })}
          onClose={() => setShowDiff(false)} />
      )}
      {questionDiscard && (
        <ConfirmDialog
          title={tr("editor.discard_draft")}
          text={tr("editor.throw_away_open_draft")}
          hint={tr("editor.editor_shows_published_version")}
          confirmText={tr("editor.discard_draft")}
          onClose={() => setQuestionDiscard(false)}
          onConfirm={() => { setQuestionDiscard(false); void discard(); }} />
      )}
    </div>
  );
}
