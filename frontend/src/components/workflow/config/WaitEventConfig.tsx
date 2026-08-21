import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";

const EVENTS: [string, string, string][] = [
  ["comment", "wait_event.comment", "wait_event.somebody_comments_ticket"],
  ["answer", "wait_event.answer", "wait_event.question_answered_permission_decided"],
  ["manual", "wait_event.manual", "wait_event.explicit_continue_interface"],
  ["any", "wait_event.any", "wait_event.any_event_resumes_process"],
];

export default function WaitEventConfig({
  config,
  onChange,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
}) {
  const events = config.events?.length ? config.events : ["comment", "manual"];
  const toggle = (key: string) => {
    const next = events.includes(key) ? events.filter((e) => e !== key) : [...events, key];
    onChange({ ...config, events: next.length ? next : ["manual"] });
  };
  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted">{tr("wait_event_config.what_waited")}</div>
      {EVENTS.map(([key, label, hint]) => (
        <label key={key} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={events.includes(key)}
            onChange={() => toggle(key)}
            className="mt-1"
          />
          <span>
            {tr(label)}
            <span className="block text-[11px] text-muted">{tr(hint)}</span>
          </span>
        </label>
      ))}
      <p className="text-[11px] text-muted">
        {tr("wait_event_config.while_process_waits_here")}
      </p>
    </div>
  );
}
