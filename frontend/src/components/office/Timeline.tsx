// Schicht 2 — die Zeitleiste. Das Herzstück der Bedienung: eine Spalte je Sekunde,
// ein Klick spult den Raum dorthin zurück.
//
// Zwei Aussagen stecken in einem Balken, und sie sind bewusst getrennt:
//
//   · **Höhe = Menge.** Wie viel in dieser Sekunde überhaupt geschah — wurzelskaliert gegen den
//     Spitzenwert des sichtbaren Fensters. Linear skaliert drückte eine einzelne Sekunde mit
//     200 Ereignissen alle anderen zu einer Linie zusammen; die Wurzel lässt den Ausreißer
//     Ausreißer sein, ohne den Rest platt zu machen. `max(6, …)` sorgt dafür, dass eine Sekunde
//     mit *irgendetwas* darin sichtbar bleibt — eine leere Sekunde bekommt dagegen die Höhe 0,
//     sonst wäre „nichts passiert" nicht von „fast nichts passiert" zu unterscheiden.
//   · **Farbe = Zusammensetzung.** Jede Reihe ist ein Kind mit `height: Anteil %`. Eine rot
//     dominierte Spalte ist damit eine Sekunde voller Fehlschläge — genau die Stelle, auf die
//     man klickt.
//
// Die Zahlen kommen aus `timeline.ts` (Schicht 0). Formatiert wird **hier**, weil `toLocale*`
// dort verboten ist und `labelOf` deshalb Zahlen liefert, keinen Text.
//
// Das Fenster **gleitet** (`slice(-spalten)`): es wird nichts gescrollt und kein Balken dünner.
// Wie viele Spalten hineinpassen, misst ein `ResizeObserver` — schmaler Reiter heißt weniger
// Sekunden, nicht dünnere Balken. Genau deshalb steht `TIMELINE_COLUMNS` nur als Obergrenze da.

import { useCallback, useMemo, useRef, useState } from "react";
import { REPLAY_CAP, TIMELINE_BUCKET_MS, TIMELINE_COLUMNS } from "./const.ts";
import { bucketize, labelOf } from "./timeline.ts";
import type { Bucket, LogEntry } from "./types.ts";

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

/** Was die Bedienelemente vom Log brauchen — **strukturell**, nicht als Klasse.
 *
 *  Sowohl der echte `Recorder` (Schicht 0) als auch die `RecorderApi` des Feeds erfüllen diese
 *  Form. Ein Verweis auf die Klasse selbst wäre enger als nötig und zwänge Welle L zu einer
 *  Typzusicherung; `bounds()` ist hier absichtlich weit gefasst (nullbar, `dropped` optional),
 *  weil die beiden Fassungen sich genau dort unterscheiden. */
export interface LogQuelle {
  entries(): readonly LogEntry[];
  bounds(): { t0: number; t1: number; dropped?: boolean } | null;
}

export interface TimelineProps {
  /** Das Log. Wird nur gelesen. */
  recorder: LogQuelle;
  /** Das einzige Neuberechnungssignal — steigt gedrosselt im Feed. */
  revision: number;
  /** Angesprungene Position in Epoch-ms, `null` = Gegenwart/Live. */
  seekTs: number | null;
  /** Bekommt `b.t` unverändert, also Epoch-ms — genau das, was `Replay.seek` erwartet. */
  onSeek: (ts: number) => void;
  className?: string;
}

// ── Die vier Reihen ─────────────────────────────────────────────────────────────────────────
//
// Vier Farben, mehr gibt es nicht. Rot ist dieselbe Farbe wie `failed` im Agenten-Monitor;
// Violett/Blau/Grau sind bewusst **keine** Statusfarben, denn eine Nachricht ist kein Erfolg
// und ein Werkzeugaufruf kein Warten.

type ReihenKey = "tools" | "says" | "thinks" | "errors";

interface Reihe {
  key: ReihenKey;
  css: string;
  ein: string;
  viele: string;
}

