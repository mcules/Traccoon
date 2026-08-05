import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ApiError, DeploymentListe, DeploymentRow, DeploymentStatusFilter, deploymentApi,
} from "../api";
import { formatTime } from "../lib/formatTime";

// Eine Komponente für beide Orte (Dashboard-Karte und Einstellungen → Deployment), damit es
// über Status, Dauern und Log-Kopf nicht zwei Wahrheiten gibt. Die Hülle (Karte/Section)
// stellt die aufrufende Seite — hier steht nur der Inhalt.

/** Rohstatus → deutsche Beschriftung. Unbekannte Werte werden roh durchgereicht. */
const ST_LABEL: Record<string, string> = {
  ok: "erfolgreich", failed: "fehlgeschlagen", cancelled: "abgebrochen",
  building: "baut", pending: "wartet", "pending-check": "wartet (Prüfung)",
  rolledback: "zurückgerollt",
};
/** Textfarben aus dem vorhandenen Vorrat (AgentMonitor.ST_COLOR) — keine neue Farbsprache. */
const ST_TEXT: Record<string, string> = {
  ok: "text-green-400", failed: "text-red-400", building: "text-yellow-400",
  pending: "text-sky-400", "pending-check": "text-sky-400",
  rolledback: "text-orange-400", cancelled: "text-muted",
};
/** Balkenfarben wie Dashboard.KAT_FARBE. „abgebrochen“ bewusst blass. */
const ST_BAR: Record<string, string> = {
  ok: "bg-green-400", failed: "bg-red-400", building: "bg-yellow-400",
  pending: "bg-sky-400", "pending-check": "bg-sky-400",
  rolledback: "bg-orange-400", cancelled: "bg-slate-600",
};
const KIND_LABEL: Record<string, string> = {
  self: "Wartungs-Update", check: "nur Prüfung", stack: "Stack",
};
const QUELLE_LABEL: Record<string, string> = {
  agent: "Agent", merge: "Merge", workflow: "Prozess", maintenance: "Wartung",
};
const FILTER: [DeploymentStatusFilter, string][] = [
  // `running` umfasst serverseitig die Warteschlange mit („noch nicht entschieden“) —
  // deshalb „Offen“ und nicht „Läuft“.
  ["all", "Alle"], ["running", "Offen"], ["ok", "Erfolgreich"],
  ["failed", "Fehlgeschlagen"], ["other", "Sonstige"],
];
/** Die API kennt kein „ohne Fenster“: `since_hours` ist Pflicht mit Standard 720 h und
 *  Höchstwert 8760 h. Ein Eintrag „Alles“ wäre also eine Lüge über 30 Tage. */
const FENSTER: [number, string][] = [
  [24, "24 Stunden"], [168, "7 Tage"], [720, "30 Tage"], [8760, "1 Jahr"],
];
const FENSTER_STANDARD = 720;
/** `LIMIT_MAX` der API. Darüber hinaus nachzuladen brächte nichts als denselben Ausschnitt. */
const LIMIT_MAX = 200;

export type DeploymentVariante = "kompakt" | "voll";

export interface DeploymentsPanelProps {
  /** Projektbezogene Liste. Fehlt sie, wird die globale gelesen (Wartungs-Updates ohne Projekt). */
  projectId?: number;
  /** Nur Deployments dieses Tickets (laut Vertrag nur projektbezogen wirksam). */
  issueId?: number;
  /** `kompakt` = Karte im Dashboard (wenige Zeilen, keine Filter), `voll` = Einstellungen. */
  variante?: DeploymentVariante;
  /** Anfangs-Obergrenze; Standard 5 (kompakt) bzw. 50 (voll). */
  limit?: number;
}

