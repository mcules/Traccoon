import type { NodeConfig, AutoActionConfig as AutoAction } from "../types";
import { KeyValueEditor } from "../kv";

const ACTIONS: [AutoAction["action"], string][] = [
  ["create_ticket", "Ticket anlegen"],
  ["notify", "Benachrichtigen"],
  ["webhook", "Webhook aufrufen"],
  ["set_context", "Kontext setzen"],
  ["set_board_status", "Board-Status setzen"],
];

const HINTS: Record<string, string> = {
  create_ticket: "params: summary, description, priority, type …",
  notify: "params: to, message …",
  webhook: "params: url, method, payload …",
  set_context: "params: <schlüssel> = <wert> (Werte dürfen {var:\"key\"} referenzieren)",
  set_board_status: "params: status …",
};

export default function AutoActionConfig({
  config,
  onChange,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
}) {
  const action: AutoAction = config.action || { action: "notify", params: {} };
  const setAction = (a: AutoAction) => onChange({ ...config, action: a });
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Aktion
        <select
          value={action.action}
          onChange={(e) => setAction({ ...action, action: e.target.value as AutoAction["action"] })}
          className={`mt-1 ${inp}`}
        >
          {ACTIONS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </label>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">Parameter</div>
        <KeyValueEditor value={action.params || {}} onChange={(p) => setAction({ ...action, params: p })} />
        <div className="mt-1 text-[10px] text-muted">{HINTS[action.action]}</div>
      </div>
    </div>
  );
}