/** Reihenfolge der **Beschriftung** — „2 Werkzeugaufrufe, 1 Nachricht". */
const REIHEN: readonly Reihe[] = [
  { key: "tools", css: "bg-sky-400", ein: "Werkzeugaufruf", viele: "Werkzeugaufrufe" },
  { key: "says", css: "bg-violet-400", ein: "Nachricht", viele: "Nachrichten" },
  { key: "thinks", css: "bg-slate-400", ein: "Denkschritt", viele: "Denkschritte" },
  { key: "errors", css: "bg-red-400", ein: "Fehler", viele: "Fehler" },
];

/** Reihenfolge im **Stapel**, von oben nach unten. Fehler liegen obenauf: sie sind das, was man
 *  im Vorbeischauen erkennen können muss. */
const STAPEL: readonly ReihenKey[] = ["errors", "says", "tools", "thinks"];

// ── Geometrie ───────────────────────────────────────────────────────────────────────────────

/** Balkenbreite und Lücke in CSS-Pixeln. Fest — der Preis dafür ist, dass bei schmalem Fenster
 *  weniger Sekunden hineinpassen, und der ist billiger als unlesbar dünne Balken. */
const SPALTE_PX = 4;
const LUECKE_PX = 1;
/** Unter so vielen Spalten lohnt die Anzeige nicht mehr; dann wird eben gedrängt. */
const MIN_SPALTEN = 24;

// ── Beschriftung ────────────────────────────────────────────────────────────────────────────

