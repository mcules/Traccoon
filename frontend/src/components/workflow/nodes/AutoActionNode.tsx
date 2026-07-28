import { BaseNode, useSourceHandles, type FlowNodeProps, type SourceHandleDef } from "./shared";

const ACTION_LABEL: Record<string, string> = {
  create_ticket: "Ticket anlegen",
  notify: "Benachrichtigen",
  webhook: "Freie URL aufrufen",
  http_request: "Ziel aufrufen",
  set_context: "Kontext setzen",
  set_board_status: "Board-Spalte setzen",
  set_status: "Zustand setzen",
  set_field: "Feld setzen",
  refresh_facts: "Projekt-Fakten lesen",
  assign_agent: "Agent zuweisen",
  set_cap_baseline: "Kostenfenster zurücksetzen",
  split_tickets: "Teilaufgaben anlegen",
  stop_agent: "Agenten stoppen",
  start_testenv: "Testumgebung starten",
  stop_testenv: "Testumgebung abräumen",
  accept_merge: "Mergen / PR öffnen",
  deploy: "Deployment einreihen",
  comment: "Kommentar schreiben",
};

/** Aktionen, die asynchron laufen und ihren Ausgang nach dem Ergebnis benennen. */
const OUTCOMES: Record<string, SourceHandleDef[]> = {
  accept_merge: [
    { id: "merged", label: "gemerged", color: "!bg-green-500" },
    { id: "pr_open", label: "PR offen", color: "!bg-sky-500" },
    { id: "conflict", label: "Konflikt", color: "!bg-red-500" },
    { id: "out", label: "sonst" },
  ],
};

export default function AutoActionNode({ id, data, selected }: FlowNodeProps) {
  const a = data.config.action;
  // Ohne benannte Ausgänge blieben Kanten wie „merged" oder „conflict" ungezeichnet.
  const sources = useSourceHandles(id, OUTCOMES[a?.action ?? ""] ?? [{ id: "out" }]);
  return (
    <BaseNode
      nodeId={id}
      title={data.config.label || "Aktion"}
      icon="⚙"
      accent="border-t-sky-500"
      selected={selected}
      runtimeState={data.runtimeState}
      sources={sources}
    >
      <div>{a ? ACTION_LABEL[a.action] || a.action : "keine Aktion"}</div>
    </BaseNode>
  );
}
