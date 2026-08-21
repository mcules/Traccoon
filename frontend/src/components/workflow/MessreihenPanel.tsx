import { useState } from "react";
import { formatDate, formatDateTime } from "../../lib/formatTime";
import { tr } from "../../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api";
import {
  Actions, Area, Fehlerzeile, ICON, IconButton, Listing, ListingLeer, ListenLine,
  LoeschDialog, BUTTON_KLEIN, BUTTON_TEXT} from "../ui";

interface Trend {
  points: number; wert: number | null; einheit: string;
  pro_tag: number | null; rest_tage: number | null; leer_am: string | null;
  guete: number | null; alter_stunden: number | null; letzter_am: string | null;
  erster_wert: number | null; erster_am: string | null;
}
interface Series {
  id: number; key: string; name: string; unit: string; description: string;
  last_value: number | null; last_at: string | null; warned_at: string | null;
  trend: Trend | null;
}
interface Point { id: number; ts: string; value: number; context: Record<string, any> }
interface Verlauf extends Series { target: number; points: Point[] }

const ZEITRAeUME: [number, string][] = [[7, "7 Tage"], [30, "30 Tage"], [90, "90 Tage"],
                                        [365, "1 Jahr"]];

/**
 * Metric series: the numbers flows write along.
 *
 * A flow only ever sees the moment. Only the history answers the question one really has:
 * where is this heading, and when do I have to act? That is why the forecast does not stand
 * here as a number alone but as a dashed extension of the points; a forecast one cannot
 * check is rightly not believed.
 */
export default function MessreihenPanel() {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [loeschSeries, setLoeschSeries] = useState<any | null>(null);
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
    <Area hinweis={tr("messreihen_panel.einleitung")}>
      <Fehlerzeile text={err} />

      <Listing>
        {series?.map((r) => (
          <SeriesLine key={r.key} reihe={r}
            offen={open === r.key}
            umschalten={() => setOpen(open === r.key ? null : r.key)}
            loeschen={() => setLoeschSeries(r)} />
        ))}
        {!series?.length && <ListingLeer>{tr("messreihen_panel.keine_reihe")}</ListingLeer>}
      </Listing>
      {loeschSeries && (
        <LoeschDialog was={loeschSeries.key} laeuft={remove.isPending}
          onClose={() => setLoeschSeries(null)}
          onLoeschen={() => { remove.mutate(loeschSeries.key); setLoeschSeries(null); }} />
      )}
    </Area>
  );
}

function SeriesLine({ reihe: series, offen: open, umschalten, loeschen: remove }: {
  reihe: Series; offen: boolean; umschalten: () => void; loeschen: () => void;
}) {
  const t = series.trend;
  const knapp = t?.rest_tage != null && t.rest_tage <= 14;
  const alt = (t?.alter_stunden ?? 0) > 26;

  return (
    <ListenLine warnung={knapp}>
      <div className="flex flex-wrap items-baseline gap-2">
        <button onClick={umschalten} className={BUTTON_TEXT.neben}>
          {open ? "▾" : "▸"} {series.name}
        </button>
        <code className="font-mono text-[11px] text-muted">{series.key}</code>
        <div className="flex-1" />
        <span className="text-sm text-ink">{series.last_value ?? "—"} {series.unit}</span>
        <span className={`text-[11px] ${alt ? "text-amber-300" : "text-muted"}`}>
          {formatDateTime(series.last_at)}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {t?.pro_tag != null && (
          <span>{t.pro_tag > 0 ? "+" : ""}{t.pro_tag} {series.unit}/Tag</span>
        )}
        {t?.rest_tage != null ? (
          <span className={knapp ? "text-amber-300" : ""}>
            {tr("messreihen.noch_tage", { tage: t.rest_tage, datum: t.leer_am ?? "" })}
          </span>
        ) : (
          <span>{tr((t?.points ?? 0) < 3 ? "messreihen.zu_wenige_werte" : "messreihen.kein_ende")}</span>
        )}
        {t?.guete != null && <span>{tr("messreihen.guete", { wert: t.guete })}</span>}
        <span>{t?.points ?? 0} Werte</span>
        {alt && t?.alter_stunden != null && (
          <span className="text-amber-300">
            {tr("messreihen.kein_neuer_wert", { stunden: Math.round(t.alter_stunden) })}
          </span>
        )}
        {series.warned_at && (
          <span className="text-amber-300">
            {tr("messreihen_panel.gewarnt_am", { datum: formatDate(series.warned_at) })}
          </span>
        )}
      </div>

      {open && <Detail reihe={series} loeschen={remove} />}
    </ListenLine>
  );
}

