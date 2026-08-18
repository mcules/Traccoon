import { useState } from "react";
import { tr } from "../../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api";

interface Trend {
  punkte: number; wert: number | null; einheit: string;
  pro_tag: number | null; rest_tage: number | null; leer_am: string | null;
  guete: number | null; alter_stunden: number | null; letzter_am: string | null;
  erster_wert: number | null; erster_am: string | null;
}
interface Reihe {
  id: number; key: string; name: string; unit: string; description: string;
  last_value: number | null; last_at: string | null; warned_at: string | null;
  trend: Trend | null;
}
interface Punkt { id: number; ts: string; wert: number; kontext: Record<string, any> }
interface Verlauf extends Reihe { ziel: number; punkte: Punkt[] }

const ZEITRAeUME: [number, string][] = [[7, "7 Tage"], [30, "30 Tage"], [90, "90 Tage"],
                                        [365, "1 Jahr"]];

/**
 * Messreihen — die Zahlen, die Abläufe mitschreiben.
 *
 * Ein Ablauf sieht immer nur den Augenblick. Erst der Verlauf beantwortet die Frage, die
 * man wirklich hat: wohin läuft das, und wann muss ich handeln? Deshalb steht die Prognose
 * hier nicht als Zahl allein, sondern als gestrichelte Verlängerung der Punkte — eine
 * Vorhersage, die man nicht nachsehen kann, glaubt man zu Recht nicht.
 */
