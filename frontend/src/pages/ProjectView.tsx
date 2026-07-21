import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, Issue, Project, ProjectMeta } from "../api";
import TicketDrawer from "../components/TicketDrawer";
import NewTicketModal from "../components/NewTicketModal";
// Monaco ist groß → nur laden, wenn der Code-Tab geöffnet wird.
const FilesPanel = lazy(() => import("../components/FilesPanel"));
import Members from "../components/Members";
import Hardware from "../components/Hardware";
import ProjectSettings from "../components/ProjectSettings";
import Backlog from "../components/Backlog";
import IssueList from "../components/IssueList";
import Dashboard from "../components/Dashboard";
import Board from "../components/Board";
import PmChat from "../components/PmChat";
import AgentMonitor from "../components/AgentMonitor";

type Tab = "board" | "list" | "backlog" | "archiv" | "code" | "dashboard" | "pm" | "monitor" | "members" | "hardware" | "settings";

export default function ProjectView() {
  const { key } = useParams();
  const [tab, setTab] = useState<Tab>("board");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

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
    enabled: !!project && tab === "archiv",
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

  if (!project) return <div className="text-muted">Projekt nicht gefunden.</div>;
  const canManage = project.my_role === "owner" || project.my_role === "maintainer";
  const canWrite = canManage || project.my_role === "member";

  const tabs: [Tab, string][] = [
    ["board", "Board"],
    ["list", "Liste"],
    ["backlog", "Backlog"],
    ["archiv", "Archiv"],
    ...(canManage && project.git_enabled ? ([["code", "Code"]] as [Tab, string][]) : []),
    ["dashboard", "Dashboard"],
    ...(project.my_ai_assign && project.pm_chat_enabled ? ([["pm", "PM-Chat"]] as [Tab, string][]) : []),
    ...(project.my_ai_assign ? ([["monitor", "Monitor"]] as [Tab, string][]) : []),
    ...(canManage ? ([["members", "Mitglieder"]] as [Tab, string][]) : []),
    ...(project.has_hardware ? ([["hardware", "Hardware"]] as [Tab, string][]) : []),
    ...(canManage ? ([["settings", "Einstellungen"]] as [Tab, string][]) : []),
  ];

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <span className="font-mono text-xs text-muted">{project.key}</span>
        <h1 className="text-lg font-semibold">{project.name}</h1>
        {project.managed && <span className="rounded bg-brand/20 px-1.5 py-0.5 text-xs text-brand">KI-gemanagt</span>}
        {!project.my_ai_assign && (
          <span className="rounded bg-surface px-2 py-0.5 text-xs text-muted">Ticketsystem (kein KI-Recht)</span>
        )}
        <div className="flex-1" />
        {canWrite && (
          <button onClick={() => setNewOpen(true)}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white">+ Neues Ticket</button>
        )}
      </div>

      <div className="mb-4 flex gap-1 border-b border-line">
        {tabs.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {label}
          </button>
        ))}
      </div>

      {tab === "board" && meta && issues && (
        <Board project={project} meta={meta} issues={issues} onOpen={setOpenKey} />
      )}
      {tab === "list" && meta && issues && (
        <IssueList project={project} meta={meta} issues={issues} onOpen={setOpenKey} />
      )}
      {tab === "backlog" && meta && issues && (
        <Backlog project={project} meta={meta} issues={issues} onOpen={setOpenKey} />
      )}
      {tab === "archiv" && meta && (
        (archivedIssues && archivedIssues.length > 0)
          ? <IssueList project={project} meta={meta} issues={archivedIssues} onOpen={setOpenKey} />
          : <div className="text-sm text-muted">Keine archivierten Tickets.</div>
      )}
      {tab === "code" && (
        <Suspense fallback={<div className="text-sm text-muted">Editor lädt…</div>}>
          <FilesPanel project={project} />
        </Suspense>
      )}
      {tab === "dashboard" && <Dashboard project={project} />}
      {tab === "pm" && <PmChat project={project} />}
      {tab === "monitor" && <AgentMonitor project={project} />}
      {tab === "members" && canManage && <Members project={project} />}
      {tab === "hardware" && <Hardware project={project} />}
      {tab === "settings" && canManage && <ProjectSettings project={project} />}

      {openKey && meta && (
        <TicketDrawer
          issueKey={openKey}
          project={project}
          meta={meta}
          issues={issues || []}
          onOpen={setOpenKey}
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
