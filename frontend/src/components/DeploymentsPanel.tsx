import { useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ApiError, DeploymentListing, DeploymentRow, DeploymentStatusFilter, deploymentApi,
} from "../api";
import { formatTime } from "../lib/formatTime";
import { BUTTON, BUTTON_SMALL } from "./ui";

// One component for both places (dashboard card and Settings → Deployment), so that there
// are no two truths about status, durations and log head. The shell (card, section) is
// provided by the calling page; only the content stands here.

/** Raw status to label. Unknown values are passed through raw. */
const ST_LABEL: Record<string, string> = {
  ok: "erfolgreich", failed: "fehlgeschlagen", cancelled: "abgebrochen",
  building: "deploy.status.baut", pending: "deploy.status.wartet", "pending-check": "deploy.status.wartet_pruefung",
  rolledback: "deploy.status.zurueckgerollt",
};
/** Text colours from the existing supply (AgentMonitor.ST_COLOR), no new colour language. */
const ST_TEXT: Record<string, string> = {
  ok: "text-green-400", failed: "text-red-400", building: "text-yellow-400",
  pending: "text-sky-400", "pending-check": "text-sky-400",
  rolledback: "text-orange-400", cancelled: "text-muted",
};
/** Bar colours like Dashboard.KAT_FARBE. "aborted" deliberately pale. */
const ST_BAR: Record<string, string> = {
  ok: "bg-green-400", failed: "bg-red-400", building: "bg-yellow-400",
  pending: "bg-sky-400", "pending-check": "bg-sky-400",
  rolledback: "bg-orange-400", cancelled: "bg-slate-600",
};
const KIND_LABEL: Record<string, string> = {
  self: "deploy.art.wartung", check: "deploy.art.pruefung", stack: "deploy.art.stack",
};
const SOURCE_LABEL: Record<string, string> = {
  agent: "deployments_panel.quelle_agent", merge: "deployments_panel.quelle_merge",
  workflow: "deployments_panel.quelle_workflow", maintenance: "deployments_panel.quelle_maintenance",
  // The only value with a human behind it, the button below.
  manual: "deployments_panel.quelle_manual",
};
const FILTER: [DeploymentStatusFilter, string][] = [
  // Server side, `running` includes the queue ("not decided yet"), which is why it is
  // "open" and not "running".
  ["all", "deployments_panel.filter_alle"], ["running", "deployments_panel.filter_offen"],
  ["ok", "deployments_panel.filter_ok"], ["failed", "deployments_panel.filter_failed"],
  ["other", "deployments_panel.filter_other"],
];
/** The API knows no "without a window": `since_hours` is mandatory with a default of 720 h
 *  and a maximum of 8760 h. An entry "everything" would therefore be a lie about 30 days. */
const WINDOW: [number, string][] = [
  [24, "deployments_panel.fenster_24h"], [168, "deployments_panel.fenster_7t"],
  [720, "deployments_panel.fenster_30t"], [8760, "deployments_panel.fenster_1j"],
];
const WINDOW_STANDARD = 720;
/** `LIMIT_MAX` of the API. Loading more beyond that would bring nothing but the same excerpt. */
const LIMIT_MAX = 200;

export type DeploymentVariant = "kompakt" | "voll";

export interface DeploymentsPanelProps {
  /** Project bound list. Without it the global one is read (maintenance updates without a project). */
  projectId?: number;
  /** Only deployments of this ticket (by contract effective only project bound). */
  issueId?: number;
  /** `kompakt` = card on the dashboard (few rows, no filters), `voll` = settings. */
  variant?: DeploymentVariant;
  /** Anfangs-Obergrenze; Standard 5 (kompakt) bzw. 50 (voll). */
  limit?: number;
  /** The button "deploy now", only in the full list and only project bound.
   *
   *  Both come from outside because they cannot be fetched here: the role stands on the
   *  project (`my_role`), the stack directory in the project settings. The path is not an
   *  accessory: it stands in the confirmation, and without it "this stack is rebuilt" would
   *  be a claim nobody can check. If the property is missing entirely there is no button
   *  (dashboard card, ticket view). */
  fire?: { stackDir?: string | null; allowed: boolean };
}

