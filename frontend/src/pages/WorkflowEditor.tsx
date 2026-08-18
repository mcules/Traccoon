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
import { graphToFlow, flowToGraph, graphSignatur } from "../components/workflow/convert";
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
    case "loop":
      return { liste: "", element: "element", index: "i" };
    case "timer":
      return { dauer: 30, einheit: "m" };
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
  // Immer laden, nicht nur beim Nur-Lesen: daraus ergibt sich, welche Fassung draußen
  // gilt — und ob der Entwurf vor einem liegt.
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
  /** Wird ein Knoten per Taste gelöscht, müssen seine Kanten AUS DEM VOLLEN Graphen weg —
   *  in der gefilterten Sicht kennt React Flow die ausgeblendeten Kanten nicht und ließe
   *  sonst Verweise ins Leere zurück. */
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

  /** Gelöschte Verbindungen sind eine echte Änderung — das muss man auch sehen. */
  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      const weg = changes.filter((c) => c.type === "remove").length;
      if (weg) setMsg(tr("editor.verbindungen_geloescht", { anzahl: weg }));
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
  // Der zuletzt gespeicherte Graph als Text. Daran hängt die einzige Frage, die man beim
  // Verlassen des Editors wirklich hat: ist das hier schon gesichert? Vorher stand dort
  // eine Meldung, die nach dem nächsten Klick verschwand — und danach wusste es niemand
  // mehr.
  const gesichert = useRef<string>("");
  const [stand, setStand] = useState(0);   // zwingt die Kopfzeile zum Nachrechnen

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

  // Am Handy passen die drei Spalten nicht nebeneinander: gezeigt wird immer eine, und der
  // Wechsel hängt an dieser Auswahl. Am Schreibtisch bleibt alles, wie es war.
  const schmal = useSchmal();
  const [spalte, setSpalte] = useState<"flaeche" | "baustein">("flaeche");

  const onDropNode = useCallback(
    (type: WorkflowNodeType, pos: { x: number; y: number }, edgeId?: string) => {
      const id2 = `${type}_${Date.now()}`;
      const node: FlowNode = { id: id2, type, position: pos, data: { config: defaultConfig(type) } };
      setNodes((ns) => ns.concat(node));
      setSelectedId(id2);

      // Ein Baustein, der in der Luft hängt, ist ein Validierungsfehler und Handarbeit
      // obendrein: erst ziehen, dann zwei Verbindungen nachziehen. Also verbinden, wo die
      // Absicht eindeutig ist — auf einer Linie abgelegt heißt „dazwischen", bei
      // ausgewähltem Knoten heißt es „dahinter". Sonst bleibt er frei stehen.
      setEdges((es) => {
        /** Baustein in eine bestehende Verbindung schieben: sie endet jetzt bei ihm, und
         *  von ihm geht sie weiter. Ein Ende-Baustein schließt den Weg ab. */
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

        // Kein Treffer auf einer Linie: hinter den ausgewählten Knoten. Hängt dort schon
        // etwas, wird eingeschoben statt danebengestellt — „dahinter" ist die Erwartung,
        // und ein Baustein in der Luft ist Handarbeit plus Validierungsfehler.
        const quelle = nodes.find((n) => n.id === selectedId);
        if (!quelle || quelle.id === id2 || quelle.type === "end") return es;

        // Verzweigung, Freigabe und Schleife haben benannte Ausgänge — dort wird nicht
        // geraten, sondern der erste noch freie genommen.
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
        // Alles belegt: in die erste ausgehende Verbindung einschieben.
        const raus = es.find((e) => e.source === quelle.id);
        return raus ? einschieben(raus) : es;
      });
    },
    [setNodes, setEdges, nodes, selectedId]
  );

  /** Baustein per Tipp: er landet unter dem ausgewählten (oder unter dem letzten), den Rest
   *  — Verbinden, Auswählen — erledigt `onDropNode`. */
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
    setMsg(tr("editor.neu_angeordnet"));
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
      const graph = flowToGraph(nodes, edges);
      await workflowApi.saveVersion(wfId, version.id, { graph });
      gesichert.current = graphSignatur(graph);
      setStand((n) => n + 1);
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
      const graph = flowToGraph(nodes, edges);
      await workflowApi.saveVersion(wfId, version.id, { graph });
      gesichert.current = graphSignatur(graph);   // Validieren speichert mit
      setStand((n) => n + 1);
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
      const graph = flowToGraph(nodes, edges);
      await workflowApi.saveVersion(wfId, version.id, { graph });
      await workflowApi.publish(wfId, version.id);
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

  // Ungespeichert? Der Vergleich läuft gegen den zuletzt gesicherten Graphen — auch eine
  // verschobene Karte zählt, denn Positionen werden mitgespeichert.
  const jetzt = useMemo(() => graphSignatur(flowToGraph(nodes, edges)), [nodes, edges]);
  const geaendert = !nurLesen && seeded.current && jetzt !== gesichert.current;

  // Und welche Fassung gilt draußen? Drei Lagen, die man auseinanderhalten muss: noch nie
  // veröffentlicht, veröffentlicht und identisch, oder veröffentlicht und der Entwurf ist
  // weiter. Die dritte war bisher unsichtbar — man baut um, freut sich, und draußen läuft
  // weiter die alte Fassung.
  const liveVersion = veroeffentlicht?.find((v) => v.id === def?.current_version_id);
  // Verglichen wird der INHALT, nicht die Versionsnummer: der Editor legt beim Öffnen eine
  // frische Entwurfsfassung an, die zwar eine neue Nummer trägt, aber Zeichen für Zeichen
  // dasselbe enthält. Nach der Nummer zu gehen hieße, jeden Ablauf beim bloßen Anschauen
  // als „nicht veröffentlicht" auszuweisen.
  const gleichWieLive = !!liveVersion && jetzt === graphSignatur(liveVersion.graph);
  const veroeffentlichung = !def?.current_version_id
    ? { text: tr("editor.nie_veroeffentlicht"), stil: "text-muted",
        titel: tr("editor.nie_veroeffentlicht_titel") }
    : gleichWieLive
      ? { text: `veröffentlicht (v${liveVersion?.version ?? "?"})`, stil: "text-green-400",
          titel: tr("editor.live_titel") }
      : { text: `weicht von v${liveVersion?.version ?? "?"} ab`, stil: "text-amber-300",
          titel: tr("editor.abweichung_titel") };

  // Beim Verlassen des Fensters mit ungespeicherter Arbeit nachfragen — der Browser
  // erlaubt nur seinen eigenen Text, aber die Rückfrage selbst ist der Punkt.
  useEffect(() => {
    if (!geaendert) return;
    const warnen = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warnen);
    return () => window.removeEventListener("beforeunload", warnen);
  }, [geaendert]);

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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-card px-3 py-2 sm:px-4">
        <button
          // Zurück dorthin, wo man hergekommen ist. Der Aufrufer gibt seine eigene Adresse
          // als `state.from` mit; nur wenn sie fehlt (Lesezeichen, neu geladene Seite), wird
          // sie aus dem Ablauf selbst erschlossen — Projekt-Ablauf zur Projektübersicht,
          // Slot-Ablauf zum Standard-Satz, freier Ablauf zu den eigenen Prozessen. Vorher
          // landete ein Slot-Ablauf in den Einstellungen, wo er gar nicht steht.
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
        <span className={`rounded px-1.5 py-0.5 text-xs ${veroeffentlichung.stil}`}
          title={veroeffentlichung.titel}>
          {veroeffentlichung.text}
        </span>
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
                // Der Entwurf bringt keine Größen mit — erst anordnen, dann zeichnen,
                // sonst klebt alles im selben Raster übereinander.
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
    </div>
  );
}