function zwei(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Uhrzeit eines Balkens in der **Ortszeit des Browsers**.
 *
 *  `labelOf` rechnet in UTC — Schicht 0 kennt die Zeitzone nicht und darf sie nicht kennen,
 *  sonst zeigte dasselbe Log in zwei Tabs zwei verschiedene Zeitleisten. Aufgelöst wird sie
 *  genau hier, indem der Balkenzeitpunkt um den Ortsversatz **dieses** Zeitpunkts verschoben in
 *  `labelOf` geht (versatzweise je Balken, damit auch der Sprung zur Sommerzeit stimmt).
 *  Der Rest der Anwendung zeigt Zeiten ebenfalls in Ortszeit (`lib/formatTime.ts`) — eine
 *  Zeitleiste in UTC widerspräche jedem anderen Zeitstempel auf dem Bildschirm. */
function ortsZahlen(b: Bucket): ReturnType<typeof labelOf> {
  const versatz = new Date(b.t).getTimezoneOffset() * 60_000;
  return labelOf({ ...b, t: b.t - versatz });
}

/** „12:34:56 · 2 Werkzeugaufrufe, 1 Nachricht" — **nur** die Zahlen ≠ 0, ein Ereignis im
 *  Singular. Eine Beschriftung, die „0 Fehler" sagt, redet über etwas, das nicht passiert ist. */
export function balkenLabel(b: Bucket): string {
  const l = ortsZahlen(b);
  const uhr = `${zwei(l.h)}:${zwei(l.m)}:${zwei(l.s)}`;
  const teile: string[] = [];
  for (const r of REIHEN) {
    const n = l[r.key];
    if (n > 0) teile.push(`${n} ${n === 1 ? r.ein : r.viele}`);
  }
  return teile.length ? `${uhr} · ${teile.join(", ")}` : `${uhr} · keine Ereignisse`;
}

/** Nur die Uhrzeit, für die Randbeschriftung unter der Leiste. */
function uhrzeit(b: Bucket): string {
  const l = ortsZahlen(b);
  return `${zwei(l.h)}:${zwei(l.m)}:${zwei(l.s)}`;
}

// ── Die Komponente ──────────────────────────────────────────────────────────────────────────

export default function Timeline({ recorder, revision, seekTs, onSeek, className }: TimelineProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const beobachterRef = useRef<ResizeObserver | null>(null);
  const [breite, setBreite] = useState(0);
  /** Wandernder Tastaturfokus (roving tabindex): 220 Tabstopps wären keine Bedienung. */
  const [fokus, setFokus] = useState<number | null>(null);

  // Das Log neu zu Sekunden zusammenfassen, sobald sich etwas geändert hat. `revision` ist das
  // Signal; `recorder` selbst wechselt nur bei einem Sitzungswechsel die Identität.
  const alle = useMemo(() => bucketize(recorder.entries()), [recorder, revision]);
  const grenzen = recorder.bounds();
  const gekappt = grenzen?.dropped === true;

  /** Breitenmessung als **Rückruf-Ref**, nicht als Effekt.
   *
   *  Ein `useEffect` mit leerer Abhängigkeitsliste liefe genau einmal — und beim ersten Rendern
   *  gibt es die Leiste noch gar nicht, weil ein leeres Log stattdessen einen Hinweis zeigt.
   *  Der Effekt fände `null`, liefe nie wieder, und die Leiste rechnete für immer mit der
   *  Vollbreite. Der Rückruf greift dagegen genau dann, wenn das Element auftaucht. */
  const setzeBox = useCallback((el: HTMLDivElement | null) => {
    beobachterRef.current?.disconnect();
    beobachterRef.current = null;
    boxRef.current = el;
    if (!el) return;
    setBreite(el.clientWidth);
    const ro = new ResizeObserver((eintraege) => {
      for (const e of eintraege) setBreite(e.contentRect.width);
    });
    ro.observe(el);
    beobachterRef.current = ro;
  }, []);

  const passen = breite > 0
    ? Math.floor((breite + LUECKE_PX) / (SPALTE_PX + LUECKE_PX))
    : TIMELINE_COLUMNS;
  const spalten = Math.max(MIN_SPALTEN, Math.min(TIMELINE_COLUMNS, passen));
  const sichtbar = alle.length > spalten ? alle.slice(-spalten) : alle;
  const versteckt = alle.length - sichtbar.length;

  // Spitzenwert des **sichtbaren** Fensters: die Leiste beantwortet „wie voll war diese Sekunde
  // im Vergleich zu dem, was ich gerade sehe" — ein Ausreißer von vor drei Stunden, der längst
  // aus dem Fenster gerutscht ist, darf das Bild nicht mehr bestimmen.
  let peak = 0;
  for (const b of sichtbar) {
    const t = b.tools + b.says + b.thinks + b.errors;
    if (t > peak) peak = t;
  }

  const zielT = seekTs === null ? null : Math.floor(seekTs / TIMELINE_BUCKET_MS) * TIMELINE_BUCKET_MS;
  const aktuellIdx = zielT === null ? -1 : sichtbar.findIndex((b) => b.t === zielT);
  const tabIdx = fokus !== null && fokus < sichtbar.length
    ? fokus
    : (aktuellIdx >= 0 ? aktuellIdx : sichtbar.length - 1);

  /** Fokus verschieben — **ohne** zu springen. Jeder Sprung baut die Engine neu auf und spielt
   *  das Log von vorn ab; bei gedrückter Pfeiltaste wären das zweihundert Neuaufbauten in einer
   *  Sekunde. Ausgelöst wird deshalb erst mit Eingabe-/Leertaste, also mit dem Knopf selbst. */
  const bewege = (zu: number) => {
    if (sichtbar.length === 0) return;
    const i = Math.max(0, Math.min(sichtbar.length - 1, zu));
    setFokus(i);
    boxRef.current?.querySelector<HTMLElement>(`[data-spalte="${i}"]`)?.focus();
  };

  const taste = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const i = Number(e.currentTarget.dataset.spalte);
    if (e.key === "ArrowLeft") { e.preventDefault(); bewege(i - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); bewege(i + 1); }
    else if (e.key === "Home") { e.preventDefault(); bewege(0); }
    else if (e.key === "End") { e.preventDefault(); bewege(sichtbar.length - 1); }
  };

  if (alle.length === 0) {
    return (
      <div className={`rounded border border-line bg-card px-3 py-2 text-xs text-muted ${className ?? ""}`}>
        Noch keine Ereignisse in dieser Sitzung.
      </div>
    );
  }

  const kappTitel = gekappt
    ? `Der Anfang der Sitzung ist nicht mehr im Speicher: das Büro hält höchstens `
      + `${REPLAY_CAP.toLocaleString("de-DE")} Ereignisse und verwirft die ältesten.`
      + (versteckt > 0 ? ` Zusätzlich liegen ${versteckt} Sekunden links außerhalb des Fensters.` : "")
    : `${versteckt} Sekunden liegen links außerhalb des Fensters.`;

  return (
    <div className={`rounded border border-line bg-card px-2 py-1.5 ${className ?? ""}`}>
      <div
        ref={setzeBox}
        role="group"
        aria-label="Zeitleiste — ein Balken je Sekunde"
        // `overflow-hidden`: verzählt sich die Messung um eine Spalte (Rundung, Bildlaufleiste),
        // soll die Leiste beschnitten werden und nicht das Layout dahinter aufreißen.
        className="flex h-16 items-end overflow-hidden"
        style={{ gap: `${LUECKE_PX}px` }}
      >
        {(gekappt || versteckt > 0) && (
          <div
            title={kappTitel}
            aria-label={kappTitel}
            className={`h-full shrink-0 rounded-sm ${gekappt ? "bg-line" : "bg-line/50"}`}
            style={{ width: `${SPALTE_PX}px` }}
          />
        )}
        {sichtbar.map((b, i) => {
          const gesamt = b.tools + b.says + b.thinks + b.errors;
          // Wurzelskalierung gegen den Spitzenwert. `gesamt === 0` ergibt ausdrücklich 0 und
          // nicht die Mindesthöhe — sonst behauptete jede leere Sekunde, es sei etwas gewesen.
          const h = gesamt === 0 || peak === 0
            ? 0
            : Math.max(6, Math.round((Math.sqrt(gesamt) / Math.sqrt(peak)) * 100));
          const label = balkenLabel(b);
          const ist = i === aktuellIdx;
          return (
            <button
              key={b.t}
              type="button"
              data-spalte={i}
              tabIndex={i === tabIdx ? 0 : -1}
              // Eine Aussage über die App („hier steht der Raum gerade"), nicht über den Fokus —
              // deshalb `aria-current` und nicht `aria-selected`.
              aria-current={ist ? "true" : undefined}
              aria-label={label}
              title={label}
              onClick={() => onSeek(b.t)}
              onKeyDown={taste}
              onFocus={() => setFokus(i)}
              className={"group flex h-full shrink-0 cursor-pointer flex-col justify-end rounded-sm "
                + "outline-none focus-visible:ring-1 focus-visible:ring-brand "
                + (ist ? "bg-brand/20" : "hover:bg-line/40")}
              style={{ width: `${SPALTE_PX}px` }}
            >
              {h > 0 ? (
                <span className="flex w-full flex-col overflow-hidden rounded-sm" style={{ height: `${h}%` }}>
                  {STAPEL.map((key) => {
                    const n = b[key];
                    if (n === 0) return null;
                    const r = REIHEN.find((x) => x.key === key)!;
                    return (
                      <span
                        key={key}
                        className={`w-full ${r.css}`}
                        style={{ height: `${(n / gesamt) * 100}%` }}
                      />
                    );
                  })}
                </span>
              ) : (
                // Leere Sekunde: ein Punkt auf der Grundlinie. Ohne ihn wäre die Zeitachse an
                // ruhigen Stellen unsichtbar und man wüsste nicht, wohin man klicken kann.
                <span className="w-full bg-line" style={{ height: "1px" }} />
              )}
            </button>
          );
        })}
      </div>
      <div className="mt-1 flex items-center justify-between text-[11px] text-muted">
        <span>{uhrzeit(sichtbar[0])}</span>
        <span>
          {gekappt && <span className="mr-2" title={kappTitel}>⚠ Anfang verworfen</span>}
          {sichtbar.length} {sichtbar.length === 1 ? "Sekunde" : "Sekunden"}
        </span>
        <span>{uhrzeit(sichtbar[sichtbar.length - 1])}</span>
      </div>
    </div>
  );
}
