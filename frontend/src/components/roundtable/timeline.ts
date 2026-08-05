// Schicht 0 — die Zeitleiste als Zahlen.
//
// Ein Balken je Sekunde, vier Serien. Was die Komponente daraus macht, steht ausdrücklich
// **nicht** hier, gehört aber zum Verständnis der Serien:
//
//   · **Höhe = Menge.** Wie viel in dieser Sekunde überhaupt passiert ist (Wurzelskalierung
//     gegen den Spitzenwert, damit eine einzelne Sekunde mit 400 Ereignissen nicht alle
//     anderen platt drückt).
//   · **Farbe = Zusammensetzung.** Woraus die Sekunde bestand. Eine rot dominierte Sekunde ist
//     eine Sekunde voller Fehlschläge — und das ist die Stelle, auf die man klickt.
//
// Beides sind Darstellungsentscheidungen und leben in der React-Schicht. Hier stehen nur die
// Zähler; sonst gäbe es zwei Orte, an denen über dieselben Daten entschieden wird.
//
// `toLocale*` ist in Schicht 0 verboten (PIXEL-CONTRACT.md 3.1): es hängt an Sprache und
// Zeitzone des Browsers, dasselbe Log ergäbe in zwei Tabs zwei verschiedene Zeitleisten.
// `labelOf` liefert deshalb **Zahlen**, keinen Text.

import type { Bucket, LogEntry } from "./types.ts";
import { TIMELINE_BUCKET_MS, TIMELINE_CAP } from "./const.ts";

/** Fasst das Log zu Sekundenbalken zusammen.
 *
 *  Die Balken sind **lückenlos**: auch eine Sekunde ohne jedes Ereignis bekommt ihren leeren
 *  Balken. Nur so ist die Waagerechte eine echte Zeitachse, auf der ein Klick auf halber
 *  Strecke auch die halbe Zeit meint. Ließe man leere Sekunden weg, wäre eine zwanzigminütige
 *  Pause optisch so breit wie eine einzelne Sekunde.
 *
 *  `t` ist die **Wanduhr** in ms (auf die Sekunde abgerundet), nicht `engine.t`: es ist genau
 *  der Wert, den `Replay.seek` entgegennimmt. Ein Umrechnen in Simulationszeit müsste den
 *  gesamten Replay durchrechnen, nur um einen Balken zu beschriften.
 *
 *  Über `TIMELINE_CAP` hinaus werden die **ältesten** Balken verworfen — dieselbe Richtung, in
 *  die auch `REPLAY_CAP` kappt. Die Ansicht ist ein Kurzzeitgedächtnis; das Jüngste ist das,
 *  wofür man sie öffnet. */
export function bucketize(log: readonly LogEntry[], from?: number, to?: number): Bucket[] {
  if (log.length === 0) return [];

  let lo = from;
  let hi = to;
  if (lo === undefined || hi === undefined) {
    // Minimum und Maximum, nicht erster und letzter Eintrag: das Log steht in `seq`-Reihenfolge,
    // und die ist unter `WORKER_CONCURRENCY > 1` nicht chronologisch.
    let a = log[0].ts;
    let b = a;
    for (const e of log) {
      if (e.ts < a) a = e.ts;
      if (e.ts > b) b = e.ts;
    }
    if (lo === undefined) lo = a;
    if (hi === undefined) hi = b;
  }
  if (hi < lo) hi = lo;

  const first = floorBucket(lo);
  const last = floorBucket(hi);
  let count = (last - first) / TIMELINE_BUCKET_MS + 1;
  let start = first;
  if (count > TIMELINE_CAP) {
    start = last - (TIMELINE_CAP - 1) * TIMELINE_BUCKET_MS;
    count = TIMELINE_CAP;
  }

  const out: Bucket[] = new Array(count);
  for (let i = 0; i < count; i++) {
    out[i] = { t: start + i * TIMELINE_BUCKET_MS, says: 0, tools: 0, thinks: 0, errors: 0 };
  }

  for (const e of log) {
    const idx = (floorBucket(e.ts) - start) / TIMELINE_BUCKET_MS;
    if (idx < 0 || idx >= count) continue;
    const b = out[idx];
    for (const c of e.cmds) {
      switch (c.k) {
        case "say":
        case "deliver":
          // Eine Übergabe ist eine Äußerung mit Ziel — für die Zeitleiste dasselbe.
          b.says++;
          break;
        case "tool":
          b.tools++;
          break;
        case "think":
          b.thinks++;
          break;
        case "toolEnd":
          // Dreiwertig: `null` heißt *unbekannt* und zählt zu gar nichts. Wer `ok ?? true`
          // oder `!ok` schreibt, malt Farbe auf geratene Daten.
          if (c.ok === false) b.errors++;
          break;
        case "done":
          if (!c.ok) b.errors++;
          break;
        case "status":
          if (c.status === "failed" || c.status === "loop_exhausted") b.errors++;
          break;
        default:
          break;
      }
    }
  }
  return out;
}

/** Die Zahlen hinter einem Balken, unformatiert.
 *
 *  `h`/`m`/`s` sind **UTC**. Die Zeitzone kennt nur Schicht 2 — sie aufzulösen bräuchte hier
 *  die Umgebung des Browsers und machte aus derselben Datenlage zwei verschiedene Zeitleisten. */
export function labelOf(b: Bucket): {
  h: number; m: number; s: number;
  tools: number; says: number; thinks: number; errors: number;
} {
  const secs = Math.floor(b.t / 1000);
  return {
    h: Math.floor(secs / 3600) % 24,
    m: Math.floor(secs / 60) % 60,
    s: ((secs % 60) + 60) % 60,
    tools: b.tools,
    says: b.says,
    thinks: b.thinks,
    errors: b.errors,
  };
}

function floorBucket(ts: number): number {
  return Math.floor(ts / TIMELINE_BUCKET_MS) * TIMELINE_BUCKET_MS;
}
