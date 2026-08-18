import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api";

interface Trend {
  punkte: number; wert: number | null; einheit: string;
  pro_tag: number | null; rest_tage: number | null; leer_am: string | null;
  guete: number | null; erster_wert: number | null; erster_am: string | null;
}
interface Reihe {
  id: number; key: string; name: string; unit: string; description: string;
  last_value: number | null; last_at: string | null; warned_at: string | null;
  trend: Trend | null;
}
interface Punkt { ts: string; wert: number }

/**
 * Messreihen — die Zahlen, die Abläufe mitschreiben.
 *
 * Ein Ablauf sah bisher nur den Augenblick: „Akku 25 %" wurde zu einer Nachricht und war
 * weg. Hier steht die Reihe, und damit die einzige Frage, die wirklich zählt — wohin läuft
 * das, und wann muss ich handeln? Die Linie ist bewusst mit abgebildet: eine Prognose, die
 * man nicht nachsehen kann, glaubt man zu Recht nicht.
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
        <b>Messreihen</b> — was Abläufe an Zahlen mitschreiben (Aktion <code>Messwert</code>).
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
              loeschen={() => { if (confirm(`Reihe '${r.key}' mit allen Werten löschen?`)) loeschen.mutate(r.key); }} />
          ))}
        </div>
      ) : (
        <div className="text-sm text-muted">
          Noch keine Messreihe. Sie entsteht beim ersten Wert — setz in einem Ablauf die
          Aktion <b>Messwert</b> ein.
        </div>
      )}
    </div>
  );
}

function ReihenZeile({ reihe, offen, umschalten, loeschen }: {
  reihe: Reihe; offen: boolean; umschalten: () => void; loeschen: () => void;
}) {
  const { data: verlauf } = useQuery({
    queryKey: ["metric-points", reihe.key],
    queryFn: () => api.get<{ punkte: Punkt[] }>(`/metrics/${encodeURIComponent(reihe.key)}/punkte?tage=90`),
    enabled: offen,
  });
  const t = reihe.trend;
  const knapp = t?.rest_tage != null && t.rest_tage <= 14;

  return (
    <div className={`rounded border p-3 ${knapp ? "border-amber-500/40 bg-amber-500/5" : "border-line bg-surface"}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <button onClick={umschalten} className="font-medium text-ink hover:text-brand">
          {offen ? "▾" : "▸"} {reihe.name}
        </button>
        <code className="font-mono text-[10px] text-muted">{reihe.key}</code>
        <div className="flex-1" />
        <span className="text-sm text-ink">
          {reihe.last_value ?? "—"} {reihe.unit}
        </span>
        <span className="text-[10px] text-muted">
          {reihe.last_at ? new Date(reihe.last_at).toLocaleString() : ""}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {t?.pro_tag != null && (
          <span>{t.pro_tag > 0 ? "+" : ""}{t.pro_tag} {reihe.unit}/Tag</span>
        )}
        {t?.rest_tage != null ? (
          <span className={knapp ? "text-amber-300" : ""}>
            noch {t.rest_tage} Tage — leer am {t.leer_am}
          </span>
        ) : (
          <span>{(t?.punkte ?? 0) < 3 ? "zu wenige Werte für eine Prognose" : "kein absehbares Ende"}</span>
        )}
        {t?.guete != null && <span>Güte {t.guete}</span>}
        <span>{t?.punkte ?? 0} Werte</span>
        {reihe.warned_at && (
          <span className="text-amber-300">
            gewarnt am {new Date(reihe.warned_at).toLocaleDateString()}
          </span>
        )}
      </div>

      {offen && (
        <div className="mt-2 space-y-2">
          <Verlaufsbild punkte={verlauf?.punkte || []} einheit={reihe.unit} />
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-muted">
              {(verlauf?.punkte || []).length} Werte der letzten 90 Tage
            </span>
            <div className="flex-1" />
            <button onClick={loeschen}
              className="rounded border border-line px-2 py-1 text-[10px] text-red-400 hover:bg-card">
              Reihe löschen
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Der Verlauf als Linie — ohne Bibliothek, das ist eine Polyline und zwei Achsenbeschriftungen. */
function Verlaufsbild({ punkte, einheit }: { punkte: Punkt[]; einheit: string }) {
  if (punkte.length < 2) {
    return <div className="text-[10px] text-muted">Noch keine Linie — dafür braucht es zwei Werte.</div>;
  }
  const B = 600, H = 140, rand = 24;
  const xs = punkte.map((p) => new Date(p.ts).getTime());
  const ys = punkte.map((p) => p.wert);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys, 0), Math.max(...ys)];
  const px = (x: number) => rand + ((x - x0) / Math.max(1, x1 - x0)) * (B - 2 * rand);
  const py = (y: number) => H - rand - ((y - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * rand);
  const linie = punkte.map((p) => `${px(new Date(p.ts).getTime())},${py(p.wert)}`).join(" ");

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${B} ${H}`} className="h-36 w-full min-w-[320px]">
        <line x1={rand} y1={H - rand} x2={B - rand} y2={H - rand}
              className="stroke-line" strokeWidth="1" />
        <polyline points={linie} fill="none" className="stroke-brand" strokeWidth="2" />
        {punkte.map((p, i) => (
          <circle key={i} cx={px(new Date(p.ts).getTime())} cy={py(p.wert)} r="2.5"
                  className="fill-brand" />
        ))}
        <text x={rand} y={12} className="fill-current text-[10px] text-muted">
          {ys[ys.length - 1]} {einheit}
        </text>
        <text x={rand} y={H - 6} className="fill-current text-[10px] text-muted">
          {new Date(x0).toLocaleDateString()}
        </text>
        <text x={B - rand} y={H - 6} textAnchor="end" className="fill-current text-[10px] text-muted">
          {new Date(x1).toLocaleDateString()}
        </text>
      </svg>
    </div>
  );
}
