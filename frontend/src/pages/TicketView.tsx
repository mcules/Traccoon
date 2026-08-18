import { useNavigate, useParams } from "react-router-dom";
import { tr } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { api, Issue, Project, ProjectMeta } from "../api";
import { usePageChrome } from "../pageChrome";
import { projectChromeTabs } from "../projectTabs";
import TicketDrawer from "../components/TicketDrawer";

// Volle Ticket-Seite (Route /projects/:key/tickets/:ticketKey). Nutzt den TicketDrawer im
// asPage-Modus (kein Popup). Deep-linkbar, teilbar, echter Zurück-Weg zum Board.
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

  // Untermenü des Projekts bleibt auf der Ticket-Seite sichtbar (kein Reiter ist aktiv —
  // der Weg zurück ins Projekt ist damit immer einen Klick entfernt).
  usePageChrome(ticketKey ?? "Ticket", projectChromeTabs(project));

  if (!project) return <div className="text-muted">{tr("ticket_view.projekt_nicht_gefunden")}</div>;
  if (!meta) return <div className="text-muted">{tr("ticket_view.laedt")}</div>;

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
