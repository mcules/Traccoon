import { useNavigate, useParams } from "react-router-dom";
import { tr } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { api, Issue, Project, ProjectMeta } from "../api";
import { usePageChrome } from "../pageChrome";
import { projectChromeTabs, projectPath } from "../projectTabs";
import TicketDrawer from "../components/TicketDrawer";

// Full ticket page (route /projects/:key/tickets/:ticketKey). Uses the TicketDrawer in
// asPage mode (no popup). Deep linkable, shareable, with a real way back to the board.
export default function TicketView() {
  const { key, ticketKey } = useParams();
  const navigate = useNavigate();

  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projects?.find((p) => p.key === key);
  const { data: meta } = useQuery({
    queryKey: ["meta", project?.id],
    queryFn: () => api.get<ProjectMeta>(`/projects/${project!.id}/meta`),
    enabled: !!project,
  });
  const { data: issues } = useQuery({
    queryKey: ["issues", project?.id],
    queryFn: () => api.get<Issue[]>(`/projects/${project!.id}/issues`),
    enabled: !!project,
  });

  // The sub-menu of the project stays visible on the ticket page (no tab is active, so the
  // way back into the project is always one click away).
  usePageChrome(ticketKey ?? "Ticket", projectChromeTabs(project), undefined, "side");

  if (!project) return <div className="text-muted">{tr("ticket_view.project_not_found")}</div>;
  if (!meta) return <div className="text-muted">{tr("ticket_view.loading")}</div>;

  return (
    <TicketDrawer
      asPage
      issueKey={ticketKey!}
      project={project}
      meta={meta}
      issues={issues || []}
      onOpen={(k) => navigate(`/projects/${project.key}/tickets/${k}`)}
      onClose={() => navigate(projectPath(project.key, "work", "board"))}
    />
  );
}
