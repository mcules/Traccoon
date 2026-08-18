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
 * Auswahlliste der Agenten — **eine Zeile je Rolle**.
 *
 * Warum entdoppeln: gespeichert wird nur der Rollenname. Dieselbe Rolle kann aber mehrfach
 * definiert sein (systemweit ausgeliefert, persönlich, als Projekt-Kopie) — in der Liste
 * sahen die Einträge identisch aus, obwohl sie im Ablauf ohnehin auf dieselbe Auswahl
 * hinauslaufen.
 *
 * Vorrang wie im Worker (`_load_agent`): eigene vor ausgelieferter Definition, und
 * projektbezogen vor projektlos. Hinter dem Namen steht deshalb, welche Definition wirklich
 * zieht — und ob es weitere gibt.
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

  // Kleinere Zahl = höherer Vorrang.
  const rang = (a: AgentLite) =>
    (a.user_id == null ? 2 : 0) + (a.project_id == null ? 1 : 0);

  const zeilen: [string, string][] = [];
  for (const [rolle, liste] of [...proRolle.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const [gewinner] = [...liste].sort((a, b) => rang(a) - rang(b));
    const herkunft =
      gewinner.project_id != null ? "Projekt"
        : gewinner.user_id != null ? tr("agent_options.persoenlich")
          : "Ausgeliefert";
    const zusatz = [
      herkunft,
      gewinner.customized ? "angepasst" : "",
      liste.length > 1 ? `${liste.length} Definitionen` : "",
    ].filter(Boolean).join(" · ");
    const name = gewinner.display_name || rolle;
    zeilen.push([rolle, `${name} (${rolle}) — ${zusatz}`]);
  }
  return opts.empty ? [["", opts.empty], ...zeilen] : zeilen;
}
