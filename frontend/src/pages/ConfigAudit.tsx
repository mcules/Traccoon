import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { language, tr } from "../i18n";
import { formatTime } from "../lib/formatTime";
import {
  Area, Button, BUTTON_SMALL, Figure, GradeIcon, Listing, ListingEmpty, ListRow, Tab, Tag,
} from "../components/ui";
import { Band, Mark, Point, Sparkline, StackedHistory } from "../components/charts";
import { usePageChrome } from "../pageChrome";

/**
 * The configuration audit: what the scanner found in the agent configurations.
 *
 * Three questions, in the order one asks them. How bad is it right now (the figures). How did
 * it get here (the chart, all stacks together or one of them). And what exactly is it (the
 * list, grouped by stack).
 *
 * The findings are not tickets and must not become a board: most of them are a state one
 * accepts for a while ("no deny list in a stack nobody but me touches"), and the two useful
 * gestures are "look at it" and "leave me alone with it". Hence a list with one handle.
 */

const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
type Severity = typeof SEVERITIES[number];

// Bottom to top of the stack — see `charts.tsx` for why the worst one lies on the base line.
const BANDS: Band[] = SEVERITIES.map((key) => ({ key, color: key }));

interface Finding {
  id: number; key: string; config: string; severity: Severity; title: string; file: string;
  rule: string; detail: string; status: string;
  first_seen: string; last_seen: string; seen_count: number;
}

interface RunConfig {
  config: string; grade: string; error: string;
  critical: number; high: number; medium: number; low: number; info: number;
}

interface Run {
  id: number; started_at: string; finished_at: string | null; trigger: string;
  configs: number; findings: number; new_count: number; fixed_count: number;
  critical: number; high: number; medium: number; low: number; info: number;
  per_config: RunConfig[];
}

interface Overview {
  open: Record<Severity, number>;
  ignored: number; fixed: number; stacks: number;
  last_run: null | {
    id: number; started_at: string; finished_at: string | null; trigger: string;
    configs: number; findings: number; new_count: number; fixed_count: number;
  };
}

const SEVERITY_TONE: Record<Severity, "bad" | "wait" | "quiet"> = {
  critical: "bad", high: "bad", medium: "wait", low: "quiet", info: "quiet",
};

function severityLabel(name: string) {
  return tr(`agentshield.sev_${name}`);
}

/** A day without the hour — for a badge, the minute of a scan is noise. */
function day(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(language(), { year: "2-digit", month: "2-digit", day: "2-digit" });
}

