import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Issue, Project, ProjectMeta } from "../api";
import { usePageChrome } from "../pageChrome";
import TicketDrawer from "../components/TicketDrawer";

// Volle Ticket-Seite (Route /projects/:key/tickets/:ticketKey). Nutzt den TicketDrawer im
// asPage-Modus (kein Popup). Deep-linkbar, teilbar, echter Zurück-Weg zum Board.
export default function TicketView() {
  const { key, ticketKey } = useParams();
  const navigate = useNavigate();
  usePageChrome(ticketKey ?? "Ticket", []);

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

  if (!project) return <div className="text-muted">Projekt nicht gefunden.</div>;
  if (!meta) return <div className="text-muted">Lädt…</div>;

  return (
    <TicketDrawer
      asPage
      issueKey={ticketKey!}
      project={project}
      meta={meta}
      issues={issues || []}
      onOpen={(k) => navigate(`/projects/${project.key}/tickets/${k}`)}
      onClose={() => navigate(`/projects/${project.key}?tab=board`)}
    />
  );
}
