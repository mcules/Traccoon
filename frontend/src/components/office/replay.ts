// Schicht 0 — Zurückspulen ohne Schnappschüsse.
//
// Der ganze Trick: die Engine kennt Zeit nur durch `tick(dt)`, und `dt` kommt aus den
// **Ereignis-Zeitstempeln**, nie aus einer Wanduhr. Eine Blase, die damals vier Sekunden alt
// war, ist beim Zurückspulen wieder vier Sekunden alt. Damit ist „springe auf t" schlicht
// „neue Engine, Log von vorn abspielen" — kein Zustand muss serialisiert, versioniert oder
// migriert werden, und es gibt keine zweite Wahrheit, die von der ersten abweichen könnte.
//
// Drei Dinge, die dabei leicht falsch gemacht werden:
//
//  1. **Das Log wird nie nach `ts` sortiert.** Es steht in Ankunftsreihenfolge (`seq`). Zwei
//     parallel laufende Worker erzeugen nicht-monotone Zeitstempel; ein Sortieren nach `ts`
//     setzte die Wirkung vor die Ursache — das Werkzeugergebnis vor den Werkzeugstart.
//  2. **Lücken werden beidseitig geklemmt**: `dt = min(MAX_GAP_MS, max(0, ts - prev))`.
//     Nach oben allein reicht nicht: bei mehreren Workern kann `ts`
//     gegenüber `seq` rückwärts laufen, und ein negatives `dt` drehte die Engine zurück.
//  3. **Gleiche Zeitstempel wirken zusammen.** Ein Werkzeugstart und der abgeleitete
//     `file_edit` derselben Zeile tragen dieselbe Uhrzeit; liefe die Uhr dazwischen weiter,
//     hinge das Bild daran, wie viele Begleiter das Backend gerade erzeugt hat.

import type { Frame, LogEntry, Room } from "./types.ts";
import { Engine } from "./engine.ts";
import { LIVE_STEP_MS, MAX_GAP_MS, REPLAY_STEP_MS } from "./const.ts";

export class Replay {
  private log: readonly LogEntry[];
  private room: Room | undefined;
  private eng: Engine;
  /** Index des nächsten noch nicht angewandten Eintrags. */
  private i = 0;
  /** Wanduhr-Position in ms. Läuft **monoton**: ein Eintrag mit älterem `ts` bewegt sie nicht
   *  zurück, er wirkt einfach ohne Zeitvorschub (siehe Klemmung oben). */
  private p = 0;
  /** Zeitstempel des zuletzt angewandten Eintrags — der Bezugspunkt der Lückenklemmung. */
  private anchor = 0;
  /** Simulationszeit, die seit `anchor` schon vergangen ist. */
  private spent = 0;
  private _from = 0;
  private _to = 0;

  constructor(log: readonly LogEntry[], room?: Room) {
    this.log = log;
    this.room = room;
    this.measure();
    this.eng = new Engine(room);
    this.p = this._from;
    this.anchor = this._from;
  }

  get position(): number { return this.p; }
  get from(): number { return this._from; }
  get to(): number { return this._to; }

  /** Springt auf einen Zeitpunkt. **Immer** von vorn, auch vorwärts.
   *
   *  Ein Vorwärts-Seek könnte theoretisch fortsetzen, aber dann hinge das Ergebnis davon ab,
   *  wo man vorher stand — und genau die Unabhängigkeit ist der Grund, warum es diesen Entwurf
   *  gibt: `seek(t)` liefert dasselbe Bild, egal wie man dorthin gekommen ist. */
  seek(ts: number): void {
    this.eng = new Engine(this.room);
    this.i = 0;
    this.p = this._from;
    this.anchor = this._from;
    this.spent = 0;
    this.run(ts, REPLAY_STEP_MS);
  }

  /** Läuft weiter — ohne Neuaufbau. Das ist der Livebetrieb: `dtMs` ist der Abstand zweier
   *  Bilder, den die rAF-Schleife in Schicht 2 misst und deckelt (`MAX_FRAME_MS`). */
  advance(dtMs: number): void {
    if (!(dtMs > 0)) return;
    this.run(this.p + dtMs, LIVE_STEP_MS);
  }

  /** Ans Ende des Logs, also zurück in die Gegenwart. Steht die Position schon dort, kostet
   *  das nichts — sonst wäre jeder Klick auf „live" ein voller Neuaufbau. */
  toLive(): void {
    if (this.p >= this._to) return;
    this.run(this._to, REPLAY_STEP_MS);
  }

  frame(): Frame {
    return this.eng.frame();
  }

  /** Nimmt ein gewachsenes Log entgegen, ohne den Raum neu aufzubauen.
   *
   *  Nötig für den Livebetrieb: der Recorder hängt Zeilen an und `entries()` gibt jedes Mal
   *  eine neue Kopie zurück. Ohne diesen Weg müsste Schicht 2 bei jedem Ereignis einen neuen
   *  `Replay` bauen und drei Stunden Log neu durchrechnen. Ist der bereits abgespielte Anfang
   *  nicht mehr derselbe (Kappung am Kopf, Sitzungswechsel), wird ehrlich neu aufgebaut. */
  extend(log: readonly LogEntry[]): void {
    const keep =
      log.length >= this.i &&
      (this.i === 0 || (log[this.i - 1] !== undefined && log[this.i - 1].seq === this.log[this.i - 1].seq));
    this.log = log;
    this.measure();
    if (keep) return;
    const at = this.p;
    this.seek(at);
  }

