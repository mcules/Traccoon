/**
 * Was in einem Lauf tatsächlich geschah — Schritt für Schritt.
 *
 * Jeder Schritt speichert seit jeher sein Ergebnis (`workflow_step_runs.result`), seine
 * Entscheidung und einen etwaigen Fehler. Angezeigt wurde davon nichts: die Instanz-Ansicht
 * malte den Graphen und färbte den Fortschritt ein, aber die erste Frage im Betrieb ist
 * eine andere — was kam von der Gegenstelle zurück, und warum ging es dann links statt
 * rechts?
 *
 * Dieselbe Darstellung bedient den Probelauf und den echten Lauf; ein Probelauf ist nichts
 * anderes als ein Lauf, in dem jede Aktion nur sagt, was sie täte.
 */
import { tr } from "../../i18n";
export interface Schritt {
  node_id: string;
  node_type: string;
  status?: string;
  decision?: string | null;
  result?: Record<string, any> | null;
  error?: string | null;
  entered_at?: string;
  completed_at?: string | null;
}

const STATUS_FARBE: Record<string, string> = {
  done: "text-green-400",
  failed: "text-red-400",
  waiting: "text-yellow-400",
  running: "text-sky-400",
  skipped: "text-muted",
};

/** Das Wesentliche eines Ergebnisses in einer Zeile — der Rest steht im Aufklapper. */
function kurzfassung(s: Schritt): string {
  if (s.error) return s.error;
  const r = s.result || {};
  if (r.probe) return String(r.probe);
  // Aktionen melden ihren Namen und das Nötigste; alles andere wäre hier Rauschen.
  const teile = Object.entries(r)
    .filter(([k]) => !["action", "probe"].includes(k))
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  const kopf = r.action ? String(r.action) : "";
  return [kopf, teile.join(" · ")].filter(Boolean).join(" — ").slice(0, 300);
}

function uhrzeit(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function Schrittprotokoll({
  schritte,
  leerText = "Noch kein Schritt gelaufen.",
  maxHoehe = "18rem",
}: {
  schritte: Schritt[];
  leerText?: string;
  maxHoehe?: string;
}) {
  if (!schritte.length) {
    return <div className="text-[11px] text-muted">{leerText}</div>;
  }
  return (
    <ul className="overflow-auto rounded border border-line bg-surface p-2 text-xs text-muted"
        style={{ maxHeight: maxHoehe }}>
      {schritte.map((s, i) => {
        const text = kurzfassung(s);
        const voll = s.result && Object.keys(s.result).length > 1;
        return (
          <li key={`${s.node_id}-${i}`} className="border-b border-line/60 py-1 last:border-0">
            <div className="flex items-baseline gap-1.5">
              <code className="font-mono text-ink">{s.node_id}</code>
              <span className="text-[11px] opacity-70">{s.node_type}</span>
              {s.status && (
                <span className={`text-[11px] ${STATUS_FARBE[s.status] || "opacity-70"}`}>
                  {s.status}
                </span>
              )}
              {s.decision && <span className="text-[11px] text-brand">→ {s.decision}</span>}
              <span className="ml-auto shrink-0 text-[11px] opacity-50">
                {uhrzeit(s.completed_at || s.entered_at)}
              </span>
            </div>
            {text && (
              <div className={`line-clamp-2 break-words text-[11px] leading-snug
                               ${s.error ? "text-red-300" : "opacity-90"}`}>
                {text}
              </div>
            )}
            {voll && (
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
