import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tr } from "../i18n";
import { useSchmal } from "../lib/useSchmal";
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
import ProbelaufPanel from "../components/workflow/ProbelaufPanel";
import BaumeisterPanel from "../components/workflow/BaumeisterPanel";
import {
  graphToFlow, flowToGraph, graphSignatur, inhaltsSignatur,
} from "../components/workflow/convert";
import { needsLayout, layoutGraph, DEFAULT_GAP } from "../components/workflow/layout";
import { validateGraph } from "../components/workflow/validate";
import type { FlowNode } from "../components/workflow/nodes/shared";
import { projektPfad } from "../projectTabs";
import { SCHIENE_FREILASSEN } from "../nav";
import VersionsDiff from "../components/workflow/VersionsDiff";
import { BestaetigenDialog } from "../components/ui";

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
      return { liste: "", element: "element", index: "i" };
    case "timer":
      return { dauer: 30, einheit: "m" };
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
  // Editable draft version. If the write permission is missing (for instance a flow from the
  // shipped set that only an admin may change), the view falls back to the published
  // version: looking is allowed for everyone, changing is not.
  const { data: version, error: versionError } = useQuery({
    queryKey: ["workflow-editable", wfId],
    queryFn: () => workflowApi.editable(wfId),
    retry: false,
  });
  const nurLesen = versionError instanceof ApiError && versionError.status === 403;
  // Always loaded, not only in read-only mode: it shows which version applies out there, and
  // whether the draft is ahead of it.
  const { data: veroeffentlicht } = useQuery({
    queryKey: ["workflow-versions", wfId],
    queryFn: () => workflowApi.versions(wfId),
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
  /** When a node is deleted with a key, its edges have to go FROM THE FULL graph: in the
   *  filtered view React Flow does not know the hidden edges and would otherwise leave
   *  references pointing nowhere. */
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      const weg = changes.filter((c) => c.type === "remove").map((c) => c.id);
      if (weg.length) {
        setEdges((eds) => eds.filter((e) => !weg.includes(e.source) && !weg.includes(e.target)));
        setMsg(tr("editor.schritte_geloescht", { anzahl: weg.length }));
      }
      onNodesChange(changes);
    },
    [onNodesChange, setEdges],
  );

  /** Deleted connections are a real change, and that has to be visible. */
  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      const weg = changes.filter((c) => c.type === "remove").length;
      if (weg) setMsg(tr("editor.verbindungen_geloescht", { anzahl: weg }));
      onEdgesChange(changes);
    },
    [onEdgesChange],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** "Main path" hides the disturbance branches, leaving the red thread. */
  const [nurHauptweg, setNurHauptweg] = useState(false);
  /** Target for the view after arranging (start node, top centre). */
  const [fokus, setFokus] = useState<{ x: number; y: number; token: number } | undefined>();
  const [errors, setErrors] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [zeigeDiff, setZeigeDiff] = useState(false);
  const [frageVerwerfen, setFrageVerwerfen] = useState(false);
  const seeded = useRef(false);
  // The last saved graph as text. On it hangs the only question one really has when leaving
  // the editor: is this saved yet? Before, there was a message that disappeared after the
  // next click, and after that nobody knew any more.
  const gesichert = useRef<string>("");
  const [stand, setStand] = useState(0);   // forces the header to recompute

  // Spacing for "arrange", set globally by the admin.
  const { data: layoutCfg } = useQuery({
    queryKey: ["workflow-layout"],
    queryFn: workflowApi.layout,
    staleTime: 5 * 60_000,
  });
  const gap = layoutCfg?.gap ?? DEFAULT_GAP;

  // Take the graph over from the draft version once.
  useEffect(() => {
    if (ansicht && !seeded.current) {
      const graph = needsLayout(ansicht.graph) ? layoutGraph(ansicht.graph, { gap }) : ansicht.graph;
      const flow = graphToFlow(graph);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      gesichert.current = graphSignatur(flowToGraph(flow.nodes, flow.edges));
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

  // On a phone the three columns do not fit beside each other: one is always shown, and the
  // switch hangs off this selection. On the desktop everything stays as it was.
  const schmal = useSchmal();
  const [spalte, setSpalte] = useState<"flaeche" | "baustein">("flaeche");

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
        const einschieben = (kante: typeof es[number]) => {
          const rest = es.filter((e) => e.id !== kante.id);
          const hinein = { ...kante, id: `e-${kante.source}-${kante.sourceHandle || "out"}-${id2}`,
                           target: id2, targetHandle: undefined };
          const hinaus = { id: `e-${id2}-out-${kante.target}`, source: id2, target: kante.target,
                           targetHandle: kante.targetHandle };
          return type === "end" ? [...rest, hinein] : [...rest, hinein, hinaus];
        };

        if (edgeId) {
          const treffer = es.find((e) => e.id === edgeId);
          return treffer ? einschieben(treffer) : es;
        }

        // No hit on a line: behind the selected node. If something already hangs there, it is
        // inserted instead of placed beside it: "behind" is the expectation, and a block in
        // the air is manual work plus a validation error.
        const quelle = nodes.find((n) => n.id === selectedId);
        if (!quelle || quelle.id === id2 || quelle.type === "end") return es;

        // Branch, approval and loop have named exits; there nothing is guessed, the first
        // still free one is taken.
        const benannt: Record<string, string[]> = {
          decision: (quelle.data.config.branches || []).map((b) => b.handle),
          approval: ["approved", "rejected"],
          loop: ["element", "fertig"],
        };
        const kandidaten = benannt[quelle.type || ""] || ["out"];
        const frei = kandidaten.find(
          (h) => !es.some((e) => e.source === quelle.id && (e.sourceHandle || "out") === h));
        if (frei) {
          return es.concat({ id: `e-${quelle.id}-${frei}-${id2}`, source: quelle.id,
                             target: id2, sourceHandle: frei === "out" ? undefined : frei });
        }
        // Everything taken: insert into the first outgoing connection.
        const raus = es.find((e) => e.source === quelle.id);
        return raus ? einschieben(raus) : es;
      });
    },
    [setNodes, setEdges, nodes, selectedId]
  );

  /** Building block by tap: it lands under the selected one (or under the last one), and the
   *  rest, connecting and selecting, is done by `onDropNode`. */
  const anhaengen = useCallback((type: WorkflowNodeType) => {
    const bezug = nodes.find((n) => n.id === selectedId) || nodes[nodes.length - 1];
    const pos = bezug
      ? { x: bezug.position.x, y: bezug.position.y + gap }
      : { x: 0, y: 0 };
    onDropNode(type, pos);
    if (schmal) setSpalte("baustein");
  }, [nodes, selectedId, gap, onDropNode, schmal]);

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
    const ziel = start && pos.get(start.id);
    if (ziel) {
      const breite = sizes.get(start!.id)?.width ?? 220;
      setFokus({ x: ziel.x + breite / 2, y: ziel.y, token: Date.now() });
    }
    setMsg(tr("editor.neu_angeordnet"));
  }, [nodes, edges, setNodes, gap]);

  const clientErrors = useMemo(() => validateGraph(flowToGraph(nodes, edges)), [nodes, edges]);

  // Filtered view: hidden nodes and their edges disappear only optically; what is saved is
  // ALWAYS the complete graph (`nodes`/`edges`).
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

  /**
   * Speichern heißt nicht mehr „schreib in Fassung X".
   *
   * Der Server sieht sich an, was sich geändert hat: eine reine Anordnung landet in der
   * Fassung, die ohnehin gilt (auch in einer veröffentlichten), alles andere in einem
   * Entwurf, den es erst dann gibt. Vorher kostete jedes Hinsehen eine Fassungsnummer, und
   * ein verschobener Kasten machte aus „veröffentlicht" ein „weicht ab".
   */
  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const r = await workflowApi.saveGraph(wfId, { graph });
      gesichert.current = graphSignatur(graph);
      setStand((n) => n + 1);
      setMsg(r.hinweis || "Gespeichert.");
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Speichern fehlgeschlagen: ${e.message}` : "Speichern fehlgeschlagen");
    } finally {
      setSaving(false);
    }
  };

  /** Entwurf wegwerfen und zurück auf das, was läuft. */
  const verwerfen = async () => {
    setMsg("");
    try {
      await workflowApi.discardDraft(wfId);
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
      seeded.current = false;      // der Graph wird aus der Live-Fassung neu geladen
      setMsg("Entwurf verworfen.");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Verwerfen fehlgeschlagen");
    }
  };

  const validateServer = async () => {
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const gespeichert = await workflowApi.saveGraph(wfId, { graph });
      gesichert.current = graphSignatur(graph);   // Validieren speichert mit
      setStand((n) => n + 1);
      const r = await workflowApi.validate(wfId, gespeichert.version.id);
      setErrors(r.errors || []);
      setMsg(r.ok ? "Validierung ok." : `${r.errors.length} Problem(e) gefunden.`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Validierung fehlgeschlagen");
    }
  };

  const publish = async () => {
    setMsg("");
    try {
      const graph = flowToGraph(nodes, edges);
      const gespeichert = await workflowApi.saveGraph(wfId, { graph });
      // Nichts Inhaltliches geändert: dann gibt es auch nichts zu veröffentlichen.
      if (gespeichert.ergebnis === "layout" && gespeichert.version.status === "published") {
        setMsg("Nichts zu veröffentlichen — nur die Anordnung war anders.");
        return;
      }
      await workflowApi.publish(wfId, gespeichert.version.id);
      gesichert.current = graphSignatur(graph);
      setStand((n) => n + 1);
      setErrors([]);
      setMsg(tr("editor.veroeffentlicht_meldung"));
      qc.invalidateQueries({ queryKey: ["workflow-versions", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      qc.invalidateQueries({ queryKey: ["workflow-editable", wfId] });
      if (project) qc.invalidateQueries({ queryKey: ["workflows", project.id] });
    } catch (e) {
      setMsg(e instanceof ApiError ? `Veröffentlichen abgelehnt: ${e.message}` : tr("editor.veroeffentlichen_fehlgeschlagen"));
    }
  };

  // Unsaved? The comparison runs against the last saved graph, and a moved card counts too,
  // because positions are saved along.
  const jetzt = useMemo(() => graphSignatur(flowToGraph(nodes, edges)), [nodes, edges]);
  const geaendert = !nurLesen && seeded.current && jetzt !== gesichert.current;

  // And which version applies out there? Three situations that have to be told apart: never
  // published, published and identical, or published with the draft ahead. The third was
  // invisible until now: you rebuild, are happy, and out there the old version keeps
  // running.
  const liveVersion = veroeffentlicht?.find((v) => v.id === def?.current_version_id);
  // What is compared is the CONTENT, not the version number: on opening, the editor creates a
  // fresh draft version that carries a new number but contains the same thing character for
  // character. Going by the number would mean marking every flow as "not published" on mere
  // viewing.
  // Verglichen wird der INHALT: eine andere Anordnung ist keine Abweichung, sonst stünde
  // nach jedem Aufräumen „weicht von v7 ab" da, obwohl der Ablauf derselbe ist.
  const inhaltJetzt = useMemo(
    () => inhaltsSignatur(flowToGraph(nodes, edges)), [nodes, edges]);
  const gleichWieLive = !!liveVersion && inhaltJetzt === inhaltsSignatur(liveVersion.graph);
  const veroeffentlichung = !def?.current_version_id
    ? { text: tr("editor.nie_veroeffentlicht"), stil: "text-muted",
        titel: tr("editor.nie_veroeffentlicht_titel") }
    : gleichWieLive
      ? { text: `veröffentlicht (v${liveVersion?.version ?? "?"})`, stil: "text-green-400",
          titel: tr("editor.live_titel") }
      : { text: `weicht von v${liveVersion?.version ?? "?"} ab`, stil: "text-amber-300",
          titel: tr("editor.abweichung_titel") };

  // Ask when leaving the window with unsaved work: the browser only allows its own text, but
  // the question itself is the point.
  useEffect(() => {
    if (!geaendert) return;
    const warnen = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warnen);
    return () => window.removeEventListener("beforeunload", warnen);
  }, [geaendert]);

  const allErrors = errors.length ? errors : clientErrors;
  // Which context fields this flow has follows from its trigger and its steps; the catalog
  // for that comes from the backend so that it does not drift apart.
  const kontextFelder = useMemo(
    () => verfuegbareFelder(nodes, katalog), [nodes, katalog]);
  const herkunft = (ort.state as { from?: string } | null)?.from
    || (key ? projektPfad(key, "einstellungen", "prozesse")
            : def?.slot ? "/processes/standard" : "/processes/eigene");

  return (
    // Wie im Büro: die Bereichsschiene bleibt stehen, der Rest der Seite verschwindet.
    <div className={`fixed inset-0 z-30 flex flex-col bg-surface ${SCHIENE_FREILASSEN}`}>
      {/* Kopfzeile */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-card px-3 py-2 sm:px-4">
        <button
          // Back to where one came from. The caller passes its own address as `state.from`;
          // only when that is missing (bookmark, reloaded page) is it derived from the flow
          // itself: a project flow to the project overview, a slot flow to the default set, a
          // free flow to one's own processes. Before, a slot flow landed in the settings,
          // where it does not stand at all.
          onClick={() => {
            if (geaendert && !confirm(tr("editor.zurueck_trotz_aenderungen"))) return;
            nav(herkunft);
          }}
          className="rounded border border-line px-2 py-1 text-sm text-muted hover:text-ink"
        >
          <span className="sm:hidden">←</span>
          <span className="hidden sm:inline">{tr("editor.zurueck")}</span>
        </button>
        <span className="hidden font-mono text-xs text-muted sm:inline">{def?.key}</span>
        <h1 className="text-sm font-semibold">{def?.name || "Prozess"}</h1>
        {nurLesen && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
            title={tr("workflow_editor.dieser_ablauf_gehoert_zu_einem_prozess_s")}>
            {tr("editor.nur_ansehen")}
          </span>
        )}
        {!nurLesen && (
          <span className={`rounded px-1.5 py-0.5 text-xs ${
            geaendert ? "bg-amber-500/15 text-amber-300" : "text-muted"}`}
            title={geaendert
              ? tr("editor.ungespeichert_titel")
              : tr("editor.alles_gesichert")}>
            {tr(geaendert ? "editor.ungespeichert" : "editor.gespeichert")}
          </span>
        )}
        {/* Die Abweichung ist anklickbar: „weicht von v7 ab" beantwortet nicht, WAS abweicht,
            und genau danach sucht man in dem Moment. */}
        {!gleichWieLive && liveVersion && version?.id && version.id !== liveVersion.id ? (
          <button onClick={() => setZeigeDiff(true)}
            className={`rounded px-1.5 py-0.5 text-xs underline decoration-dotted ${veroeffentlichung.stil}`}
            title={tr("editor.unterschied_ansehen")}>
            {veroeffentlichung.text}
          </button>
        ) : (
          <span className={`rounded px-1.5 py-0.5 text-xs ${veroeffentlichung.stil}`}
            title={veroeffentlichung.titel}>
            {veroeffentlichung.text}
          </span>
        )}
        {/* Entwurf wegwerfen: es gab keinen Weg zurück, außer den Graphen von Hand
            zurückzubauen. Nur sichtbar, wenn es wirklich einen offenen Entwurf gibt. */}
        {!nurLesen && version?.status === "draft" && version.id > 0 && (
          <button onClick={() => setFrageVerwerfen(true)}
            className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:border-red-400 hover:text-red-300"
            title={tr("editor.entwurf_verwerfen_titel")}>
            {tr("editor.entwurf_verwerfen")}
          </button>
        )}
        <div className="flex-1" />
        {msg && <span className="text-xs text-muted">{msg}</span>}
        {schmal && (
          <div className="flex overflow-hidden rounded border border-line">
            {(["flaeche", "baustein"] as const).map((a) => (
              <button key={a} type="button" onClick={() => setSpalte(a)}
                className={`px-2 py-1 text-xs ${spalte === a
                  ? "bg-brand text-white" : "text-muted hover:text-ink"}`}>
                {tr(a === "flaeche" ? "editor.ansicht_flaeche" : "editor.ansicht_baustein")}
              </button>
            ))}
          </div>
        )}
        <button
          onClick={autoLayout}
          disabled={nodes.length === 0}
          hidden={nurLesen}
          title={tr("editor.anordnen_titel", { abstand: gap })}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          <span className="sm:hidden">⇅</span>
          <span className="hidden sm:inline">{tr("editor.anordnen")}</span>
        </button>
        <button
          onClick={save}
          disabled={saving || !version}
          hidden={nurLesen}
          className={`rounded border px-3 py-1 text-sm disabled:opacity-50 ${
            geaendert ? "border-amber-400 text-amber-200 hover:border-amber-300"
                      : "border-line text-ink hover:border-brand"}`}
        >
          <span className="sm:hidden">💾</span>
          <span className="hidden sm:inline">
            {tr(saving ? "editor.speichert" : "common.speichern")}
          </span>
        </button>
        <button
          onClick={validateServer}
          disabled={!version}
          hidden={nurLesen}
          className="rounded border border-line px-3 py-1 text-sm text-ink hover:border-brand disabled:opacity-50"
        >
          <span className="sm:hidden">✓</span>
          <span className="hidden sm:inline">{tr("editor.validieren")}</span>
        </button>
        <button
          onClick={publish}
          disabled={!version || clientErrors.length > 0}
          hidden={nurLesen}
          title={clientErrors.length ? tr("editor.erst_fehler_beheben") : tr("editor.veroeffentlichen")}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50"
        >
          <span className="sm:hidden">⬆</span>
          <span className="hidden sm:inline">{tr("editor.veroeffentlichen")}</span>
        </button>
      </div>

      {/* Arbeitsfläche */}
      <div className="flex min-h-0 flex-1">
        {!nurLesen && !schmal && (
          <div className="w-52 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
            <NodePalette onAdd={anhaengen} />
            <p className="mt-4 border-t border-line pt-3 text-[11px] leading-relaxed text-muted">
              {tr("editor.hilfe_verbinden")}<br />
              {tr("editor.hilfe_loeschen")}
            </p>
          </div>
        )}

        <div className={`relative min-w-0 flex-1 ${
          schmal && spalte !== "flaeche" ? "hidden" : ""}`}>
          <WorkflowCanvas
            nodes={sichtbar.nodes}
            edges={sichtbar.edges}
            readOnly={nurLesen}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            onNodeClick={(id) => { setSelectedId(id); if (schmal) setSpalte("baustein"); }}
            onDropNode={onDropNode}
            fokus={fokus}
          />
        </div>

        <div className={`flex flex-col overflow-y-auto border-l border-line bg-card ${
          schmal ? `w-full ${spalte === "baustein" ? "" : "hidden"}` : "w-80 shrink-0"}`}>
          {/* Am Handy steht die Palette hier oben: ohne sie käme man in dieser Ansicht an
              keinen neuen Baustein, und die Fläche hat für eine Leiste keinen Platz. */}
          {schmal && !nurLesen && (
            <div className="border-b border-line p-2">
              <div className="mb-1.5 text-xs font-medium text-muted">{tr("node_palette.bausteine")}</div>
              <NodePalette onAdd={anhaengen} kompakt />
            </div>
          )}
          <div className="border-b border-line px-3 py-2 text-xs font-medium text-muted">{tr("workflow_editor.konfiguration")}</div>
          {nurLesen ? (
            <p className="p-3 text-sm text-muted">
              Dieser Ablauf gehört zu einem Prozess-Satz und wird hier nur angezeigt. Zum Ändern
              im Projekt unter <b>{tr("workflow_editor.prozesse")}</b> auf <b>{tr("workflow_editor.anpassen")}</b> gehen — das legt eine Kopie
              für dieses Projekt an.
            </p>
          ) : (
            <NodeConfigPanel node={selected} members={members} onChange={updateConfig}
              onDelete={deleteNode} projectId={project?.id}
              subjectKind={def?.subject_kind} kontextFelder={kontextFelder}
              kontextFilter={katalog?.filter} defId={def?.id} />
          )}

          {!nurLesen && <ProbelaufPanel defId={def?.id} nodes={nodes}
              graph={() => flowToGraph(nodes, edges)} />}

          {!nurLesen && <BaumeisterPanel defId={def?.id} knotenZahl={nodes.length}
              graph={() => flowToGraph(nodes, edges)}
              uebernehmen={(g) => {
                // The draft brings no sizes with it: arrange first, then draw, otherwise
                // everything sticks on top of each other in the same grid.
                const flow = graphToFlow(needsLayout(g) ? layoutGraph(g, { gap }) : g);
                setNodes(flow.nodes);
                setEdges(flow.edges);
                setSelectedId(null);
                setMsg(tr("editor.entwurf_uebernommen"));
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
      {zeigeDiff && liveVersion && version && (
        <VersionsDiff defId={wfId} versionId={version.id} gegen={liveVersion.id}
          titel={tr("editor.unterschied_titel", { version: liveVersion.version })}
          onClose={() => setZeigeDiff(false)} />
      )}
      {frageVerwerfen && (
        <BestaetigenDialog
          titel={tr("editor.entwurf_verwerfen")}
          text={tr("editor.entwurf_verwerfen_frage")}
          hinweis={tr("editor.entwurf_verwerfen_hinweis")}
          bestaetigenText={tr("editor.entwurf_verwerfen")}
          onClose={() => setFrageVerwerfen(false)}
          onBestaetigen={() => { setFrageVerwerfen(false); void verwerfen(); }} />
      )}
    </div>
  );
}
