import { useState } from "react";
import { formatDate, formatDateTime } from "../../lib/formatTime";
import { tr } from "../../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api";
import {
  Actions, Area, Errorrow, ICON, IconButton, Listing, ListingEmpty, ListenLine,
  DeleteDialog, BUTTON_SMALL, BUTTON_TEXT} from "../ui";

interface Trend {
  points: number; value: number | null; unit: string;
  per_day: number | null; days_left: number | null; empty_at: string | null;
  fit: number | null; age_hours: number | null; last_at: string | null;
  first_value: number | null; first_at: string | null;
}
interface Series {
  id: number; key: string; name: string; unit: string; description: string;
  last_value: number | null; last_at: string | null; warned_at: string | null;
  trend: Trend | null;
}
interface Point { id: number; ts: string; value: number; context: Record<string, any> }
interface History extends Series { target: number; points: Point[] }

const TIMESPANS: [number, string][] = [[7, "7 Tage"], [30, "30 Tage"], [90, "90 Tage"],
                                        [365, "1 Jahr"]];

/**
 * Metric series: the numbers flows write along.
 *
 * A flow only ever sees the moment. Only the history answers the question one really has:
 * where is this heading, and when do I have to act? That is why the forecast does not stand
 * here as a number alone but as a dashed extension of the points; a forecast one cannot
 * check is rightly not believed.
 */
