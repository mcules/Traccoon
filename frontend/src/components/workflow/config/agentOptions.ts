import { tr } from "../../../i18n";

export interface AgentLite {
  id: number;
  role: string;
  display_name: string;
  user_id?: number | null;
  project_id?: number | null;
  customized?: boolean;
  origin_agent_id?: number | null;
  active?: boolean;
}

/**
 * Selection list of the agents: **one row per role**.
 *
 * Why deduplicate: only the role name is stored. The same role can be defined several times
 * though (shipped system wide, personal, as a project copy), and in the list the entries
 * looked identical although in the flow they come down to the same choice anyway.
 *
 * The precedence is as in the worker (`_load_agent`): an own definition before a shipped
 * one, and project bound before project-less. Behind the name it therefore says which
 * definition really applies, and whether there are more.
 */
export function agentOptions(
  agents: AgentLite[] | undefined,
  opts: { empty?: string } = {},
): [string, string][] {
  const proRolle = new Map<string, AgentLite[]>();
  for (const a of agents || []) {
    if (a.active === false) continue;
    proRolle.set(a.role, [...(proRolle.get(a.role) || []), a]);
  }

  // A smaller number means higher precedence.
  const rang = (a: AgentLite) =>
    (a.user_id == null ? 2 : 0) + (a.project_id == null ? 1 : 0);

  const lines: [string, string][] = [];
  for (const [rolle, listing] of [...proRolle.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const [gewinner] = [...listing].sort((a, b) => rang(a) - rang(b));
    const herkunft =
      gewinner.project_id != null ? "Projekt"
        : gewinner.user_id != null ? tr("agent_options.persoenlich")
          : "Ausgeliefert";
    const extra = [
      herkunft,
      gewinner.customized ? "angepasst" : "",
      listing.length > 1 ? `${listing.length} Definitionen` : "",
    ].filter(Boolean).join(" · ");
    const name = gewinner.display_name || rolle;
    lines.push([rolle, `${name} (${rolle}) — ${extra}`]);
  }
  return opts.empty ? [["", opts.empty], ...lines] : lines;
}