export default function DeploymentsPanel(
  { projectId, issueId, variante = "voll", limit }: DeploymentsPanelProps,
) {
  const kompakt = variante === "kompakt";
  const [status, setStatus] = useState<DeploymentStatusFilter>("all");
  const [seit, setSeit] = useState(FENSTER_STANDARD);   // Stunden, immer explizit
  const [max, setMax] = useState(limit ?? (kompakt ? 5 : 50));
  const [offen, setOffen] = useState<number | null>(null);

  const { data, error, isLoading } = useQuery<DeploymentListe>({
    queryKey: ["deployments", projectId ?? null, issueId ?? null, status, seit, max],
    queryFn: () => deploymentApi.list({ projectId, issueId, limit: max, sinceHours: seit, status }),
    // Deployments sind selten; ein laufendes soll trotzdem von allein weiterzählen.
    refetchInterval: 15000,
    retry: false,
  });

  if (error) {
    // Solange die Lese-API fehlt (oder das Recht), lieber eine ruhige Zeile als ein roter Kasten.
    const st = error instanceof ApiError ? error.status : 0;
    return (
      <div className="text-xs text-muted">
        {st === 404 ? "Deployment-Liste steht hier noch nicht zur Verfügung."
          : `Deployments konnten nicht geladen werden (${st || "Fehler"}).`}
      </div>
    );
  }
  if (isLoading || !data) return <div className="text-xs text-muted">Lädt…</div>;

  const items = data.items || [];

  return (
    <div className="space-y-3">
      {!kompakt && (
        <div className="flex flex-wrap items-center gap-2">
          {FILTER.map(([f, label]) => (
            <button key={f} onClick={() => setStatus(f)}
              className={`rounded border px-2 py-1 text-xs ${status === f
                ? "border-brand text-ink" : "border-line text-muted hover:text-ink"}`}>
              {label}
            </button>
          ))}
          <select value={seit} onChange={(e) => setSeit(+e.target.value)}
            className="ml-auto rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
            {FENSTER.map(([h, label]) => <option key={h} value={h}>{label}</option>)}
          </select>
        </div>
      )}

      <Kopf by={data.by_status} count={data.count} truncated={data.truncated}
        kompakt={kompakt} fenster={seit} />

      {items.length === 0 ? (
        // Leer wegen Filter oder leer wegen Bestand — das ist nicht dieselbe Nachricht.
        <div className="text-xs text-muted">
          {Object.values(data.by_status || {}).some((n) => n > 0)
            ? "Keine Zeile passt zum gewählten Filter."
            : `In ${fensterText(seit)} wurde nichts deployt.`}
        </div>
      ) : (
        <div className="divide-y divide-line">
          {items.map((d) => (
            <Zeile key={d.id} d={d} kompakt={kompakt}
              auf={offen === d.id} toggle={() => setOffen(offen === d.id ? null : d.id)} />
          ))}
        </div>
      )}

      {!kompakt && data.truncated && max < LIMIT_MAX && (
        <button onClick={() => setMax(Math.min(LIMIT_MAX, max + 50))}
          className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
          Mehr laden
        </button>
      )}
    </div>
  );
}

/** Kopfzeile: `by_status` als Balken + Legende. Der Zusatz zu den abgebrochenen ist Pflicht —
 *  ohne ihn liest sich der große graue Block wie ein Fehlerbild, und er ist keiner.
 *
 *  `by_status` zählt gegen das **Zeitfenster**, nicht gegen den Statusfilter (so gebaut in
 *  `_payload`) — deshalb steht hier „im Fenster“ und getrennt davon, wie viele Zeilen die
 *  Liste darunter gerade zeigt. Beides zu verschmelzen wäre die Zahl, die niemand nachrechnen
 *  kann. */