export default function DeploymentsPanel(
  { projectId, issueId, variant = "voll", limit, fire }: DeploymentsPanelProps,
) {
  const compact = variant === "kompakt";
  const [status, setStatus] = useState<DeploymentStatusFilter>("all");
  const [since, setSince] = useState(WINDOW_STANDARD);   // Stunden, immer explizit
  const [max, setMax] = useState(limit ?? (compact ? 5 : 50));
  const [open, setOpen] = useState<number | null>(null);
  const qc = useQueryClient();

  const { data, error, isLoading } = useQuery<DeploymentListing>({
    queryKey: ["deployments", projectId ?? null, issueId ?? null, status, since, max],
    queryFn: () => deploymentApi.list({ projectId, issueId, limit: max, sinceHours: since, status }),
    // Deployments are rare; a running one should still count on by itself.
    refetchInterval: 15000,
    retry: false,
  });

  if (error) {
    // As long as the read API is missing (or the permission), a quiet line beats a red box.
    const st = error instanceof ApiError ? error.status : 0;
    return (
      <div className="text-xs text-muted">
        {st === 404 ? tr("deploy.liste_nicht_verfuegbar")
          : tr("deploy.laden_fehlgeschlagen", { code: st || "?" })}
      </div>
    );
  }
  if (isLoading || !data) return <div className="text-xs text-muted">{tr("deployments_panel.laedt")}</div>;

  const items = data.items || [];
  // "Is one already running?" is answered from `by_status` and **not** from `items`: the
  // count goes against the time window, the list on the other hand through the status filter.
  // With "successful" no open deploy would stand in `items`, and the button would be free
  // although the server answers with 409.
  const running = ["pending", "pending-check", "building"]
    .reduce((n, s) => n + ((data.by_status || {})[s] || 0), 0);

  return (
    <div className="space-y-3">
      {!compact && projectId != null && fire && (
        <Trigger projectId={projectId} issueId={issueId}
          stackDir={fire.stackDir} allowed={fire.allowed} running={running > 0}
          catchup={() => {
            // The fresh deploy is `pending`; with a narrower filter it would fall out of the
            // list and the button would look without consequence. "All" shows it anyway.
            if (status !== "all") setStatus("running");
            qc.invalidateQueries({ queryKey: ["deployments"] });
          }} />
      )}

      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          {FILTER.map(([f, label]) => (
            <button key={f} onClick={() => setStatus(f)}
              className={`rounded border px-2 py-1 text-xs ${status === f
                ? "border-brand text-ink" : "border-line text-muted hover:text-ink"}`}>
              {tr(label)}
            </button>
          ))}
          <select value={since} onChange={(e) => setSince(+e.target.value)}
            className="ml-auto rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
            {WINDOW.map(([h, label]) => <option key={h} value={h}>{tr(label)}</option>)}
          </select>
        </div>
      )}

      <Header by={data.by_status} count={data.count} truncated={data.truncated}
        compact={compact} window={since} />

      {items.length === 0 ? (
        // Empty because of the filter or empty because there is nothing: not the same message.
        <div className="text-xs text-muted">
          {Object.values(data.by_status || {}).some((n) => n > 0)
            ? tr("deploy.kein_treffer_filter")
            : tr("deployments_panel.nichts_deployt", { window: tr(windowText(since)) })}
        </div>
      ) : (
        <div className="divide-y divide-line">
          {items.map((d) => (
            <Line key={d.id} d={d} compact={compact}
              on={open === d.id} toggle={() => setOpen(open === d.id ? null : d.id)} />
          ))}
        </div>
      )}

      {!compact && data.truncated && max < LIMIT_MAX && (
        <button onClick={() => setMax(Math.min(LIMIT_MAX, max + 50))}
          className={BUTTON_SMALL.secondary}>
          Mehr laden
        </button>
      )}
    </div>
  );
}

/** The button including the confirmation.
 *
 *  Two stages, because a click here restarts a running service: the first click only opens
 *  the confirmation, and only the second queues. The confirmation names **the folder** and
 *  **the three consequences** (rebuild, short downtime, no rollback); a "really?" without
 *  content is a clicking exercise, not a consent.
 *
 *  The button is locked as long as something is running or the role is not enough; the
 *  server answers with 409 respectively 403 in both cases anyway, but a button that is
 *  certain to fail should not be offered. The reason stands as text beside it, not only as a
 *  `title`, because otherwise it is a grey area without an explanation. */