export default function ConfigAudit() {
  usePageChrome(tr("agentshield.title"), []);
  const client = useQueryClient();
  const [status, setStatus] = useState("open");
  const [picked, setPicked] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const overview = useQuery({
    queryKey: ["audit-overview"],
    queryFn: () => api.get<Overview>("/agentshield/overview"),
    refetchInterval: 60_000,
  });
  const history = useQuery({
    queryKey: ["audit-history"],
    queryFn: () => api.get<Run[]>("/agentshield/history"),
  });
  const findings = useQuery({
    queryKey: ["audit-findings", status],
    queryFn: () => api.get<Finding[]>(`/agentshield/findings?status=${status}`),
  });

  const setFindingStatus = useMutation({
    mutationFn: ({ id, next }: { id: number; next: string }) =>
      api.post(`/agentshield/findings/${id}/status`, { status: next }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["audit-findings"] });
      client.invalidateQueries({ queryKey: ["audit-overview"] });
    },
  });
  const scan = useMutation({
    mutationFn: () => api.post("/agentshield/scan", { trigger: "hand" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["audit-overview"] });
      client.invalidateQueries({ queryKey: ["audit-history"] });
      client.invalidateQueries({ queryKey: ["audit-findings"] });
    },
  });

  const runs = history.data || [];

  /** Per configuration: one slot per run, empty where it was not scanned. */
  const series = useMemo(() => {
    const out: Record<string, (Point | null)[]> = {};
    runs.forEach((run, i) => {
      run.per_config.forEach((c) => {
        if (!out[c.config]) out[c.config] = new Array(runs.length).fill(null);
        const values = Object.fromEntries(SEVERITIES.map((s) => [s, c[s]])) as Record<string, number>;
        out[c.config][i] = {
          at: run.started_at, values,
          total: SEVERITIES.reduce((sum, s) => sum + c[s], 0),
        };
      });
    });
    return out;
  }, [runs]);

  /** All configurations of a run added up — the one line that says whether it gets better. */
  const summed: Point[] = useMemo(() => runs.map((run) => ({
    at: run.started_at,
    values: Object.fromEntries(SEVERITIES.map((s) => [s, run[s]])) as Record<string, number>,
    total: SEVERITIES.reduce((sum, s) => sum + run[s], 0),
  })), [runs]);

  /** Who joined and who left, per run. Without this a jump in the curve has no explanation. */
  const marks: Mark[] = useMemo(() => {
    const out: Mark[] = runs.map((r) => ({ at: r.started_at, added: [], removed: [] }));
    Object.entries(series).forEach(([name, points]) => {
      for (let i = 1; i < points.length; i++) {
        if (points[i] && !points[i - 1]) out[i].added.push(name);
        if (!points[i] && points[i - 1]) out[i].removed.push(name);
      }
    });
    return out;
  }, [series, runs]);

  const names = useMemo(() => {
    const here = (name: string) => series[name][series[name].length - 1];
    const weight = (name: string) => {
      const point = here(name) || series[name].filter(Boolean).slice(-1)[0];
      if (!point) return -1;
      return SEVERITIES.reduce((w, s, i) =>
        w + (point.values[s] || 0) * Math.pow(100, SEVERITIES.length - i), 0);
    };
    return Object.keys(series).sort((a, b) =>
      (here(b) ? 1 : 0) - (here(a) ? 1 : 0) || weight(b) - weight(a) || a.localeCompare(b));
  }, [series]);

  const chartPoints = picked && series[picked]
    ? series[picked].filter(Boolean) as Point[]
    : summed;
  const chartMarks = picked && series[picked]
    ? series[picked].map((p, i) => p ? {
        at: p.at,
        added: i > 0 && !series[picked][i - 1] ? [picked] : [],
        removed: [] as string[],
      } : null).filter(Boolean) as Mark[]
    : marks;

  // The grade of the last run, per configuration — the list beside the curves shows it.
  const grades: Record<string, string> = {};
  runs[runs.length - 1]?.per_config.forEach((c) => { grades[c.config] = c.grade; });

  const shown = (findings.data || []).filter((f) => !picked || f.config === picked);
  const byConfig: Record<string, Finding[]> = {};
  shown.forEach((f) => { (byConfig[f.config] ||= []).push(f); });

  const last = overview.data?.last_run;

  return (
    <div className="space-y-4">
      <Area
        hint={tr("agentshield.page_intro")}
        tools={<>
          <span className="text-xs text-muted">
            {last ? tr("agentshield.last_run", {
              when: formatTime(last.started_at), configs: last.configs,
              new: last.new_count, gone: last.fixed_count,
            }) : tr("agentshield.no_run_yet")}
          </span>
          <div className="flex-1" />
          <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
            {scan.isPending ? tr("agentshield.scanning") : tr("agentshield.scan_now")}
          </Button>
        </>}>
        <div className="grid grid-cols-3 gap-y-3 sm:grid-cols-6">
          {SEVERITIES.map((s) => (
            <Figure bare key={s} label={severityLabel(s)} value={overview.data?.open[s] ?? 0}
              tone={overview.data?.open[s] ? SEVERITY_TONE[s] : "quiet"} />
          ))}
          <Figure bare label={tr("agentshield.state_ignored")} value={overview.data?.ignored ?? 0} />
        </div>
      </Area>

      <Area title={tr("agentshield.history")}
        subtitle={runs.length >= 2
          ? tr("agentshield.history_span", {
              runs: runs.length, from: formatTime(runs[0].started_at),
              to: formatTime(runs[runs.length - 1].started_at),
            })
          : undefined}>
        {runs.length < 2 ? (
          <p className="text-muted">{tr("agentshield.history_none")}</p>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap items-baseline gap-3">
              <span className="font-medium">{picked || tr("agentshield.history_all")}</span>
              {picked && (
                <button className={BUTTON_SMALL.secondary} onClick={() => setPicked("")}>
                  {tr("agentshield.show_all_again")}
                </button>
              )}
              <div className="flex-1" />
              <span className="flex flex-wrap gap-3 text-xs text-muted">
                {SEVERITIES.map((s) => (
                  <span key={s} className="inline-flex items-center gap-1">
                    <i className="h-2 w-2 rounded-sm" style={{ background: `rgb(var(--${s}))` }} />
                    {severityLabel(s)}
                  </span>
                ))}
              </span>
            </div>
            <StackedHistory points={chartPoints} bands={BANDS} marks={chartMarks}
              language={language()} labelTotal={tr("agentshield.total")} />

            <div className="mt-4 space-y-1">
              {names.map((name) => {
                const points = series[name];
                const here = points[points.length - 1];
                const known = points.filter(Boolean) as Point[];
                const delta = known.length >= 2
                  ? known[known.length - 1].total - known[known.length - 2].total : 0;
                const firstAt = points.findIndex(Boolean);
                const lastAt = points.length - 1 - points.slice().reverse().findIndex(Boolean);
                return (
                  <button key={name} onClick={() => setPicked(picked === name ? "" : name)}
                    aria-pressed={picked === name}
                    className={`grid w-full grid-cols-[minmax(0,1fr)_74px] items-center gap-3
                                rounded px-2 py-1.5 text-left hover:bg-surface
                                sm:grid-cols-[210px_minmax(0,1fr)_130px]
                                ${picked === name ? "bg-surface" : ""}`}>
                    <span className="col-span-2 flex min-w-0 flex-wrap items-center gap-2 sm:col-span-1">
                      <b className={`truncate font-medium ${here ? "" : "text-muted"}`}>{name}</b>
                      {here && <GradeIcon grade={grades[name] || "?"}
                        title={tr("agentshield.grade", { grade: grades[name] || "?" })} />}
                      {firstAt > 0 && (
                        <Tag color="green">{tr("agentshield.added_on", { when: day(points[firstAt]!.at) })}</Tag>
                      )}
                      {lastAt < points.length - 1 && (
                        <Tag>{tr("agentshield.removed_on", { when: day(points[lastAt]!.at) })}</Tag>
                      )}
                    </span>
                    <span className={here ? "" : "opacity-50"}>
                      <Sparkline points={points} bands={BANDS}
                        stamps={runs.map((r) => r.started_at)} />
                    </span>
                    <span className="flex flex-col items-end">
                      <b className={`tabular-nums ${here ? "text-base" : "text-muted"}`}>
                        {here ? here.total : "–"}
                      </b>
                      <span className={`text-[11px] ${delta > 0 ? "text-red-400"
                        : delta < 0 ? "text-green-400" : "text-muted"}`}>
                        {!here ? tr("agentshield.history_not_scanned")
                          : delta === 0 ? tr("agentshield.delta_same")
                          : tr(delta > 0 ? "agentshield.delta_more" : "agentshield.delta_less",
                               { count: Math.abs(delta) })}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </Area>

      <Area title={tr("agentshield.findings")}
        tools={<>
          <Tab active={status} onChoose={setStatus}
            selection={["open", "ignored", "fixed", "all"].map((value) =>
              [value, tr(`agentshield.state_${value}`)])} />
          <div className="flex-1" />
          <span className="text-xs text-muted">
            {tr("agentshield.shown_of_total", {
              shown: shown.length, total: findings.data?.length ?? 0 })}
          </span>
        </>}>
        <Listing>
          {Object.keys(byConfig).sort().map((config) => {
            const mine = byConfig[config];
            // Picked in the history means: this is the one being looked at, so it stands open
            // unless it was folded away by hand afterwards.
            const unfolded = open[config] ?? picked === config;
            const per: Record<string, number> = {};
            mine.forEach((f) => { per[f.severity] = (per[f.severity] || 0) + 1; });
            return (
              <div key={config}>
                <ListRow onClick={() => setOpen({ ...open, [config]: !unfolded })}>
                  <span className="flex w-full flex-wrap items-center gap-2">
                    <span className="text-muted">{unfolded ? "▾" : "▸"}</span>
                    <b className="font-medium">{config}</b>
                    {grades[config] && <GradeIcon grade={grades[config]}
                      title={tr("agentshield.grade", { grade: grades[config] })} />}
                    <span className="flex-1" />
                    {SEVERITIES.filter((s) => per[s]).map((s) => (
                      <span key={s} className="text-xs tabular-nums"
                        style={{ color: `rgb(var(--${s}))` }}>
                        {per[s]} {severityLabel(s)}
                      </span>
                    ))}
                    <span className="text-xs text-muted">
                      {tr(mine.length === 1 ? "agentshield.count_finding"
                        : "agentshield.count_findings", { count: mine.length })}
                    </span>
                  </span>
                </ListRow>
                {unfolded && mine.map((f) => (
                  <ListRow key={f.id} dimmed={f.status === "ignored"}>
                    <span className="grid w-full gap-2 sm:grid-cols-[74px_minmax(0,1fr)_auto]">
                      <span className="text-xs font-semibold uppercase tracking-wide"
                        style={{ color: `rgb(var(--${f.severity}))` }}>
                        {severityLabel(f.severity)}
                      </span>
                      <span className="min-w-0">
                        <span className="block">{f.title || tr("agentshield.no_title")}</span>
                        <span className="mt-0.5 block text-xs text-muted">
                          {f.file && <code className="font-mono">{f.file}</code>}
                          {f.rule && <> · {f.rule}</>}
                          {f.first_seen && <> · {tr("agentshield.known_since", { when: day(f.first_seen) })}</>}
                        </span>
                        {f.detail && <span className="mt-1 block text-xs text-muted">{f.detail}</span>}
                      </span>
                      <span className="flex items-start justify-end">
                        {f.status === "fixed" ? (
                          <span className="text-xs text-muted">
                            {tr("agentshield.gone_since", { when: formatTime(f.last_seen) })}
                          </span>
                        ) : (
                          <button className={BUTTON_SMALL.secondary}
                            disabled={setFindingStatus.isPending}
                            onClick={() => setFindingStatus.mutate({
                              id: f.id, next: f.status === "ignored" ? "open" : "ignored" })}>
                            {tr(f.status === "ignored" ? "agentshield.watch_again"
                              : "agentshield.ignore")}
                          </button>
                        )}
                      </span>
                    </span>
                  </ListRow>
                ))}
              </div>
            );
          })}
          {!shown.length && (
            <ListingEmpty>
              {findings.data?.length ? tr("agentshield.nothing_matches")
                : tr("agentshield.no_findings_yet")}
            </ListingEmpty>
          )}
        </Listing>
      </Area>
    </div>
  );
}