  // ── Der eine Integrator ────────────────────────────────────────────────────

  /** Spielt bis `untilTs` ab. `seek` und `advance` gehen **beide** hier hindurch, nur mit
   *  verschiedener Schrittweite — dass das dasselbe Ergebnis liefert, ist genau die
   *  dt-Split-Invarianz aus PIXEL-CONTRACT.md 3.4. Wären es zwei Integratoren, müsste man ihre
   *  Gleichheit prüfen; so ist sie gebaut. */
  private run(untilTs: number, stepCap: number): void {
    while (this.i < this.log.length) {
      const at = this.log[this.i].ts;
      if (at > untilTs) break;
      this.settle(at, stepCap);
      // Alle Kommandos desselben Zeitstempels zusammen, bevor die Uhr weiterläuft.
      // Verglichen wird gegen `at`, nicht gegen `this.p`: ein Nachzügler mit älterer Uhrzeit
      // gehört zu seinem eigenen Zeitstempel, nicht zum aktuellen Stand.
      while (this.i < this.log.length && this.log[this.i].ts === at) {
        for (const c of this.log[this.i].cmds) this.eng.apply(c);
        this.i++;
      }
      if (at > this.anchor) {
        this.anchor = at;
        this.spent = 0;
      }
    }
    this.settle(untilTs, stepCap);
  }

  /** Bringt die Simulation auf den Stand der Wanduhrzeit `ts`.
   *
   *  Der Kern: **wie viel Simulationszeit zwischen zwei Ereignissen vergeht, hängt nur an den
   *  beiden Zeitstempeln** — `min(MAX_GAP_MS, ts - anchor)` — und nicht daran, in wie vielen
   *  Stücken man dorthin gekommen ist. Der naheliegende Weg (jedes Stück einzeln klemmen)
   *  wäre genau der Fehler: eine Stille von 155 s in einem Zug ergäbe 20 s, in 100-ms-Schritten
   *  aber volle 155 s — `seek` und `advance` zeigten denselben Zeitpunkt verschieden.
   *
   *  Nebenwirkung, bewusst in Kauf genommen: nach `MAX_GAP_MS` ohne Ereignis steht der Raum
   *  still, auch live. Das ist unsichtbar — Blasen sind nach `BUBBLE_MS` weg, Wege nach
   *  wenigen Sekunden zu Ende, und danach sitzt ohnehin jeder. Die Kaffee-Uhr
   *  (`IDLE_COFFEE_MS`) läuft dagegen nur, solange *irgendwer* im Raum Ereignisse erzeugt —
   *  also genau dann, wenn ein Untätiger neben einem Beschäftigten auffällt. */
  private settle(ts: number, stepCap: number): void {
    const want = clampGap(ts - this.anchor);
    if (want > this.spent) {
      this.integrate(want - this.spent, stepCap);
      this.spent = want;
    }
    if (ts > this.p) this.p = ts;
  }

  /** Zerlegt eine Zeitspanne in Schritte von höchstens `stepCap`.
   *
   *  Der Schritt ist **variabel**: `run` ruft immer nur bis zum nächsten Kommando, also ist die
   *  Spanne hier schon `min(stepCap, Zeit bis zum nächsten Kommando)`. Das ist zusammen mit dem
   *  Idle-Skip in `Engine.tick` das Gegenmittel gegen die einzige Stelle, an der dieser Entwurf
   *  nicht von selbst skaliert: drei Stunden Log bei 50 ms wären 216 000 Ticks. Checkpoints
   *  gibt es bewusst **nicht** — sie kämen nur, falls die Messung in Welle M sie verlangt, und
   *  dann als aus dem Log neu gerechneter Cache, nie als übertragener oder gespeicherter Zustand. */
  private integrate(span: number, stepCap: number): void {
    let left = span;
    while (left > 0) {
      const step = left < stepCap ? left : stepCap;
      this.eng.tick(step);
      left -= step;
    }
  }

  /** `from`/`to` sind Minimum und Maximum der Zeitstempel, nicht erster und letzter Eintrag —
   *  die Reihenfolge ist `seq`, und die ist nicht chronologisch. */
  private measure(): void {
    if (this.log.length === 0) {
      this._from = 0;
      this._to = 0;
      return;
    }
    let lo = this.log[0].ts;
    let hi = lo;
    for (const e of this.log) {
      if (e.ts < lo) lo = e.ts;
      if (e.ts > hi) hi = e.ts;
    }
    this._from = lo;
    this._to = hi;
  }
}

/** Beidseitige Klemmung. Oben, weil sonst minutenlange Stille an einer einzigen `run`-Zeile
 *  den Zuschauer vor ein totes Bild setzt. Unten, weil `ts` gegenüber `seq` rückwärts laufen
 *  kann und ein negatives `dt` die Engine zurückdrehen würde. */
function clampGap(raw: number): number {
  if (!(raw > 0)) return 0;
  return raw > MAX_GAP_MS ? MAX_GAP_MS : raw;
}

/** Der kopflose Golden-Test-Einstieg: rein, ohne React, ohne Canvas, ohne Uhr. Dasselbe Log und
 *  derselbe Zeitpunkt ergeben denselben `Frame` — auf jedem Rechner, in jeder Zeitzone. Diese
 *  Reinheit ist der einzige Grund, warum der Renderer später überhaupt golden prüfbar ist;
 *  sie ist streng zu halten. */
export function frameAt(log: readonly LogEntry[], ts: number): Frame {
  const r = new Replay(log);
  r.seek(ts);
  return r.frame();
}