function Trigger({ projectId, issueId, stackDir, allowed: allowed, running, catchup }: {
  projectId: number; issueId?: number; stackDir?: string | null;
  allowed: boolean; running: boolean; catchup: () => void;
}) {
  const [question, setQuestion] = useState(false);
  const [sendet, setSendet] = useState(false);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState<number | null>(null);

  const folder = (stackDir || "").trim();
  // Order = urgency: no permission beats everything, then the missing target, then the
  // running deploy (which passes by itself).
  const reason = !allowed
    ? tr("deploy.rolle_fehlt")
    : !folder
      ? tr("deploy.kein_arbeitsverzeichnis")

      : running
        ? tr("deploy.laeuft_bereits")
        : "";

  const fire = async () => {
    setSendet(true); setError("");
    try {
      const d = await deploymentApi.create(projectId, issueId ? { issue_id: issueId } : {});
      setQueued(d.id);
      setQuestion(false);
      catchup();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : tr("deploy.einreihen_fehlgeschlagen"));
    } finally {
      setSendet(false);
    }
  };

  return (
    <div className="rounded border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => { setQuestion(true); setError(""); setQueued(null); }}
          disabled={!!reason || question || sendet}
          className={BUTTON.primary}>
          Jetzt deployen
        </button>
        <span className="text-xs text-muted">
          {reason || tr("deploy.baut_neu", { folder: folder })}
        </span>
      </div>

      {question && (
        <div className="mt-3 space-y-2 rounded border border-yellow-400/40 bg-surface p-3">
          <div className="text-sm text-ink">{tr("deployments_panel.diesen_stand_wirklich_ausrollen")}</div>
          <div className="text-xs text-muted">
            {tr("deployments_panel.was_passiert", { folder: folder })}
          </div>
          <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
            <li>{tr("deployments_panel.warnung_ausfall")}</li>
            <li>{tr("deployments_panel.warnung_kein_rollback")}</li>
            <li>{tr("deployments_panel.warnung_welcher_stand")}</li>
          </ul>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button onClick={fire} disabled={sendet}
              className={BUTTON.primary}>
              {tr(sendet ? "deployments_panel.wird_eingereiht" : "deployments_panel.ja_deployen")}
            </button>
            <button onClick={() => setQuestion(false)} disabled={sendet}
              className={BUTTON.secondary}>
              Abbrechen
            </button>
          </div>
        </div>
      )}

      {queued !== null && (
        <div className="mt-2 text-xs text-green-400">
          {tr("deployments_panel.eingereiht_als", { number: queued })}
        </div>
      )}
      {error && <div className="mt-2 text-xs text-red-400">{error}</div>}
    </div>
  );
}

/** Header: `by_status` as bars plus a legend. The addition about the aborted ones is
 *  mandatory: without it the large grey block reads like a picture of failure, and it is not.
 *
 *  `by_status` counts against the **time window**, not against the status filter (built that
 *  way in `_payload`), which is why it says "in the window" here and, separately, how many
 *  rows the list below is showing right now. Merging the two would be the number nobody can
 *  recompute. */
