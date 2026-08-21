/**
 * What actually happened in a run, step by step.
 *
 * Every step has always stored its result (`workflow_step_runs.result`), its decision and
 * any error. None of that was shown: the instance view drew the graph and coloured the
 * progress in, but the first question in operation is a different one: what came back from
 * the counterpart, and why did it then go left instead of right?
 *
 * The same presentation serves the trial run and the real run; a trial run is nothing other
 * than a run in which every action only says what it would do.
 */
import { tr } from "../../i18n";
import { timezone } from "../../lib/formatTime";
export interface Step {
  node_id: string;
  node_type: string;
  status?: string;
  decision?: string | null;
  result?: Record<string, any> | null;
  error?: string | null;
  entered_at?: string;
  completed_at?: string | null;
}

const STATUS_COLOR: Record<string, string> = {
  done: "text-green-400",
  failed: "text-red-400",
  waiting: "text-yellow-400",
  running: "text-sky-400",
  skipped: "text-muted",
};

/** The essentials of a result in one line; the rest stands in the expander. */
function shortform(s: Step): string {
  if (s.error) return s.error;
  const r = s.result || {};
  if (r.probe) return String(r.probe);
  // Actions report their name and the necessary minimum; everything else would be noise here.
  const parts = Object.entries(r)
    .filter(([k]) => !["action", "probe"].includes(k))
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  const header = r.action ? String(r.action) : "";
  return [header, parts.join(" · ")].filter(Boolean).join(" — ").slice(0, 300);
}

function time(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("de-DE",
    { hour: "2-digit", minute: "2-digit", timeZone: timezone() });
}

export default function Steplog({
  steps: steps,
  emptyText = tr("instanz.kein_schritt"),
  maxHeight = "18rem",
}: {
  steps: Step[];
  emptyText?: string;
  maxHeight?: string;
}) {
  if (!steps.length) {
    return <div className="text-[11px] text-muted">{emptyText}</div>;
  }
  return (
    <ul className="overflow-auto rounded border border-line bg-surface p-2 text-xs text-muted"
        style={{ maxHeight: maxHeight }}>
      {steps.map((s, i) => {
        const text = shortform(s);
        const full = s.result && Object.keys(s.result).length > 1;
        return (
          <li key={`${s.node_id}-${i}`} className="border-b border-line/60 py-1 last:border-0">
            <div className="flex items-baseline gap-1.5">
              <code className="font-mono text-ink">{s.node_id}</code>
              <span className="text-[11px] opacity-70">{s.node_type}</span>
              {s.status && (
                <span className={`text-[11px] ${STATUS_COLOR[s.status] || "opacity-70"}`}>
                  {s.status}
                </span>
              )}
              {s.decision && <span className="text-[11px] text-brand">→ {s.decision}</span>}
              <span className="ml-auto shrink-0 text-[11px] opacity-50">
                {time(s.completed_at || s.entered_at)}
              </span>
            </div>
            {text && (
              <div className={`line-clamp-2 break-words text-[11px] leading-snug
                               ${s.error ? "text-red-300" : "opacity-90"}`}>
                {text}
              </div>
            )}
            {full && (
              <details className="mt-0.5">
                <summary className="cursor-pointer text-[11px] opacity-60">{tr("schrittprotokoll.rohdaten")}</summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all
                                rounded bg-card p-1 text-[11px]">
                  {JSON.stringify(s.result, null, 1)}
                </pre>
              </details>
            )}
          </li>
        );
      })}
    </ul>
  );
}
