import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { tr } from "../i18n";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, Issue, Project, ProjectMeta } from "../api";
import { usePageChrome } from "../pageChrome";
import {
  OLD_SECTION, OLD_SUBVIEW, redirectOldTab, workViews, operationViews, canManage,
  canWrite, projectChromeTabs,
  projectTabs, projectPath, type ProjectTab,
} from "../projectTabs";
import { useAuth } from "../auth";
import TicketDrawer from "../components/TicketDrawer";
import NewTicketModal from "../components/NewTicketModal";
import { BUTTON } from "../components/ui";
// Monaco is large, so load it only when the code tab is opened.
const FilesPanel = lazy(() => import("../components/FilesPanel"));
// Canvas, pixel world and engine of the office do not belong in the main bundle: whoever
// never clicks the tab never loads them. No prewarming as with Monaco: the office is a
// separate, small chunk and there immediately on the first click.
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

// The tab list and the icons lie in ../projectTabs so that the ticket page can render the
// same sub-menu.
export default function ProjectView() {
  const { key, tab: tabParam, under } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [newOpen, setNewOpen] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const { user } = useAuth();

  // Opening a ticket: left click by preference (popup = drawer, page = full page);
  // a middle click always opens the full page in a new tab.
  const openTicket = (k: string, e?: React.MouseEvent) => {
    const url = `/projects/${key}/tickets/${k}`;
    if (e && e.button === 1) { window.open(url, "_blank", "noopener"); return; }
    if ((user?.ticket_open_mode || "popup") === "page") navigate(url);
    else setOpenKey(k);
  };

  // Old deep links: `?ticket=KEY` went to the ticket page, `?tab=…` to the view that has
  // taken over that content (see ALT in projectTabs).
  useEffect(() => {
    const t = searchParams.get("ticket");
    if (t) { navigate(`/projects/${key}/tickets/${t}`, { replace: true }); return; }
    const old = searchParams.get("tab");
    const target = old && key ? redirectOldTab(key, old) : null;
    if (target) { navigate(target, { replace: true }); return; }
    // German sections in the path (`/projects/X/arbeit/liste`) onto the English ones: addresses
    // are English, and what stands in bookmarks should still arrive.
    const newTab = tab ? OLD_SECTION[tab] : undefined;
    const newUnder = under ? OLD_SUBVIEW[under] : undefined;
    if (key && (newTab || newUnder)) {
      navigate(projectPath(key, (newTab || tab) as any, newUnder || under), { replace: true });
    }
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
    refetchInterval: 8000, // fallback in case WS is unavailable
  });
  const { data: archivedIssues } = useQuery({
    queryKey: ["issues-archived", project?.id],
    queryFn: () => api.get<Issue[]>(`/projects/${project!.id}/issues?archived=true`),
    enabled: !!project && under === "archive",
  });

  // Live updates over the real WebSocket (dispatcher and runner events)
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

  const mayManage = canManage(project);
  const mayWrite = canWrite(project);

  // Monaco/FilesPanel is a chunk of about 3.3 MB plus workers (ts.worker about 6 MB) and
  // would otherwise load only on a click on "code". When the code tab is available, prewarm
  // it completely while the page is idle: first the editor chunk, then the worker chunks, so that the first click is almost immediate.
  useEffect(() => {
    if (!(project?.git_enabled && mayManage)) return;
    const warm = async () => {
      await import("../components/FilesPanel");        // Editor-Chunk + monaco-core
      const m = await import("../monaco");
      m.prewarmMonaco?.();                              // Worker-Chunks herunterladen + cachen
    };
    const ric = (window as any).requestIdleCallback as undefined | ((cb: () => void, o?: any) => number);
    const cic = (window as any).cancelIdleCallback as undefined | ((id: number) => void);
    const id = ric ? ric(warm, { timeout: 4000 }) : (setTimeout(warm, 2000) as unknown as number);
    return () => { if (ric && cic) cic(id); else clearTimeout(id); };
  }, [project?.id, mayManage]);

  // Role and flag dependent tab list (shared with the ticket page). Computed before the
  // project guard so that usePageChrome (a hook) is called unconditionally.
  const tabs = useMemo<[ProjectTab, string][]>(() => projectTabs(project), [project]);
  const tab: ProjectTab = tabs.some(([k]) => k === tabParam) ? (tabParam as ProjectTab) : "work";

  // Views of the current group. Unknown or missing falls back to the first one, so that a
  // project without an office does not end up on an empty page.
  const work = workViews();
  const operation = useMemo(() => operationViews(project), [project]);
  const views: [string, string][] =
    tab === "work" ? work : tab === "operations" ? operation : [];
  const view = views.some(([k]) => k === under) ? under! : (views[0]?.[0] ?? "");

  usePageChrome(
    project?.name ?? "",
    projectChromeTabs(project, { tab, sub: view || under }),
    tab,
    "side",
  );

  if (!project) return <div className="text-muted">{tr("project_view.project_not_found")}</div>;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {project.managed && <span className="rounded bg-brand/20 px-1.5 py-0.5 text-xs text-brand">{tr("project_view.ai_managed")}</span>}
        {!project.my_ai_assign && (
          <span className="rounded bg-surface px-2 py-0.5 text-xs text-muted">{tr("project_view.ticket_system_no_ai_permission")}</span>
        )}
        {tab === "work" && issues && meta && (
          <div className="flex items-center gap-3 text-xs text-muted">
            <span>◷ {tr("project_view.count_total", { count: issues.length })}</span>
            <span>⚡ {tr("project_view.count_active", { count: issues.filter((i) => i.agent_working).length })}</span>
            <span className="text-green-400">✓ {tr("project_view.count_done", { count: issues.filter((i) =>
              meta.statuses.find((s) => s.id === i.status_id)?.category === "done").length })}</span>
          </div>
        )}
        {/* The views of a group: a segmented bar, not a menu level of its own. */}
        {views.length > 1 && (
          <div className="flex flex-wrap gap-1.5">
            {views.map(([k, label]) => (
              <Link key={k} to={projectPath(project.key, tab, k)}
                className={`rounded-md border px-3 py-1 text-sm ${
                  view === k
                    ? "border-brand bg-brand text-white"
                    : "border-line text-muted hover:bg-surface hover:text-ink"
                }`}>
                {label}
              </Link>
            ))}
          </div>
        )}
        <div className="hidden flex-1 sm:block" />
        {mayWrite && tab === "work" && (
          <button onClick={() => setNewOpen(true)} title={tr("project_view.new_ticket")}
            className={BUTTON.primary}>
            + <span className="hidden sm:inline">{tr("project_view.new_ticket")}</span>
          </button>
        )}
      </div>

      {tab === "work" && meta && issues && (
        <>
          {view === "board" && <Board project={project} meta={meta} issues={issues} onOpen={openTicket} />}
          {view === "list" && <IssueList project={project} meta={meta} issues={issues} onOpen={openTicket} />}
          {view === "backlog" && <Backlog project={project} meta={meta} issues={issues} onOpen={openTicket} />}
          {view === "archive" && (
            (archivedIssues && archivedIssues.length > 0)
              ? <IssueList project={project} meta={meta} issues={archivedIssues} onOpen={openTicket} />
              : <div className="text-sm text-muted">{tr("project_view.no_archived_tickets")}</div>
          )}
        </>
      )}
      {tab === "code" && (
        <Suspense fallback={<div className="text-sm text-muted">{tr("project_view.loading_editor")}</div>}>
          <FilesPanel project={project} />
        </Suspense>
      )}
      {tab === "dashboard" && <Dashboard project={project} />}
      {tab === "pm" && <PmChat project={project} />}
      {tab === "operations" && (
        <>
          {view === "monitor" && <AgentMonitor project={project} />}
          {view === "office" && (
            <Suspense fallback={<div className="text-sm text-muted">{tr("project_view.loading_office")}</div>}>
              <OfficeTab project={project} />
            </Suspense>
          )}
          {view === "testenvs" && mayWrite && <TestenvsPanel project={project} />}
          {view === "hardware" && <Hardware project={project} />}
        </>
      )}
      {tab === "settings" && mayManage && (
        <ProjectSettings project={project} area={under} />
      )}

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
