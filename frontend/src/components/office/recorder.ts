// Schicht 0 — das Log. Alles, was der Raum je gesehen hat, in Ankunftsreihenfolge.
//
// Der Recorder ist die **einzige** Stelle, an der dedupliziert wird. Das ist keine Sparsamkeit,
// sondern der Grund, warum es ihn gibt: Traccoons Ereignisse kommen auf drei Wegen herein, und
// alle drei können sich überschneiden.
//
//   · Beim Verbinden puffert die WS-Brücke, holt danach den Verlauf per `GET …/events` und
//     verwirft die gepufferten mit `seq <= seq_to` — die Grenze ist knapp und darf es sein,
//     weil ein Doppler hier hängenbleibt.
//   · Ein Reconnect nach Netzwackler liefert Zeilen, die schon da waren.
//   · Ein manuelles Neuladen im Frontend zieht das Fenster noch einmal.
//
// Ein `Set<number>` über `seq` erschlägt alle drei an einer Stelle. Jede zweite Prüfung
// woanders wäre eine Stelle, an der sie später auseinanderlaufen.

import type { Ev, LogEntry, Roster } from "./types.ts";
import { REPLAY_CAP } from "./const.ts";
import { mapEvent } from "./mapEvent.ts";

export class Recorder {
  /** Ein-Zahl-Veraltungsprüfung. Bewegt sich bei **jedem** Zuwachs und bei **jedem** Verwurf;
   *  damit genügt ein Zahlenvergleich, um zu wissen, ob ein laufender `Replay` noch auf dem
   *  Log sitzt, das er beim Bauen gesehen hat. Eine Inhaltsprüfung wäre O(n) je Bild. */
  revision = 0;

  private log: LogEntry[] = [];
  /** Gesehene `seq`. Bleibt vollständig, auch wenn das Log vorn gekappt wird: eine Zeile, die
   *  wegen der Kappe verschwunden ist, darf nicht durch einen späten Doppler ans **Ende**
   *  zurückkehren — dort stünde sie zeitlich falsch und der Raum spielte sie ein zweites Mal. */
  private seen = new Set<number>();
  private roster: Roster = [];
  private droppedAny = false;

  /** Der Roster kommt aus derselben Antwort wie die Ereignisse und beantwortet, was in den
   *  Ereignissen fehlt (Rolle, Elternlauf, Modell). `mapEvent` braucht ihn; der Recorder
   *  reicht ihn nur durch. */
  setRoster(roster: Roster): void {
    this.roster = roster;
  }

  /** Nimmt ein Ereignis auf.
   *
   *  `false` heißt: das Log ist nach diesem Aufruf **nicht** einfach um diese eine Zeile
   *  gewachsen — entweder war die `seq` schon da (Doppler), oder die Kappe hat dafür vorn
   *  etwas verdrängt. Beides sind Gründe, einen laufenden Replay nicht blind fortzuschreiben;
   *  die verlässliche Antwort darauf ist aber `revision`, nicht dieser Rückgabewert. */
  push(ev: Ev): boolean {
    if (this.seen.has(ev.seq)) return false;
    this.seen.add(ev.seq);

    // Die Mapping-Tabelle steht in `mapEvent.ts` (Welle F) und nirgends sonst. Läge auch nur
    // ein Sonderfall hier, gäbe es zwei Orte, an denen ein Werkzeugname zu einem Bild wird.
    const cmds = mapEvent(ev, this.roster);
    this.log.push({ ts: parseTs(ev.ts), seq: ev.seq, cmds });
    this.revision++;

    let dropped = false;
    while (this.log.length > REPLAY_CAP) {
      this.log.shift();
      this.droppedAny = true;
      this.revision++;
      dropped = true;
    }
    return !dropped;
  }

  /** Gibt eine **Kopie** heraus.
   *
   *  Der Fehler, den das verhindert: ein lebender Cursor läuft über ein
   *  Log, dessen `shift()` unter ihm wegrutscht. Jeder Verwurf am Kopf verschiebt alle Indizes
   *  um eins, der Cursor überspringt die verschobenen Einträge — Bearbeitungen und ganze
   *  Agenten tauchen nie im Raum auf. Der Fehler sieht aus wie ein Engine-Fehler und
   *  ist ein Aliasing-Fehler; deshalb verlässt die eigene Sammlung dieses Objekt nie.
   *
   *  Die `LogEntry` selbst werden geteilt, nicht kopiert — sie sind nach dem Anlegen
   *  unveränderlich, und ein tiefes Kopieren von 20 000 Einträgen je Bild wäre die teuerste
   *  Zeile der ganzen Ansicht. */
  entries(): readonly LogEntry[] {
    return this.log.slice();
  }

  /** Grenzen des Logs.
   *
   *  `t0`/`t1` sind Minimum und Maximum der **Wanduhr**, nicht erster und letzter Eintrag: das
   *  Log steht in `seq`-Reihenfolge, und unter `WORKER_CONCURRENCY > 1` kann der zuletzt
   *  angekommene Zeitstempel älter sein als ein früherer. Für die Zeitleiste zählt die späteste
   *  Uhrzeit, nicht die späteste Zeile.
   *
   *  `seq0`/`seq1` dagegen sind die Grenzen der Ankunftsreihenfolge — die braucht das
   *  Reconnect-Protokoll (gepufferte Ereignisse mit `seq <= seq_to` verwerfen), und sie sind
   *  etwas anderes als die Zeitgrenzen. Beides aus einem Durchgang, weil beides denselben
   *  Durchlauf kostet. */
  bounds(): { t0: number; t1: number; seq0: number; seq1: number; dropped: boolean } {
    if (this.log.length === 0) {
      return { t0: 0, t1: 0, seq0: 0, seq1: 0, dropped: this.droppedAny };
    }
    let t0 = this.log[0].ts;
    let t1 = t0;
    let seq0 = this.log[0].seq;
    let seq1 = seq0;
    for (const e of this.log) {
      if (e.ts < t0) t0 = e.ts;
      if (e.ts > t1) t1 = e.ts;
      if (e.seq < seq0) seq0 = e.seq;
      if (e.seq > seq1) seq1 = e.seq;
    }
    return { t0, t1, seq0, seq1, dropped: this.droppedAny };
  }

  /** Beim Sitzungswechsel. `revision` wird **hochgezählt**, nicht auf 0 gesetzt — ein Replay,
   *  der zufällig `revision === 0` gemerkt hatte, hielte sich sonst für aktuell. */
  reset(): void {
    this.log = [];
    this.seen.clear();
    this.roster = [];
    this.droppedAny = false;
    this.revision++;
  }
}

/** ISO-8601 → ms. `Date.parse` ist erlaubt und nötig: es ist keine Uhr, sondern eine reine
 *  Funktion auf einer Zeichenkette (`new Date`/`Date.now` wären beides nicht). Ein unlesbarer
 *  Zeitstempel wird zu 0 statt zu `NaN` — `NaN` würde sich durch jede Zeitrechnung des Replays
 *  fressen und den Raum lautlos einfrieren. */
function parseTs(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : 0;
}