/** Everything about one series: choose the period, see the history, remove single values. */
function Detail({ reihe: series, loeschen: remove }: { reihe: Series; loeschen: () => void }) {
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const [target, setTarget] = useState(0);
  const [err, setErr] = useState("");
  const path = `/metrics/${encodeURIComponent(series.key)}/points`;

  const { data: verlauf, isFetching } = useQuery({
    queryKey: ["metric-points", series.key, days, target],
    queryFn: () => api.get<Verlauf>(`${path}?days=${days}&target=${target}`),
  });

  const wegwerfen = useMutation({
    mutationFn: (pid: number) => api.del(`${path}/${pid}`),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["metric-points", series.key] });
      qc.invalidateQueries({ queryKey: ["metrics"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const points = verlauf?.points || [];
  const t = verlauf?.trend;

  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{tr("messreihen_panel.zeitraum")}</span>
        {ZEITRAeUME.map(([d, label]) => (
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

      <Verlaufsbild punkte={points} einheit={series.unit} trend={t} ziel={verlauf?.target ?? 0} />

      {t?.rest_tage != null && (
        <p className="text-xs text-muted">
          Die gestrichelte Linie ist die Fortschreibung dieser Punkte:{" "}
          <b className="text-ink">{t.pro_tag} {series.unit}/Tag</b>, erreicht{" "}
          <b className="text-ink">{verlauf?.target} {series.unit}</b> am{" "}
          <b className="text-ink">{t.leer_am}</b> — in {t.rest_tage} Tagen. Güte {t.guete}
          {(t.guete ?? 1) < 0.8 && " (die Punkte streuen — die Zahl ist grob)"}.
        </p>
      )}

      {err && <div className="text-xs text-red-300">{err}</div>}

      <div className="max-h-64 overflow-auto rounded border border-line">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card text-left text-[11px] uppercase text-muted">
            <tr>
              <th className="px-2 py-1">{tr("messreihen_panel.zeitpunkt")}</th>
              <th className="px-2 py-1">{tr("messreihen_panel.wert")}</th>
              <th className="px-2 py-1">{tr("messreihen_panel.herkunft")}</th>
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
                  {p.context?.quelle
                    || (p.context?.instanz ? `Lauf #${p.context.instanz}` : "—")}
                </td>
                <td className="px-2 py-1 text-right">
                  <div className="flex justify-end">
                    <IconButton icon={ICON.loeschen} gefahr
                      titel={tr("messreihen_panel.diesen_wert_entfernen_z_b_einen_ausreiss")}
                      onClick={() => wegwerfen.mutate(p.id)} />
                  </div>
                </td>
              </tr>
            ))}
            {!points.length && !isFetching && (
              <tr><td colSpan={4} className="px-2 py-2 text-muted">
                {tr("messreihen_panel.kein_wert_im_zeitraum")}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-[11px] text-muted">{tr("messreihen_panel.werte_im_zeitraum", { anzahl: points.length })}</span>
        <div className="flex-1" />
        <button onClick={remove}
          className={BUTTON_KLEIN.gefahr}>
          {tr("messreihen_panel.ganze_reihe_loeschen")}
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
function Verlaufsbild({ punkte: points, einheit: unit, trend, ziel: target }: {
  punkte: Point[]; einheit: string; trend?: Trend | null; ziel: number;
}) {
  if (points.length < 2) {
    return <div className="text-[11px] text-muted">{tr("messreihen_panel.noch_keine_linie_dafuer_braucht_es_zwei_")}</div>;
  }
  const B = 900, H = 260, li = 46, re = 16, ob = 14, un = 26;

  const xs = points.map((p) => new Date(p.ts).getTime());
  const ys = points.map((p) => p.value);
  const ende = trend?.leer_am ? new Date(trend.leer_am + "T12:00:00").getTime() : null;

  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs, ende ?? 0);
  const y0 = Math.min(...ys, target);
  const y1 = Math.max(...ys, target);
  const span = Math.max(1e-9, y1 - y0);

  const px = (x: number) => li + ((x - x0) / Math.max(1, x1 - x0)) * (B - li - re);
  const py = (y: number) => H - un - ((y - y0) / span) * (H - ob - un);
  const linie = points.map((p) => `${px(new Date(p.ts).getTime())},${py(p.value)}`).join(" ");

  const last = points[points.length - 1];
  const prognose = ende && trend?.rest_tage != null
    ? { x1: px(new Date(last.ts).getTime()), y1: py(last.value),
        x2: px(ende), y2: py(target) }
    : null;

  const marken = [y0, (y0 + y1) / 2, y1];
  const tag = (ms: number) => new Date(ms).toLocaleDateString(undefined,
    { day: "2-digit", month: "2-digit" });

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${B} ${H}`} className="h-64 w-full min-w-[420px]">
        {marken.map((y, i) => (
          <g key={i}>
            <line x1={li} y1={py(y)} x2={B - re} y2={py(y)}
                  className="stroke-line" strokeWidth="1" strokeDasharray="2 4" />
            <text x={li - 6} y={py(y) + 3} textAnchor="end"
                  className="fill-current text-[11px] text-muted">
              {Math.round(y * 100) / 100}
            </text>
          </g>
        ))}

        {prognose && (
          <>
            <line x1={prognose.x1} y1={prognose.y1} x2={prognose.x2} y2={prognose.y2}
                  className="stroke-amber-400" strokeWidth="2" strokeDasharray="6 5"
                  opacity="0.85" />
            <circle cx={prognose.x2} cy={prognose.y2} r="4" className="fill-amber-400" />
            <text x={prognose.x2} y={prognose.y2 - 8} textAnchor="end"
                  className="fill-current text-[11px] text-amber-300">
              {target} {unit} am {trend?.leer_am}
            </text>
          </>
        )}

        <polyline points={linie} fill="none" className="stroke-brand" strokeWidth="2" />
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