export default function MetricseriesPanel() {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [deleteSeries, setDeleteSeries] = useState<any | null>(null);
  const [err, setErr] = useState("");

  const { data: series } = useQuery({
    queryKey: ["metrics"],
    queryFn: () => api.get<Series[]>("/metrics"),
    refetchInterval: 60_000,
  });

  const remove = useMutation({
    mutationFn: (key: string) => api.del(`/metrics/${encodeURIComponent(key)}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["metrics"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  return (
    <Area hint={tr("metric_series_panel.measurement_series_numbers_flows")}>
      <Errorrow text={err} />

      <Listing>
        {series?.map((r) => (
          <SeriesLine key={r.key} series={r}
            open={open === r.key}
            toggle={() => setOpen(open === r.key ? null : r.key)}
            remove={() => setDeleteSeries(r)} />
        ))}
        {!series?.length && <ListingEmpty>{tr("metric_series_panel.no_series_yet_one")}</ListingEmpty>}
      </Listing>
      {deleteSeries && (
        <DeleteDialog was={deleteSeries.key} runs={remove.isPending}
          onClose={() => setDeleteSeries(null)}
          onDelete={() => { remove.mutate(deleteSeries.key); setDeleteSeries(null); }} />
      )}
    </Area>
  );
}

function SeriesLine({ series: series, open: open, toggle, remove: remove }: {
  series: Series; open: boolean; toggle: () => void; remove: () => void;
}) {
  const t = series.trend;
  const knapp = t?.days_left != null && t.days_left <= 14;
  const old = (t?.age_hours ?? 0) > 26;

  return (
    <ListenLine warning={knapp}>
      <div className="flex flex-wrap items-baseline gap-2">
        <button onClick={toggle} className={BUTTON_TEXT.secondary}>
          {open ? "▾" : "▸"} {series.name}
        </button>
        <code className="font-mono text-[11px] text-muted">{series.key}</code>
        <div className="flex-1" />
        <span className="text-sm text-ink">{series.last_value ?? "—"} {series.unit}</span>
        <span className={`text-[11px] ${old ? "text-amber-300" : "text-muted"}`}>
          {formatDateTime(series.last_at)}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {t?.per_day != null && (
          <span>{t.per_day > 0 ? "+" : ""}{t.per_day} {series.unit}/Tag</span>
        )}
        {t?.days_left != null ? (
          <span className={knapp ? "text-amber-300" : ""}>
            {tr("metric_series.days_days_left_empty", { days: t.days_left, date: t.empty_at ?? "" })}
          </span>
        ) : (
          <span>{tr((t?.points ?? 0) < 3 ? "metric_series.too_few_values_forecast" : "metric_series.no_end_sight")}</span>
        )}
        {t?.fit != null && <span>{tr("metric_series.quality_value", { value: t.fit })}</span>}
        <span>{t?.points ?? 0} Werte</span>
        {old && t?.age_hours != null && (
          <span className="text-amber-300">
            {tr("metric_series.no_new_value_hours", { hours: Math.round(t.age_hours) })}
          </span>
        )}
        {series.warned_at && (
          <span className="text-amber-300">
            {tr("metric_series_panel.warned_date", { date: formatDate(series.warned_at) })}
          </span>
        )}
      </div>

      {open && <Detail series={series} remove={remove} />}
    </ListenLine>
  );
}

/** Everything about one series: choose the period, see the history, remove single values. */
function Detail({ series: series, remove: remove }: { series: Series; remove: () => void }) {
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const [target, setTarget] = useState(0);
  const [err, setErr] = useState("");
  const path = `/metrics/${encodeURIComponent(series.key)}/points`;

  const { data: history, isFetching } = useQuery({
    queryKey: ["metric-points", series.key, days, target],
    queryFn: () => api.get<History>(`${path}?days=${days}&target=${target}`),
  });

  const discard = useMutation({
    mutationFn: (pid: number) => api.del(`${path}/${pid}`),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["metric-points", series.key] });
      qc.invalidateQueries({ queryKey: ["metrics"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const points = history?.points || [];
  const t = history?.trend;

  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{tr("metric_series_panel.period")}</span>
        {TIMESPANS.map(([d, label]) => (
          <button key={d} onClick={() => setDays(d)}
            className={`rounded border px-2 py-0.5 ${
              days === d ? "border-brand text-brand" : "border-line text-muted hover:text-ink"}`}>
            {label}
          </button>
        ))}
        <div className="flex-1" />
        <label className="flex items-center gap-1 text-muted">
          Zielwert
          <input type="number" value={target} onChange={(e) => setTarget(Number(e.target.value) || 0)}
            className="w-16 rounded border border-line bg-surface px-1 py-0.5 text-ink" />
          <span>{series.unit}</span>
        </label>
      </div>

      <Historyimage points={points} unit={series.unit} trend={t} target={history?.target ?? 0} />

      {t?.days_left != null && (
        <p className="text-xs text-muted">
          Die gestrichelte Linie ist die Fortschreibung dieser Punkte:{" "}
          <b className="text-ink">{t.per_day} {series.unit}/Tag</b>, erreicht{" "}
          <b className="text-ink">{history?.target} {series.unit}</b> am{" "}
          <b className="text-ink">{t.empty_at}</b> — in {t.days_left} Tagen. Güte {t.fit}
          {(t.fit ?? 1) < 0.8 && " (die Punkte streuen — die Zahl ist grob)"}.
        </p>
      )}

      {err && <div className="text-xs text-red-300">{err}</div>}

      <div className="max-h-64 overflow-auto rounded border border-line">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card text-left text-[11px] uppercase text-muted">
            <tr>
              <th className="px-2 py-1">{tr("metric_series_panel.time")}</th>
              <th className="px-2 py-1">{tr("metric_series_panel.value")}</th>
              <th className="px-2 py-1">{tr("metric_series_panel.origin")}</th>
              <th className="px-2 py-1" />
            </tr>
          </thead>
          <tbody>
            {[...points].reverse().map((p) => (
              <tr key={p.id} className="border-t border-line/60">
                <td className="whitespace-nowrap px-2 py-1 text-muted">
                  {formatDateTime(new Date(p.ts).toISOString())}
                </td>
                <td className="px-2 py-1 text-ink">{p.value} {series.unit}</td>
                <td className="px-2 py-1 text-[11px] text-muted">
                  {p.context?.source
                    || (p.context?.instance ? `Lauf #${p.context.instance}` : "—")}
                </td>
                <td className="px-2 py-1 text-right">
                  <div className="flex justify-end">
                    <IconButton icon={ICON.remove} danger
                      title={tr("metric_series_panel.remove_this_value_an_outlier_that_bends_the_l")}
                      onClick={() => discard.mutate(p.id)} />
                  </div>
                </td>
              </tr>
            ))}
            {!points.length && !isFetching && (
              <tr><td colSpan={4} className="px-2 py-2 text-muted">
                {tr("metric_series_panel.no_value_in_this_period")}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-[11px] text-muted">{tr("metric_series_panel.count_values_period", { count: points.length })}</span>
        <div className="flex-1" />
        <button onClick={remove}
          className={BUTTON_SMALL.danger}>
          {tr("metric_series_panel.delete_whole_series")}
        </button>
      </div>
    </div>
  );
}

/**
 * The history as a line, plus the forecast dashed.
 *
 * Without a library: it is a polyline, a dashed segment and a few labels. The forecast
 * deliberately gets the same area as the measurement, so that one sees how far it goes
 * beyond what was measured.
 */
function Historyimage({ points: points, unit: unit, trend, target: target }: {
  points: Point[]; unit: string; trend?: Trend | null; target: number;
}) {
  if (points.length < 2) {
    return <div className="text-[11px] text-muted">{tr("metric_series_panel.no_line_yet_that_needs_two_values")}</div>;
  }
  const B = 900, H = 260, li = 46, re = 16, ob = 14, un = 26;

  const xs = points.map((p) => new Date(p.ts).getTime());
  const ys = points.map((p) => p.value);
  const ende = trend?.empty_at ? new Date(trend.empty_at + "T12:00:00").getTime() : null;

  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs, ende ?? 0);
  const y0 = Math.min(...ys, target);
  const y1 = Math.max(...ys, target);
  const span = Math.max(1e-9, y1 - y0);

  const px = (x: number) => li + ((x - x0) / Math.max(1, x1 - x0)) * (B - li - re);
  const py = (y: number) => H - un - ((y - y0) / span) * (H - ob - un);
  const line = points.map((p) => `${px(new Date(p.ts).getTime())},${py(p.value)}`).join(" ");

  const last = points[points.length - 1];
  const forecast = ende && trend?.days_left != null
    ? { x1: px(new Date(last.ts).getTime()), y1: py(last.value),
        x2: px(ende), y2: py(target) }
    : null;

  const brands = [y0, (y0 + y1) / 2, y1];
  const tag = (ms: number) => new Date(ms).toLocaleDateString(undefined,
    { day: "2-digit", month: "2-digit" });

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${B} ${H}`} className="h-64 w-full min-w-[420px]">
        {brands.map((y, i) => (
          <g key={i}>
            <line x1={li} y1={py(y)} x2={B - re} y2={py(y)}
                  className="stroke-line" strokeWidth="1" strokeDasharray="2 4" />
            <text x={li - 6} y={py(y) + 3} textAnchor="end"
                  className="fill-current text-[11px] text-muted">
              {Math.round(y * 100) / 100}
            </text>
          </g>
        ))}

        {forecast && (
          <>
            <line x1={forecast.x1} y1={forecast.y1} x2={forecast.x2} y2={forecast.y2}
                  className="stroke-amber-400" strokeWidth="2" strokeDasharray="6 5"
                  opacity="0.85" />
            <circle cx={forecast.x2} cy={forecast.y2} r="4" className="fill-amber-400" />
            <text x={forecast.x2} y={forecast.y2 - 8} textAnchor="end"
                  className="fill-current text-[11px] text-amber-300">
              {target} {unit} am {trend?.empty_at}
            </text>
          </>
        )}

        <polyline points={line} fill="none" className="stroke-brand" strokeWidth="2" />
        {points.map((p) => (
          <circle key={p.id} cx={px(new Date(p.ts).getTime())} cy={py(p.value)} r="3"
                  className="fill-brand">
            <title>{formatDateTime(new Date(p.ts).toISOString())} — {p.value} {unit}</title>
          </circle>
        ))}

        <text x={li} y={H - 8} className="fill-current text-[11px] text-muted">{tag(x0)}</text>
        <text x={B - re} y={H - 8} textAnchor="end"
              className="fill-current text-[11px] text-muted">{tag(x1)}</text>
        <text x={li} y={ob + 2} className="fill-current text-[11px] text-muted">{unit}</text>
      </svg>
    </div>
  );
}