function Kopf({ by, count, truncated, kompakt, fenster }: {
  by?: Record<string, number>; count: number; truncated?: boolean;
  kompakt: boolean; fenster: number;
}) {
  const eintraege = Object.entries(by || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const summe = eintraege.reduce((s, [, n]) => s + n, 0);
  if (!summe) return null;
  const abgebrochen = (by || {}).cancelled || 0;
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded">
        {eintraege.map(([s, n]) => (
          <div key={s} className={ST_BAR[s] || "bg-slate-500"} style={{ width: `${(n / summe) * 100}%` }}
            title={`${ST_LABEL[s] || s}: ${n}`} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {eintraege.map(([s, n]) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${ST_BAR[s] || "bg-slate-500"}`} />
            {ST_LABEL[s] || s}: <b className={s === "cancelled" ? "text-muted" : "text-ink"}>{n}</b>
          </span>
        ))}
        <span className="ml-auto">
          {summe} in {fensterText(fenster)} · {count} angezeigt{truncated ? " (gekürzt)" : ""}
        </span>
      </div>
      {abgebrochen > 0 && !kompakt && (
        <div className="mt-1.5 text-xs text-muted">
          „Abgebrochen“ schreibt kein Codepfad: die {abgebrochen} Zeilen stammen aus einer
          einmaligen, von Hand ausgeführten Aufräumaktion an einer festgefahrenen Warteschlange.
          Sie stehen hier, damit die Summe stimmt — als Fehler sind sie nicht zu lesen.
        </div>
      )}
      {abgebrochen > 0 && kompakt && (
        <div className="mt-1.5 text-xs text-muted">
          Die abgebrochenen stammen aus einer einmaligen Aufräumaktion, nicht aus einem Fehler.
        </div>
      )}
    </div>
  );
}

function Zeile({ d, kompakt, auf, toggle }: {
  d: DeploymentRow; kompakt: boolean; auf: boolean; toggle: () => void;
}) {
  const laeuft = d.phase === "running";
  // Der Log-Kopf ist der Grund, warum „fehlgeschlagen“ überhaupt verständlich wird — in der
  // vollen Liste immer, in der Karte dort, wo es nicht offensichtlich gut ausging.
  const zeigeKopf = !!d.log_head && (!kompakt || d.ok !== true);
  return (
    <div>
      <div role="button" tabIndex={0} onClick={toggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}
        className="flex cursor-pointer items-start gap-2 py-2 text-left hover:bg-surface/50">
        <OkZeichen ok={d.ok} laeuft={laeuft} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
            <span className={ST_TEXT[d.status] || "text-muted"}>{ST_LABEL[d.status] || d.status}</span>
            {/* `stack` ist der Normalfall und steht an jeder zweiten Zeile — nur die
                Abweichungen sind eine Nachricht. */}
            {d.kind && d.kind !== "stack" && (
              <span className="text-xs text-muted">{KIND_LABEL[d.kind] || d.kind}</span>
            )}
            {d.issue_key ? (
              d.project_key ? (
                <Link to={`/projects/${d.project_key}/tickets/${d.issue_key}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-xs text-brand hover:underline">{d.issue_key}</Link>
              ) : <span className="text-xs text-muted">{d.issue_key}</span>
            ) : <span className="text-xs text-muted">ohne Ticket</span>}
            <span className="ml-auto shrink-0 text-xs text-muted">{formatTime(d.created_at) || "—"}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
            <span>Warteschlange: {dauerText(d.wait_ms)}</span>
            <span>Arbeit: {dauerText(d.duration_ms)}</span>
            {!kompakt && <span>Auslöser: {quelleText(d.source)}</span>}
            {!kompakt && d.stack_dir && (
              <span className="truncate font-mono" title={d.stack_dir}>{d.stack_dir}</span>
            )}
          </div>
          {zeigeKopf && (
            <div className="mt-1 truncate font-mono text-[11px] text-muted" title={d.log_head || ""}>
              {einzeilig(d.log_head)}
            </div>
          )}
        </div>
        <span className="shrink-0 text-muted">{auf ? "▾" : "▸"}</span>
      </div>
      {auf && <LogAusklapper id={d.id} bytes={d.log_bytes} />}
    </div>
  );
}

/** Dreiwertiges `ok`. `null` heißt **unbekannt** — deshalb ein eigenes Zeichen und eine eigene
 *  Farbe, nicht das grüne Häkchen. Läuft es gerade, gewinnt das laufende Kennzeichen. */
function OkZeichen({ ok, laeuft }: { ok?: boolean | null; laeuft: boolean }) {
  if (laeuft) {
    return <span className="mt-0.5 animate-pulse text-yellow-400" title="läuft gerade">◐</span>;
  }
  if (ok === true) return <span className="mt-0.5 text-green-400" title="erfolgreich">✓</span>;
  if (ok === false) return <span className="mt-0.5 text-red-400" title="nicht erfolgreich">✗</span>;
  return <span className="mt-0.5 text-muted" title="unbekannt">•</span>;
}

/** Volltext-Log — wird erst beim Aufklappen geholt (bis ~20 000 Zeichen je Zeile). */
function LogAusklapper({ id, bytes }: { id: number; bytes?: number | null }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["deployment", id],
    queryFn: () => deploymentApi.get(id),
    staleTime: 60000,
    retry: false,
  });
  if (isLoading) return <div className="pb-2 pl-6 text-xs text-muted">Log wird geladen…</div>;
  if (error) {
    return <div className="pb-2 pl-6 text-xs text-muted">
      Log nicht abrufbar ({error instanceof ApiError ? error.status : "Fehler"}).
    </div>;
  }
  const log = data?.log || "";
  return (
    <div className="pb-3 pl-6">
      <div className="mb-1 text-xs text-muted">
        Log{typeof bytes === "number" ? ` · ${bytes} Bytes` : ""}
      </div>
      {log ? (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-surface p-2 font-mono text-[11px] text-muted">
          {log}
        </pre>
      ) : <div className="text-xs text-muted">Kein Log hinterlegt.</div>}
    </div>
  );
}

/** Dauer in der Schreibweise von `AgentMonitor.fmtDauer`. `null`/fehlt = „—“, **nie** „0 s“:
 *  bei 71 von 186 Zeilen fehlt ein Zeitstempel, eine gerechnete Null wäre gelogen. */
function dauerText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** `requested_by`/`chat_id` sind bei keiner einzigen Zeile gefüllt — der Auslöser kommt aus
 *  `source`, und bei Altzeilen gibt es ihn schlicht nicht. */
function quelleText(source?: string | null): string {
  if (!source) return "unbekannt";
  return QUELLE_LABEL[source] || source;
}

/** Das Zeitfenster benennen, statt es zu verschweigen: „61 erfolgreich“ ohne Zeitraum ist
 *  keine Aussage. Dativ, weil der Text immer hinter „in“ steht. */
function fensterText(stunden: number): string {
  if (stunden === 24) return "24 Stunden";
  if (stunden === 168) return "7 Tagen";
  if (stunden === 720) return "30 Tagen";
  if (stunden === 8760) return "einem Jahr";
  return stunden % 24 === 0 ? `${stunden / 24} Tagen` : `${stunden} Stunden`;
}

/** Der Log-Kopf enthält Zeilenumbrüche; in einer Tabellenzeile stört das nur. */
function einzeilig(s?: string | null): string {
  return (s || "").replace(/\s+/g, " ").trim();
}
