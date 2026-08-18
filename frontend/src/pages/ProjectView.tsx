import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { tr } from "../i18n";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, Issue, Project, ProjectMeta } from "../api";
import { usePageChrome } from "../pageChrome";
import { BOARD_VIEWS, projectChromeTabs, projectTabs, type ProjectTab } from "../projectTabs";
import { useAuth } from "../auth";
import TicketDrawer from "../components/TicketDrawer";
import NewTicketModal from "../components/NewTicketModal";
// Monaco ist groß → nur laden, wenn der Code-Tab geöffnet wird.
const FilesPanel = lazy(() => import("../components/FilesPanel"));
// Canvas, Pixelwelt und Engine des Büros gehören nicht ins Haupt-Bundle: wer nie auf den
// Reiter klickt, lädt sie nie. Kein Vorwärmen wie bei Monaco — das Büro ist ein eigener,
// kleiner Brocken und beim ersten Klick sofort da.
const OfficeTab = lazy(() => import("../components/office/OfficeTab"));
import Hardware from "../components/Hardware";
import TestenvsPanel from "../components/TestenvsPanel";
import ProjectSettings from "../components/ProjectSettings";
import Backlog from "../components/Backlog";
import IssueList from "../components/IssueList";
import Dashboard from "../components/Dashboard";
import Board from "../components/Board";
import PmChat from "../components/PmChat";
import AgentMonitor from "../components/AgentMonitor";
import WorkflowList from "../components/workflow/WorkflowList";
import SlotList from "../components/workflow/SlotList";

// Reiter-Liste/Icons liegen in ../projectTabs, damit die Ticket-Seite dasselbe
// Untermenü rendern kann.
type Tab = ProjectTab;

