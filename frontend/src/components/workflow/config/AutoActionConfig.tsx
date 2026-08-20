import type { NodeConfig, AutoActionConfig as AutoAction, AutoActionName } from "../types";
import { tr } from "../../../i18n";
import type { MemberLite } from "../../../api";
import HttpRequestConfig from "./HttpRequestConfig";
import ActionParams from "./ActionParams";
import { ACTION_SPECS } from "./actionFields";

/** Actions grouped by topic; otherwise the list becomes confusing. */
const GROUPS: [string, [AutoActionName, string][]][] = [
  ["Artefakt", [
    ["set_status", "Zustand setzen"],
  ]],
  ["Allgemein", [
    ["http_request", "Ziel aufrufen (HTTP)"],
    ["tool_call", "Werkzeug aufrufen (MCP)"],
    ["set_context", "Kontext setzen"],
    ["comment", "Kommentar schreiben"],
    ["notify", "Benachrichtigen"],
    ["webhook", "Freie URL aufrufen"],
    ["create_ticket", "Ticket anlegen"],
    ["refresh_facts", "Projekt-Fakten lesen"],
    ["assistant_task", "Assistent beauftragen"],
    ["agent_run", "Agenten arbeiten lassen"],
    ["script", "Skript ausführen"],
    ["job_pause", "Zeitplan anhalten"],
    ["document", "Text ablegen"],
    ["document_read", "Abgelegten Text holen"],
    ["answer", "Antwort setzen"],
    ["mail_attachment", "Anhang einer Mail holen"],
  ]],
  ["Ticket", [
    ["set_board_status", "Board-Spalte setzen"],
    ["assign_agent", "Agent zuweisen"],
    ["set_cap_baseline", "action.set_cap_baseline"],
    ["split_tickets", "Teilaufgaben anlegen"],
    ["stop_agent", "Laufenden Agenten stoppen"],
  ]],
  ["Mail-Eingang", [
    ["mail_classify", "Mail einordnen"],
    ["spam_evaluate", "Spam beurteilen"],
    ["spam_card", "action.spam_card"],
    ["spam_apply", "action.spam_apply"],
    ["mail_assistant_task", "Assistent-Item anlegen"],
    ["mail_assistant_card", "Freigabekarte schicken"],
    ["mail_assistant_run", "Assistenten starten"],
  ]],
  ["Auslieferung", [
    ["start_testenv", "Testumgebung starten"],
    ["stop_testenv", "action.stop_testenv"],
    ["accept_merge", "action.accept_merge"],
    ["deploy", "Deployment einreihen"],
  ]],
];

/** Retrying and the error branch apply to EVERY action, which is why they stand below the
 *  selection and not in the fields of an individual one. */
function Fehlerverhalten({ config, onChange }: { config: NodeConfig; onChange: (c: NodeConfig) => void }) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink";
  return (
    <div className="space-y-2 rounded border border-line bg-surface p-2">
      <div className="text-xs font-medium text-muted">{tr("auto_action_config.wenn_es_schiefgeht")}</div>
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] text-muted">
          Wiederholungen
          <input
            type="number" min={0} max={10}
            value={(config.wiederholungen as number) ?? ""}
            onChange={(e) => onChange({ ...config,
              wiederholungen: e.target.value ? Number(e.target.value) : undefined })}
            placeholder="0"
            className={`mt-0.5 ${inp}`}
          />
        </label>
        <label className="flex-1 text-[11px] text-muted">
          Abstand (Sekunden)
          <input
            type="number" min={1}
            value={(config.warte_sek as number) ?? ""}
            onChange={(e) => onChange({ ...config,
              warte_sek: e.target.value ? Number(e.target.value) : undefined })}
            placeholder="30"
            className={`mt-0.5 ${inp}`}
          />
        </label>
      </div>
      <p className="text-[11px] text-muted">
        {tr("auto_action_config.wiederholung_hinweis")}
      </p>
    </div>
  );
}

export default function AutoActionConfig({
  config,
  onChange,
  members,
  projectId,
  subjectKind,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  members: MemberLite[];
  projectId?: number;
  subjectKind?: string;
}) {
  const action: AutoAction = config.action || { action: "notify", params: {} };
  // Show only what fits the subject of the flow: a hardware process needs no "merge branch",
  // and a ticket process no procurement status. The currently chosen action always stays
  // visible, even when it does not (yet) fit.
  const passt = (name: AutoActionName) => {
    const s = ACTION_SPECS[name]?.subjects;
    return !s || !subjectKind || s.includes(subjectKind as any);
  };
  const gruppen = GROUPS
    .map(([g, items]) => [g, items.filter(([k]) => passt(k) || k === action.action)] as const)
    .filter(([, items]) => items.length);
  // Action changed: do not carry the parameters of the old one along.
  const setAction = (name: AutoActionName) =>
    onChange({ ...config, action: { action: name, params: {} } });
  const setParams = (params: Record<string, any>) =>
    onChange({ ...config, action: { ...action, params } });
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Aktion
        <select
          value={action.action}
          onChange={(e) => setAction(e.target.value as AutoActionName)}
          className={`mt-1 ${inp}`}
        >
          {gruppen.map(([gruppe, items]) => (
            <optgroup key={gruppe} label={gruppe}>
              {items.map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>

      {action.action === "http_request" ? (
        <HttpRequestConfig
          params={action.params || {}}
          onChange={setParams}
          projectId={projectId}
        />
      ) : (
        <ActionParams
          action={action.action}
          params={action.params || {}}
          onChange={setParams}
          members={members}
          projectId={projectId}
          subjectKind={subjectKind}
        />
      )}

      <Fehlerverhalten config={config} onChange={onChange} />
    </div>
  );
}
