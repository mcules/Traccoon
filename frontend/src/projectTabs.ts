import type { Project } from "./api";
import type { ChromeTab } from "./pageChrome";

/** Tab keys of the project sub-menu (including the board sub-views). */
export type ProjectTab =
  | "board" | "list" | "backlog" | "archiv" | "code" | "dashboard" | "pm"
  | "monitor" | "buero" | "workflows" | "members" | "hardware" | "testenvs" | "settings";

/** Ticket views under "board": represented in the sub-menu only as "board", and below that
 *  in the board itself as buttons. */
export const BOARD_VIEWS: [ProjectTab, string][] = [
  ["board", "Board"], ["list", "Liste"], ["backlog", "Backlog"], ["archiv", "Archiv"],
];

/** Icon per tab for the pill navigation in the header. */
export const TAB_ICONS: Record<string, string> = {
  pm: "💬", board: "🗂️", code: "📁", dashboard: "📊",
  monitor: "⚡", buero: "🏢", workflows: "🔀", hardware: "🖥️", testenvs: "🧪", settings: "⚙️",
};

/** Role and flag dependent tabs of the project. Empty without a project, so that callers can
 *  make the hook call before their project guard. */
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
    // The office shows the same runs as the monitor, only as a room, so the same gate.
    ...(project.my_ai_assign ? ([["buero", "Büro"]] as [ProjectTab, string][]) : []),
    ...(canManage ? ([["workflows", "Prozesse"]] as [ProjectTab, string][]) : []),
    ...(project.has_hardware ? ([["hardware", "Hardware"]] as [ProjectTab, string][]) : []),
    ...(project.testenv_enabled !== false && canWrite
      ? ([["testenvs", "Testumgebungen"]] as [ProjectTab, string][]) : []),
    // Members lie in the project settings (the ProjectSettings tab)
    ...(canManage ? ([["settings", "Einstellungen"]] as [ProjectTab, string][]) : []),
  ];
}

/** Tab to chrome tabs (the header sub-menu). `activeBoardView` keeps "board" highlighted even
 *  when list, backlog or archive is active (the link points at the current URL). On the
 *  ticket page the parameter stays empty. */
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