export default function ProjectView() {
  const { key } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // Aktiver Tab kommt jetzt aus ?tab= (Header-Links). Rohwert früh ableiten, da die
  // archiv-Query ihn braucht; die endgültige Validierung gegen die (rollenabhängige)
  // Tab-Liste passiert weiter unten, sobald das Projekt geladen ist.
  const rawTab = (searchParams.get("tab") || "board") as Tab;
  const [newOpen, setNewOpen] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const { user } = useAuth();

  // Ticket öffnen: Linksklick per Präferenz (popup=Drawer, page=volle Seite);
  // Mittelklick öffnet immer die volle Seite in neuem Tab.
  const openTicket = (k: string, e?: React.MouseEvent) => {
    const url = `/projects/${key}/tickets/${k}`;
    if (e && e.button === 1) { window.open(url, "_blank", "noopener"); return; }
    if ((user?.ticket_open_mode || "popup") === "page") navigate(url);
    else setOpenKey(k);
  };

  // Alt-Deep-Link /projects/:key?ticket=KEY → auf die Ticket-Seite umleiten.
  useEffect(() => {
    const t = searchParams.get("ticket");
    if (t) navigate(`/projects/${key}/tickets/${t}`, { replace: true });
  }, []);

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const project = useMemo(() => projects?.find((p) => p.key === key), [projects, key]);

  const { data: meta } = useQuery({
    queryKey: ["meta", project?.id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${project!.id}/meta`),
    enabled: !!project,
  });
  const { data: issues } = useQuery({
    queryKey: ["issues", project?.id],
    queryFn: () => api.get<Issue[]>(`/projects/${project!.id}/issues`),
    enabled: !!project,
    refetchInterval: 8000, // Fallback, falls WS nicht verfügbar
  });
  const { data: archivedIssues } = useQuery({
    queryKey: ["issues-archived", project?.id],
    queryFn: () => api.get<Issue[]>(`/projects/${project!.id}/issues?archived=true`),
    enabled: !!project && rawTab === "archiv",
  });

  // Live-Updates über den echten WebSocket (Dispatcher/Runner-Events)
  const qc = useQueryClient();
  useEffect(() => {
    if (!project) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${proto}://${location.host}/api/projects/${project.id}/ws?token=${getToken()}`);
      ws.onmessage = () => {
        qc.invalidateQueries({ queryKey: ["issues", project.id] });
      };
    } catch {
      /* Polling-Fallback greift */
    }
    return () => ws?.close();
  }, [project?.id]);

  const canManage = project?.my_role === "owner" || project?.my_role === "maintainer";
  const canWrite = canManage || project?.my_role === "member";

  // Monaco/FilesPanel ist ein ~3,3 MB-Chunk + Worker (ts.worker ~6 MB) und lädt sonst erst beim
  // Klick auf „Code". Wenn der Code-Tab verfügbar ist, während die Seite schon steht im Leerlauf
  // vollständig vorwärmen: erst den Editor-Chunk, dann die Worker-Chunks → erster Klick fast sofort.
  useEffect(() => {
    if (!(project?.git_enabled && canManage)) return;
    const warm = async () => {
      await import("../components/FilesPanel");        // Editor-Chunk + monaco-core
      const m = await import("../monaco");
      m.prewarmMonaco?.();                              // Worker-Chunks herunterladen + cachen
    };
    const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: any) => number);
    const cic = (window as any).cancelIdleCallback as undefined | ((id: number) => void);
    const id = ric ? ric(warm, { timeout: 4000 }) : (setTimeout(warm, 2000) as unknown as number);
    return () => { if (ric && cic) cic(id); else clearTimeout(id); };
  }, [project?.id, canManage]);

  // Rollen-/Flag-abhängige Tab-Liste (geteilt mit der Ticket-Seite). Vor dem Projekt-Guard
  // berechnet, damit usePageChrome (ein Hook) unbedingt aufgerufen wird.
  const tabs = useMemo<[Tab, string][]>(() => projectTabs(project), [project]);

  // Gültige Tabs = Untermenü-Tabs + die Board-Ansichten (list/backlog/archiv sind nicht im
  // Untermenü, aber gültige Ziele der Board-Buttons).
  const validKeys = useMemo(
    () => new Set<string>([...tabs.map(([k]) => k), ...BOARD_VIEWS.map(([k]) => k)]),
    [tabs]
  );
  const tab: Tab = validKeys.has(rawTab) ? rawTab : "board";
  const inBoardGroup = BOARD_VIEWS.some(([k]) => k === tab);

  const switchTab = (k: Tab) => {
    const sp = new URLSearchParams(searchParams);
    sp.set("tab", k);
    setSearchParams(sp);
  };

  usePageChrome(
    project?.name ?? "",
    // „Board" bleibt im Untermenü hervorgehoben, solange eine Board-Ansicht (auch Liste/…) aktiv ist:
    // dazu zeigt sein Link auf die aktuelle URL, wenn wir in der Board-Gruppe sind.
    projectChromeTabs(project, inBoardGroup ? tab : undefined)
  );

  if (!project) return <div className="text-muted">{tr("project_view.projekt_nicht_gefunden")}</div>;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {project.managed && <span className="rounded bg-brand/20 px-1.5 py-0.5 text-xs text-brand">{tr("project_view.ki_gemanagt")}</span>}
        {!project.my_ai_assign && (
          <span className="rounded bg-surface px-2 py-0.5 text-xs text-muted">{tr("project_view.ticketsystem_kein_ki_recht")}</span>
        )}
        {issues && meta && (
          <div className="flex items-center gap-3 text-xs text-muted">
            <span>◷ {tr("project_view.gesamt", { anzahl: issues.length })}</span>
            <span>⚡ {tr("project_view.aktiv", { anzahl: issues.filter((i) => i.agent_working).length })}</span>
            <span className="text-green-400">✓ {tr("project_view.fertig", { anzahl: issues.filter((i) =>
              meta.statuses.find((s) => s.id === i.status_id)?.category === "done").length })}</span>
          </div>
        )}
        {/* Ticket-Ansichten unter „Board" als Buttons */}
        {inBoardGroup && (
          <div className="flex flex-wrap gap-1.5">
            {BOARD_VIEWS.map(([k, label]) => (
              <button key={k} onClick={() => switchTab(k)}
                className={`rounded-md border px-3 py-1 text-sm ${
                  tab === k
                    ? "border-brand bg-brand text-white"
                    : "border-line text-muted hover:bg-surface hover:text-ink"
                }`}>
                {label}
              </button>
            ))}
          </div>
        )}
        <div className="hidden flex-1 sm:block" />
        {canWrite && (
          <button onClick={() => setNewOpen(true)} title={tr("project_view.neues_ticket")}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white">
            + <span className="hidden sm:inline">{tr("project_view.neues_ticket")}</span>
          </button>
        )}
      </div>

      {tab === "board" && meta && issues && (
        <Board project={project} meta={meta} issues={issues} onOpen={openTicket} />
      )}
      {tab === "list" && meta && issues && (
        <IssueList project={project} meta={meta} issues={issues} onOpen={openTicket} />
      )}
      {tab === "backlog" && meta && issues && (
        <Backlog project={project} meta={meta} issues={issues} onOpen={openTicket} />
      )}
      {tab === "archiv" && meta && (
        (archivedIssues && archivedIssues.length > 0)
          ? <IssueList project={project} meta={meta} issues={archivedIssues} onOpen={openTicket} />
          : <div className="text-sm text-muted">{tr("project_view.keine_archivierten_tickets")}</div>
      )}
      {tab === "code" && (
        <Suspense fallback={<div className="text-sm text-muted">{tr("project_view.editor_laedt")}</div>}>
          <FilesPanel project={project} />
        </Suspense>
      )}
      {tab === "dashboard" && <Dashboard project={project} />}
      {tab === "pm" && <PmChat project={project} />}
      {tab === "monitor" && <AgentMonitor project={project} />}
      {tab === "buero" && (
        <Suspense fallback={<div className="text-sm text-muted">{tr("project_view.buero_laedt")}</div>}>
          <OfficeTab project={project} />
        </Suspense>
      )}
      {tab === "workflows" && canManage && (
        <div className="space-y-8">
          <SlotList project={project} />
          <div>
            <h3 className="mb-2 text-sm font-semibold">{tr("project_view.eigene_prozesse")}</h3>
            <WorkflowList project={project} />
          </div>
        </div>
      )}
      {tab === "hardware" && <Hardware project={project} />}
      {tab === "testenvs" && canWrite && <TestenvsPanel project={project} />}
      {tab === "settings" && canManage && <ProjectSettings project={project} />}

      {openKey && meta && (
        <TicketDrawer
          issueKey={openKey}
          project={project}
          meta={meta}
          issues={issues || []}
          onOpen={openTicket}
          onClose={() => setOpenKey(null)}
        />
      )}
      {newOpen && meta && (
        <NewTicketModal
          project={project}
          meta={meta}
          onClose={() => setNewOpen(false)}
        />
      )}
    </div>
  );
}