function Header({ by, count, truncated, compact, window: window }: {
  by?: Record<string, number>; count: number; truncated?: boolean;
  compact: boolean; window: number;
}) {
  const entries = Object.entries(by || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const sum_total = entries.reduce((s, [, n]) => s + n, 0);
  if (!sum_total) return null;
  const aborted = (by || {}).cancelled || 0;
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded">
        {entries.map(([s, n]) => (
          <div key={s} className={ST_BAR[s] || "bg-slate-500"} style={{ width: `${(n / sum_total) * 100}%` }}
            title={`${ST_LABEL[s] || s}: ${n}`} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {entries.map(([s, n]) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${ST_BAR[s] || "bg-slate-500"}`} />
            {ST_LABEL[s] || s}: <b className={s === "cancelled" ? "text-muted" : "text-ink"}>{n}</b>
          </span>
        ))}
        <span className="ml-auto">
          {tr("deployments_panel.zusammenfassung", { sum: sum_total, window: tr(windowText(window)), count })}
          {truncated ? ` ${tr("deployments_panel.gekuerzt")}` : ""}
        </span>
      </div>
      {aborted > 0 && !compact && (
        <div className="mt-1.5 text-xs text-muted">
          {tr("deployments_panel.abgebrochen_erklaerung", { count: aborted })}
        </div>
      )}
      {aborted > 0 && compact && (
        <div className="mt-1.5 text-xs text-muted">
          {tr("deployments_panel.abgebrochen_kurz")}
        </div>
      )}
    </div>
  );
}

function Line({ d, compact, on, toggle }: {
  d: DeploymentRow; compact: boolean; on: boolean; toggle: () => void;
}) {
  const running = d.phase === "running";
  // The log head is the reason "failed" becomes understandable at all: always in the full
  // list, and in the card where it did not obviously end well.
  const showHeader = !!d.log_head && (!compact || d.ok !== true);
  return (
    <div>
      <div role="button" tabIndex={0} onClick={toggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}
        className="flex cursor-pointer items-start gap-2 py-2 text-left hover:bg-surface/50">
        <OkChars ok={d.ok} runs={running} />
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
            ) : <span className="text-xs text-muted">{tr("deployments_panel.ohne_ticket")}</span>}
            <span className="ml-auto shrink-0 text-xs text-muted">{formatTime(d.created_at) || "—"}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
            <span>{tr("deployments_panel.warteschlange")}: {durationText(d.wait_ms)}</span>
            <span>{tr("deployments_panel.arbeit")}: {durationText(d.duration_ms)}</span>
            {!compact && <span>{tr("deployments_panel.ausloeser")}: {tr(sourceText(d.source))}</span>}
            {!compact && d.stack_dir && (
              <span className="truncate font-mono" title={d.stack_dir}>{d.stack_dir}</span>
            )}
          </div>
          {showHeader && (
            <div className="mt-1 truncate font-mono text-[11px] text-muted" title={d.log_head || ""}>
              {singleline(d.log_head)}
            </div>
          )}
        </div>
        <span className="shrink-0 text-muted">{on ? "▾" : "▸"}</span>
      </div>
      {on && <LogExpander id={d.id} bytes={d.log_bytes} />}
    </div>
  );
}

/** Three valued `ok`. `null` means **unknown**, which is why it gets a sign and a colour of
 *  its own, not the green tick. If it is running, the running mark wins. */
function OkChars({ ok, runs: running }: { ok?: boolean | null; runs: boolean }) {
  if (running) {
    return <span className="mt-0.5 animate-pulse text-yellow-400" title={tr("deployments_panel.laeuft_gerade")}>◐</span>;
  }
  if (ok === true) return <span className="mt-0.5 text-green-400" title="erfolgreich">✓</span>;
  if (ok === false) return <span className="mt-0.5 text-red-400" title={tr("deployments_panel.nicht_erfolgreich")}>✗</span>;
  return <span className="mt-0.5 text-muted" title="unbekannt">•</span>;
}

/** Full text log, fetched only on expanding (up to about 20 000 characters per row). */
function LogExpander({ id, bytes }: { id: number; bytes?: number | null }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["deployment", id],
    queryFn: () => deploymentApi.get(id),
    staleTime: 60000,
    retry: false,
  });
  if (isLoading) return <div className="pb-2 pl-6 text-xs text-muted">{tr("deployments_panel.log_wird_geladen")}</div>;
  if (error) {
    return <div className="pb-2 pl-6 text-xs text-muted">
      {tr("deployments_panel.log_nicht_abrufbar", {
        reason: error instanceof ApiError ? String(error.status) : tr("common.fehler") })}
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
      ) : <div className="text-xs text-muted">{tr("deployments_panel.kein_log_hinterlegt")}</div>}
    </div>
  );
}

/** Duration in the notation of `AgentMonitor.fmtDauer`. `null` or missing = "-", **never**
 *  "0 s": 71 of 186 rows lack a timestamp, and a computed zero would be a lie. */
function durationText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** `requested_by`/`chat_id` are filled on not a single row; the trigger comes from `source`,
 *  and with old rows it simply does not exist. */
function sourceText(source?: string | null): string {
  if (!source) return "deployments_panel.quelle_unbekannt";
  return SOURCE_LABEL[source] || source;
}

/** Name the time window instead of hiding it: "61 successful" without a period says nothing.
 *  Returns a key, the caller translates. The odd windows fall back to a counted text, because
 *  a number plus a unit survives translation while a hand written case ending does not. */
function windowText(hours: number): string {
  if (hours === 24) return "deployments_panel.fenster_24h";
  if (hours === 168) return "deployments_panel.fenster_7t";
  if (hours === 720) return "deployments_panel.fenster_30t";
  if (hours === 8760) return "deployments_panel.fenster_1j";
  return hours % 24 === 0
    ? tr("deployments_panel.fenster_tage", { count: hours / 24 })
    : tr("deployments_panel.fenster_stunden", { count: hours });
}

/** The log head contains line breaks; in a table row those are only a nuisance. */
function singleline(s?: string | null): string {
  return (s || "").replace(/\s+/g, " ").trim();
}
