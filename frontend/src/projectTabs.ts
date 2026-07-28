import type { Project } from "./api";
import type { ChromeTab } from "./pageChrome";

/** Reiter-Schlüssel des Projekt-Untermenüs (inkl. der Board-Unteransichten). */
export type ProjectTab =
  | "board" | "list" | "backlog" | "archiv" | "code" | "dashboard" | "pm"
  | "monitor" | "workflows" | "members" | "hardware" | "testenvs" | "settings";

/** Ticket-Ansichten unter „Board" — im Untermenü nur als „Board" vertreten,
 *  darunter im Board selbst als Buttons. */
export const BOARD_VIEWS: [ProjectTab, string][] = [
  ["board", "Board"], ["list", "Liste"], ["backlog", "Backlog"], ["archiv", "Archiv"],
];

/** Icon je Reiter für die Pill-Navigation im Header. */
export const TAB_ICONS: Record<string, string> = {
  pm: "💬", board: "🗂️", code: "📁", dashboard: "📊",
  monitor: "⚡", workflows: "🔀", hardware: "🖥️", testenvs: "🧪", settings: "⚙️",
};

/** Rollen-/Flag-abhängige Reiter des Projekts. Ohne Projekt leer, damit Aufrufer
 *  den Hook-Aufruf vor ihrem Projekt-Guard machen können. */
export function projectTabs(project: Project | undefined): [ProjectTab, string][] {
  if (!project) return [];
  const canManage = project.my_role === "owner" || project.my_role === "maintainer";
  const canWrite = canManage || project.my_role === "member";
  return [
    // PM-Chat zuerst
    ...(project.my_ai_assign && project.pm_chat_enabled ? ([["pm", "PM-Chat"]] as [ProjectTab, string][]) : []),
    ["board", "Board"],   // Liste/Backlog/Archiv liegen als Buttons UNTER Board (BOARD_VIEWS)
    ...(canManage && project.git_enabled ? ([["code", "Code"]] as [ProjectTab, string][]) : []),
    ["dashboard", "Dashboard"],
    ...(project.my_ai_assign ? ([["monitor", "Monitor"]] as [ProjectTab, string][]) : []),
    ...(canManage ? ([["workflows", "Prozesse"]] as [ProjectTab, string][]) : []),
    ...(project.has_hardware ? ([["hardware", "Hardware"]] as [ProjectTab, string][]) : []),
    ...(project.testenv_enabled !== false && canWrite
      ? ([["testenvs", "Testumgebungen"]] as [ProjectTab, string][]) : []),
    // Mitglieder liegen in den Projekt-Einstellungen (ProjectSettings-Reiter)
    ...(canManage ? ([["settings", "Einstellungen"]] as [ProjectTab, string][]) : []),
  ];
}

/** Reiter → Chrome-Tabs (Header-Untermenü). `activeBoardView` hält „Board" auch
 *  dann hervorgehoben, wenn Liste/Backlog/Archiv aktiv ist (Link zeigt auf die
 *  aktuelle URL). Auf der Ticket-Seite bleibt der Parameter leer. */
export function projectChromeTabs(
  project: Project | undefined,
  activeBoardView?: ProjectTab
): ChromeTab[] {
  if (!project) return [];
  return projectTabs(project).map(([key, label]) => ({
    key,
    label,
    icon: TAB_ICONS[key],
    to: key === "board" && activeBoardView
      ? `/projects/${project.key}?tab=${activeBoardView}`
      : `/projects/${project.key}?tab=${key}`,
  }));
}
