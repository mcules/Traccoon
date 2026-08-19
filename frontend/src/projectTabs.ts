import { tr } from "./i18n";
import type { Project } from "./api";
import type { ChromeTab } from "./pageChrome";

/**
 * The sub-menu of a project: six entries, not twelve.
 *
 * Twelve tabs (chat, board, code, dashboard, monitor, office, flows, hardware, test
 * environments, settings, plus list, backlog and archive as a second row of buttons) never
 * fitted into one line. They wrapped into two, and above the second row stood a third one
 * with the board views, and inside the settings a fourth with nine more. Four levels for
 * one project.
 *
 * Grouped by what one does with it since: **Arbeit** are the tickets in their four views,
 * **Betrieb** is what runs right now (monitor, office, test environments, hardware), and
 * **Einstellungen** is everything configuring, the flows of the project included. The views
 * inside a group stand in the page header as a segmented control, one click away and
 * without a level of their own.
 */
export type ProjectTab = "pm" | "arbeit" | "code" | "betrieb" | "dashboard" | "einstellungen";
export type ArbeitAnsicht = "board" | "liste" | "backlog" | "archiv";
export type BetriebAnsicht = "monitor" | "buero" | "testenvs" | "hardware";

export const TAB_ICONS: Record<ProjectTab, string> = {
  pm: "💬", arbeit: "🗂️", code: "📁", betrieb: "⚡", dashboard: "📊", einstellungen: "⚙️",
};

/** Path of a project view. One place, so that no caller has to know the shape. */
export function projektPfad(key: string, tab: ProjectTab, unter?: string): string {
  return `/projects/${key}/${tab}` + (unter ? `/${unter}` : "");
}

export function canManage(project: Project | undefined): boolean {
  return project?.my_role === "owner" || project?.my_role === "maintainer";
}

export function canWrite(project: Project | undefined): boolean {
  return canManage(project) || project?.my_role === "member";
}

/** The four ticket views of "Arbeit". */
export function arbeitAnsichten(): [ArbeitAnsicht, string][] {
  return [
    ["board", tr("projekt.ansicht_board")], ["liste", tr("projekt.ansicht_liste")],
    ["backlog", tr("projekt.ansicht_backlog")], ["archiv", tr("projekt.ansicht_archiv")],
  ];
}

/**
 * The views of "Betrieb", as far as this project has them.
 *
 * Monitor and office show the same runs, once as a list and once as a room, which is why
 * they were never two tabs but two views of one.
 */
export function betriebAnsichten(project: Project | undefined): [BetriebAnsicht, string][] {
  if (!project) return [];
  return [
    ...(project.my_ai_assign ? ([["monitor", tr("projekt.ansicht_monitor")],
                                 ["buero", tr("projekt.ansicht_buero")]] as [BetriebAnsicht, string][]) : []),
    ...(project.testenv_enabled !== false && canWrite(project)
      ? ([["testenvs", tr("projekt.ansicht_testenvs")]] as [BetriebAnsicht, string][]) : []),
    ...(project.has_hardware ? ([["hardware", tr("projekt.ansicht_hardware")]] as [BetriebAnsicht, string][]) : []),
  ];
}

/** Role and flag dependent tabs of the project. Empty without a project, so that callers can
 *  make the hook call before their project guard. */
export function projectTabs(project: Project | undefined): [ProjectTab, string][] {
  if (!project) return [];
  return [
    ...(project.my_ai_assign && project.pm_chat_enabled
      ? ([["pm", tr("projekt.tab_pm")]] as [ProjectTab, string][]) : []),
    ["arbeit", tr("projekt.tab_arbeit")],
    ...(canManage(project) && project.git_enabled
      ? ([["code", tr("projekt.tab_code")]] as [ProjectTab, string][]) : []),
    ...(betriebAnsichten(project).length
      ? ([["betrieb", tr("projekt.tab_betrieb")]] as [ProjectTab, string][]) : []),
    ["dashboard", tr("projekt.tab_dashboard")],
    ...(canManage(project) ? ([["einstellungen", tr("projekt.tab_einstellungen")]] as [ProjectTab, string][]) : []),
  ];
}

/**
 * Tab to chrome tabs (the sub-menu).
 *
 * `aktuell` keeps the group highlighted and its link pointing at the view one is in, so
 * that a click on "Arbeit" while in the backlog does not silently jump back to the board.
 */
export function projectChromeTabs(
  project: Project | undefined,
  aktuell?: { tab: ProjectTab; unter?: string },
): ChromeTab[] {
  if (!project) return [];
  return projectTabs(project).map(([key, label]) => ({
    key,
    label,
    icon: TAB_ICONS[key],
    to: projektPfad(project.key, key, aktuell && aktuell.tab === key ? aktuell.unter : undefined),
  }));
}

/**
 * Old addresses to new ones (`?tab=…`, the shape until August 2026).
 *
 * Links to them stand in tickets, in notes and in the vault, and a dead link is worse than
 * a redirect nobody notices.
 */
const ALT: Record<string, [ProjectTab, string?]> = {
  board: ["arbeit", "board"], list: ["arbeit", "liste"], liste: ["arbeit", "liste"],
  backlog: ["arbeit", "backlog"], archiv: ["arbeit", "archiv"],
  pm: ["pm"], code: ["code"], dashboard: ["dashboard"],
  monitor: ["betrieb", "monitor"], buero: ["betrieb", "buero"],
  testenvs: ["betrieb", "testenvs"], hardware: ["betrieb", "hardware"],
  workflows: ["einstellungen", "prozesse"], members: ["einstellungen", "mitglieder"],
  settings: ["einstellungen"],
};

export function altenTabUmleiten(key: string, alt: string): string | null {
  const ziel = ALT[alt];
  return ziel ? projektPfad(key, ziel[0], ziel[1]) : null;
}