export default function MessreihenPanel() {
  const qc = useQueryClient();
  const [offen, setOffen] = useState<string | null>(null);
  const [err, setErr] = useState("");

  const { data: reihen } = useQuery({
    queryKey: ["metrics"],
    queryFn: () => api.get<Reihe[]>("/metrics"),
    refetchInterval: 60_000,
  });

  const loeschen = useMutation({
    mutationFn: (key: string) => api.del(`/metrics/${encodeURIComponent(key)}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["metrics"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-sm text-muted">
        <b>{tr("messreihen_panel.messreihen")}</b> — was Abläufe an Zahlen mitschreiben (Aktion <code>{tr("messreihen_panel.messwert")}</code>).
        Aus dem Verlauf entsteht die Prognose: wieviel sich pro Tag ändert, wann der Zielwert
        erreicht ist, und ob rechtzeitig gewarnt wurde.
      </p>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      {reihen?.length ? (
        <div className="space-y-2">
          {reihen.map((r) => (
            <ReihenZeile key={r.key} reihe={r}
              offen={offen === r.key}
              umschalten={() => setOffen(offen === r.key ? null : r.key)}
              loeschen={() => { if (confirm(tr("messreihen.reihe_loeschen_frage", { key: r.key }))) loeschen.mutate(r.key); }} />
          ))}
        </div>
      ) : (
        <div className="text-sm text-muted">
          Noch keine Messreihe. Sie entsteht beim ersten Wert — setz in einem Ablauf die
          Aktion <b>{tr("messreihen_panel.messwert")}</b> ein.
        </div>
      )}
    </div>
  );
}

function ReihenZeile({ reihe, offen, umschalten, loeschen }: {
  reihe: Reihe; offen: boolean; umschalten: () => void; loeschen: () => void;
}) {
  const t = reihe.trend;
  const knapp = t?.rest_tage != null && t.rest_tage <= 14;
  const alt = (t?.alter_stunden ?? 0) > 26;

  return (
    <div className={`rounded border p-3 ${knapp ? "border-amber-500/40 bg-amber-500/5" : "border-line bg-surface"}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <button onClick={umschalten} className="font-medium text-ink hover:text-brand">
          {offen ? "▾" : "▸"} {reihe.name}
        </button>
        <code className="font-mono text-[10px] text-muted">{reihe.key}</code>
        <div className="flex-1" />
        <span className="text-sm text-ink">{reihe.last_value ?? "—"} {reihe.unit}</span>
        <span className={`text-[10px] ${alt ? "text-amber-300" : "text-muted"}`}>
          {reihe.last_at ? new Date(reihe.last_at).toLocaleString() : ""}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {t?.pro_tag != null && (
          <span>{t.pro_tag > 0 ? "+" : ""}{t.pro_tag} {reihe.unit}/Tag</span>
        )}
        {t?.rest_tage != null ? (
          <span className={knapp ? "text-amber-300" : ""}>
            {tr("messreihen.noch_tage", { tage: t.rest_tage, datum: t.leer_am ?? "" })}
          </span>
        ) : (
          <span>{tr((t?.punkte ?? 0) < 3 ? "messreihen.zu_wenige_werte" : "messreihen.kein_ende")}</span>
        )}
        {t?.guete != null && <span>{tr("messreihen.guete", { wert: t.guete })}</span>}
        <span>{t?.punkte ?? 0} Werte</span>
        {alt && t?.alter_stunden != null && (
          <span className="text-amber-300">
            {tr("messreihen.kein_neuer_wert", { stunden: Math.round(t.alter_stunden) })}
          </span>
        )}
        {reihe.warned_at && (
          <span className="text-amber-300">
            gewarnt am {new Date(reihe.warned_at).toLocaleDateString()}
          </span>
        )}
      </div>

      {offen && <Detail reihe={reihe} loeschen={loeschen} />}
    </div>
  );
}

/** Alles zu einer Reihe: Zeitraum wählen, Verlauf sehen, einzelne Werte wegnehmen. */
function Detail({ reihe, loeschen }: { reihe: Reihe; loeschen: () => void }) {
  const qc = useQueryClient();
  const [tage, setTage] = useState(30);
  const [ziel, setZiel] = useState(0);
  const [err, setErr] = useState("");
  const pfad = `/metrics/${encodeURIComponent(reihe.key)}/punkte`;

  const { data: verlauf, isFetching } = useQuery({
    queryKey: ["metric-points", reihe.key, tage, ziel],
    queryFn: () => api.get<Verlauf>(`${pfad}?tage=${tage}&ziel=${ziel}`),
  });

  const wegwerfen = useMutation({
    mutationFn: (pid: number) => api.del(`${pfad}/${pid}`),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["metric-points", reihe.key] });
      qc.invalidateQueries({ queryKey: ["metrics"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const punkte = verlauf?.punkte || [];
  const t = verlauf?.trend;

  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{tr("messreihen_panel.zeitraum")}</span>
        {ZEITRAeUME.map(([d, label]) => (
          <button key={d} onClick={() => setTage(d)}
            className={`rounded border px-2 py-0.5 ${
              tage === d ? "border-brand text-brand" : "border-line text-muted hover:text-ink"}`}>
            {label}
          </button>
        ))}
        <div className="flex-1" />
        <label className="flex items-center gap-1 text-muted">
          Zielwert
          <input type="number" value={ziel} onChange={(e) => setZiel(Number(e.target.value) || 0)}
            className="w-16 rounded border border-line bg-surface px-1 py-0.5 text-ink" />
          <span>{reihe.unit}</span>
        </label>
      </div>

      <Verlaufsbild punkte={punkte} einheit={reihe.unit} trend={t} ziel={verlauf?.ziel ?? 0} />

      {t?.rest_tage != null && (
        <p className="text-xs text-muted">
          Die gestrichelte Linie ist die Fortschreibung dieser Punkte:{" "}
          <b className="text-ink">{t.pro_tag} {reihe.unit}/Tag</b>, erreicht{" "}
          <b className="text-ink">{verlauf?.ziel} {reihe.unit}</b> am{" "}
          <b className="text-ink">{t.leer_am}</b> — in {t.rest_tage} Tagen. Güte {t.guete}
          {(t.guete ?? 1) < 0.8 && " (die Punkte streuen — die Zahl ist grob)"}.
        </p>
      )}

      {err && <div className="text-xs text-red-300">{err}</div>}

      <div className="max-h-64 overflow-auto rounded border border-line">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card text-left text-[10px] uppercase text-muted">
            <tr>
              <th className="px-2 py-1">{tr("messreihen_panel.zeitpunkt")}</th>
              <th className="px-2 py-1">{tr("messreihen_panel.wert")}</th>
              <th className="px-2 py-1">{tr("messreihen_panel.herkunft")}</th>
              <th className="px-2 py-1" />
            </tr>
          </thead>
          <tbody>
            {[...punkte].reverse().map((p) => (
              <tr key={p.id} className="border-t border-line/60">
                <td className="whitespace-nowrap px-2 py-1 text-muted">
                  {new Date(p.ts).toLocaleString()}
                </td>
                <td className="px-2 py-1 text-ink">{p.wert} {reihe.unit}</td>
                <td className="px-2 py-1 text-[10px] text-muted">
                  {p.kontext?.quelle
                    || (p.kontext?.instanz ? `Lauf #${p.kontext.instanz}` : "—")}
                </td>
                <td className="px-2 py-1 text-right">
                  <button
                    onClick={() => { if (confirm(`Wert ${p.wert} ${reihe.unit} entfernen?`)) wegwerfen.mutate(p.id); }}
                    title={tr("messreihen_panel.diesen_wert_entfernen_z_b_einen_ausreiss")}
                    className="text-red-400 hover:text-red-300">✕</button>
                </td>
              </tr>
            ))}
            {!punkte.length && !isFetching && (
              <tr><td colSpan={4} className="px-2 py-2 text-muted">
                In diesem Zeitraum liegt kein Wert.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-muted">{punkte.length} Werte im Zeitraum</span>
        <div className="flex-1" />
        <button onClick={loeschen}
          className="rounded border border-line px-2 py-1 text-[10px] text-red-400 hover:bg-card">
          Ganze Reihe löschen
        </button>
      </div>
    </div>
  );
}

/**
 * Der Verlauf als Linie, dazu die Prognose gestrichelt.
 *
 * Ohne Bibliothek: das ist eine Polyline, eine gestrichelte Strecke und ein paar
 * Beschriftungen. Die Prognose bekommt bewusst dieselbe Fläche wie die Messung — man soll
 * sehen, wie weit sie über das Gemessene hinausgeht.
 */
function Verlaufsbild({ punkte, einheit, trend, ziel }: {
  punkte: Punkt[]; einheit: string; trend?: Trend | null; ziel: number;
}) {
  if (punkte.length < 2) {
    return <div className="text-[10px] text-muted">{tr("messreihen_panel.noch_keine_linie_dafuer_braucht_es_zwei_")}</div>;
  }
  const B = 900, H = 260, li = 46, re = 16, ob = 14, un = 26;

  const xs = punkte.map((p) => new Date(p.ts).getTime());
  const ys = punkte.map((p) => p.wert);
  const ende = trend?.leer_am ? new Date(trend.leer_am + "T12:00:00").getTime() : null;

  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs, ende ?? 0);
  const y0 = Math.min(...ys, ziel);
  const y1 = Math.max(...ys, ziel);
  const spanne = Math.max(1e-9, y1 - y0);

  const px = (x: number) => li + ((x - x0) / Math.max(1, x1 - x0)) * (B - li - re);
  const py = (y: number) => H - un - ((y - y0) / spanne) * (H - ob - un);
  const linie = punkte.map((p) => `${px(new Date(p.ts).getTime())},${py(p.wert)}`).join(" ");

  const letzter = punkte[punkte.length - 1];
  const prognose = ende && trend?.rest_tage != null
    ? { x1: px(new Date(letzter.ts).getTime()), y1: py(letzter.wert),
        x2: px(ende), y2: py(ziel) }
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
                  className="fill-current text-[10px] text-muted">
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
                  className="fill-current text-[10px] text-amber-300">
              {ziel} {einheit} am {trend?.leer_am}
            </text>
          </>
        )}

        <polyline points={linie} fill="none" className="stroke-brand" strokeWidth="2" />
        {punkte.map((p) => (
          <circle key={p.id} cx={px(new Date(p.ts).getTime())} cy={py(p.wert)} r="3"
                  className="fill-brand">
            <title>{new Date(p.ts).toLocaleString()} — {p.wert} {einheit}</title>
          </circle>
        ))}

        <text x={li} y={H - 8} className="fill-current text-[10px] text-muted">{tag(x0)}</text>
        <text x={B - re} y={H - 8} textAnchor="end"
              className="fill-current text-[10px] text-muted">{tag(x1)}</text>
        <text x={li} y={ob + 2} className="fill-current text-[10px] text-muted">{einheit}</text>
      </svg>
    </div>
  );
}
