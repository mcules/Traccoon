import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";

const EVENTS: [string, string, string][] = [
  ["comment", "Kommentar", "Jemand kommentiert das Ticket"],
  ["answer", "Antwort", "Rückfrage beantwortet oder Berechtigung entschieden"],
  ["manual", "Manuell", "Ausdrückliches Weiter über die Oberfläche"],
  ["any", "Beliebig", "Jedes Ereignis nimmt den Prozess wieder auf"],
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
      <div className="text-xs font-medium text-muted">{tr("wait_event_config.worauf_gewartet_wird")}</div>
      {EVENTS.map(([key, label, hint]) => (
        <label key={key} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={events.includes(key)}
            onChange={() => toggle(key)}
            className="mt-1"
          />
          <span>
            {label}
            <span className="block text-[11px] text-muted">{hint}</span>
          </span>
        </label>
      ))}
      <p className="text-[11px] text-muted">
        Wartet der Prozess hier, bleibt das Ticket in seinem aktuellen Zustand stehen. Eine
        Freigabe lässt sich damit <b>nicht</b> überspringen — dafür gibt es den Freigabe-Knoten.
      </p>
    </div>
  );
}
