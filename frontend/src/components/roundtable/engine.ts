// Schicht 0 — der Raum als Zustandsmaschine.
//
// Die Engine ist der einzige Ort, an dem aus Kommandos ein Bild wird. Sie hat genau zwei
// Eingänge: `apply(cmd)` (ein Ereignis ist passiert, augenblicklich) und `tick(dt)` (Zeit ist
// vergangen). Eine Uhr sieht sie nie — weder live noch beim Zurückspulen. Deshalb ergibt
// dasselbe Log denselben Raum, und zwar bitgleich.
//
// Die zwei Entwurfsentscheidungen, an denen alles hängt:
//
//  1. **Jede Bewegung ist eine geschlossene Funktion von `t`.** Ein Weg kennt seinen Start-,
//     Ankunfts-, Halte- und Rückkehrzeitpunkt; die Position ist eine Interpolation dazwischen.
//     Der naheliegende Weg — pro Tick ein Stück Strecke aufaddieren — verletzt die
//     dt-Split-Invarianz (PIXEL-CONTRACT.md 3.4) auf zwei Arten gleichzeitig: die
//     Fließkomma-Summe `8 × (v·25)` ist nicht bitgleich `v·200`, und das Ziel wird bei großem
//     `dt` in einem Sprung überschossen statt in acht Schritten erreicht. Mit Zeitstempeln
//     stellt sich die Frage gar nicht erst.
//
//  2. **Jeder Zustandsübergang trägt seinen exakten Zeitpunkt bei sich**, und der nächste
//     Übergang rechnet von *diesem* Zeitpunkt weiter, nicht von `this.t`. Endet ein Weg bei
//     t=1400 und `tick` steht gerade bei t=1600, beginnt der Folgeweg trotzdem bei 1400.
//     Genau hier scheitern solche Engines sonst: `tick(200)` würde einen Übergang um bis zu
//     200 ms nach hinten schieben, `tick(25)×8` nur um 25.
//
// Alles Übrige folgt daraus: keine Tick-Zähler, keine „alle n Bilder"-Schwellen, kein Wurf
// pro Bild — Variation kommt aus `rnd01(mix(seed, SALT))`.

import type {
  ActorState, Cmd, Frame, Fx, Pt, Room, RunStatus, Seat, Verdict,
} from "./types.ts";
import { hash32, mix, rnd01 } from "./ids.ts";
import { POD_SEATS, ROOM, SEAT_DX, seatOf } from "./room.ts";
import {
  ARRIVE_STAGGER_MS, BUBBLE_MS, COFFEE_HOLD_MS, DONE_LINGER_MS, DONE_LINGER_SPREAD_MS,
  GATE_PULSE_MS, HEARD_MS, HUDDLE_HOLD_MS, HUDDLE_MIN, HUDDLE_WINDOW_MS, IDLE_COFFEE_MS,
  LINK_MS, MAX_ACTORS, MAX_QUEUED_TRIPS, PACE_SPREAD, SETTLE_MS,
  SPEAK_HOLD_MS, SPEED_PX_PER_S, TOOL_BUSY_MS,
} from "./const.ts";

// ── Salze (PIXEL-CONTRACT.md 3.2) ────────────────────────────────────────────
//
// Jede Verwendungsstelle bekommt ihr eigenes, benanntes Salz. Zwei Stellen mit demselben Salz
// wären perfekt korreliert — dann gingen alle langsamen Läufer auch gleichzeitig durch die Tür.
// Diese Zahlen sind keine Stellschrauben, sondern Namen; deshalb stehen sie hier und nicht in
// `const.ts` (dort liegt, was man einstellt).

const SALT_PACE = 0x9e37_79b1;
const SALT_LINGER = 0x85eb_ca6b;

/** Standzeit des „wartet auf einen Menschen"-Zeichens über dem Kopf: drei Pulse.
 *  Abgeleitet statt eigenständig, damit es genau eine Stellschraube dafür gibt. */
const GATE_EMOTE_MS = 3 * GATE_PULSE_MS;

// ── Geometrie ────────────────────────────────────────────────────────────────
//
// Sie kommt vollständig aus `room.ts`; die Engine legt keinen einzigen Punkt selbst fest.
// `constructor(room?)` nimmt trotzdem einen Raum entgegen — der Prüfer stellt so eine
// Miniatur-Geometrie hin, ohne dass hier ein zweiter Grundriss gepflegt werden müsste.

/** Abstand, in dem man sich zum Übergeben neben jemanden stellt: eine Sitzbreite.
 *  Bewusst dieselbe Zahl wie der Sitzabstand — wer übergibt, stellt sich dahin, wo im Zweifel
 *  auch ein Stuhl stünde, und läuft der Figur nicht in den Rücken. */
const DELIVER_GAP = SEAT_DX;

// ── Wege ─────────────────────────────────────────────────────────────────────
//
// Ein `Trip` ist ein vollständig vorausberechneter Weg. Er kennt seine vier Zeitpunkte und
// leitet daraus jede Position ab — er zählt nichts hoch. Genau das macht ihn dt-split-invariant.

type TripKind = "arrive" | "deliver" | "coffee" | "huddle" | "exit";

interface Trip {
  kind: TripKind;
  /** Echte Arbeit drängelt sich vor Spaziergänge (Kaffee, Huddle). */
  work: boolean;
  /** `this.t` beim Einreihen — ein Weg beginnt nie vor seinem Auftrag. */
  queuedAt: number;
  from: Pt;
  to: Pt;
  /** Ziel des Rückwegs; bei `exit` ungenutzt. */
  home: Pt;
  t0: number;
  tArrive: number;
  holdUntil: number;
  /** Ende des Rückwegs; `=== holdUntil`, wenn `back === false`. */
  tBack: number;
  back: boolean;
  targetId?: string;
  text?: string;
  /** Die Wirkung der Ankunft (sprechen, verschwinden) wird genau einmal ausgelöst. */
  fired: boolean;
}

/** Was die Engine über eine Figur weiß, was die Zeichenschicht aber nichts angeht.
 *  Bewusst neben `ActorState` und nicht darin: `Frame` ist der Vertrag mit Schicht 1,
 *  und Wegewarteschlangen gehören nicht in einen Zeichenauftrag. */
interface Priv {
  /** Fließkomma-Position; `ActorState.x/.sub.x` sind nur ihre Ganz- und Bruchteile. */
  fx: number;
  fy: number;
  /** Tempofaktor aus dem Seed, `1 ± PACE_SPREAD`. */
  pace: number;
  trip: Trip | null;
  trips: Trip[];
  /** Exakter Zeitpunkt, zu dem der letzte Weg endete — Startzeit des nächsten. */
  freeAt: number;
  /** `t` der letzten echten Regung. Ab hier läuft die Kaffee-Uhr. */
  lastAct: number;
  /** `t` der letzten Äußerung — Fenster für die Huddle-Erkennung. */
  spokeAt: number;
  /** Geplanter Abgang durch die Tür (`0` = keiner). */
  exitAt: number;
}

// ── Hilfsfunktionen ──────────────────────────────────────────────────────────

/** Färbt ausschließlich den Blasenrand. `running` und alles Unbekannte bleibt farblos —
 *  ein Lauf, der noch läuft, hat kein Urteil. */
function verdictOf(s: RunStatus): Verdict {
  if (s === "success" || s === "planned") return "ok";
  if (s === "failed" || s === "loop_exhausted") return "err";
  if (s === "blocked") return "blocked";
  return null;
}

function dist(a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return Math.sqrt(dx * dx + dy * dy);
}

// ── Die Engine ───────────────────────────────────────────────────────────────

export class Engine {
  private room: Room;
  /** Einfügereihenfolge ist Iterationsreihenfolge — deshalb `Map`, nie ein Objekt
   *  (PIXEL-CONTRACT.md 3.5: `"12"` sortierte sich sonst vor `"run:8871"`). */
  private actors = new Map<string, ActorState>();
  private priv = new Map<string, Priv>();
  private fxs: Fx[] = [];
  private _t = 0;

  /** Belegte Pod-Plätze und der Chefplatz. Wird beim Abgang wieder freigegeben — sonst wäre
   *  eine lange Sitzung nach dreizehn Läufen ein Raum voller Geister am Fenster. */
  private podTaken = new Set<number>();
  private bossTaken = false;

  /** Zeitpunkt der zuletzt geplanten Ankunft — staffelt einen Rückstau (`ARRIVE_STAGGER_MS`). */
  private lastArriveAt = -ARRIVE_STAGGER_MS;
  /** Läuft gerade ein Huddle? Verhindert, dass jede weitere Äußerung ein neues auslöst. */
  private huddleUntil = 0;

  constructor(room?: Room) {
    this.room = room ?? ROOM;
  }

  /** Simulationszeit in ms. Jede Animationsphase leitet sich hieraus ab — nie aus einem
   *  Tick-Zähler, sonst hinge das Bild davon ab, wie oft getickt wurde. */
  get t(): number {
    return this._t;
  }

  // ── Eingang 1: Kommandos ───────────────────────────────────────────────────
  //
  // `apply` wirkt augenblicklich zum aktuellen `this.t`. Es liest keine Uhr und ruft nie
  // `tick` — sonst hinge die Wirkung eines Ereignisses davon ab, wann es eintraf, und der
  // Replay wäre nicht mehr derselbe Raum.

  apply(cmd: Cmd): void {
    switch (cmd.k) {
      case "ensureActor": {
        const a = this.ensureActor(cmd.id, cmd.parent);
        a.role = cmd.role;
        a.issue = cmd.issue;
        a.phase = cmd.phase;
        a.model = cmd.model;
        if (cmd.parent !== undefined) a.parent = cmd.parent;
        break;
      }
      case "think": {
        const a = this.wake(cmd.id);
        a.think = cmd.text;
        a.thinkAt = this._t;
        this.touch(a);
        break;
      }
      case "say": {
        const a = this.wake(cmd.id);
        this.speak(a, cmd.text);
        this.maybeHuddle();
        break;
      }
      case "tool": {
        const a = this.wake(cmd.id);
        a.act = cmd.act;
        a.tool = cmd.tool;
        a.target = cmd.target;
        // Eine Werkzeugzeile aus Altdaten kennt nur *einen* Zeitpunkt, kein Intervall
        // (`tool_start` und `tool_result` werden aus derselben Zeile synthetisiert,
        // `duration_ms` ist `null`). Eine konstante Beschäftigungsdauer ist ehrlich zu dem,
        // was wir wissen: sie behauptet keine Dauer, sie zeigt „hier passiert gerade etwas".
        // Liefert das Backend später ein echtes Intervall, löscht `toolEnd` einfach `busy` —
        // genau eine Zeile Änderung, und die Anzeige wird echt.
        a.busy = this._t + TOOL_BUSY_MS;
        this.emit("spark", a, this._t, TOOL_BUSY_MS);
        this.touch(a);
        break;
      }
      case "toolEnd": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.lastOk = cmd.ok;
        if (cmd.ok === false) a.fails++;
        else if (cmd.ok === true) a.resolved++;
        // `busy` bleibt absichtlich stehen — es läuft in `tick` aus (siehe oben).
        this.touch(a);
        break;
      }
      case "edit": {
        const a = this.wake(cmd.id);
        a.edit = cmd.path;
        a.edits++;
        this.emit("drop", a, this._t, LINK_MS);
        this.touch(a);
        break;
      }
      case "spawn": {
        const parent = this.actors.get(cmd.parent);
        const child = this.actors.get(cmd.id);
        if (parent) {
          parent.link = { to: cmd.id, until: this._t + LINK_MS };
          const to = child ? { x: child.x, y: child.y } : this.room.door;
          this.fxs.push({
            kind: "link", x: parent.x, y: parent.y, to: { x: to.x, y: to.y },
            t0: this._t, until: this._t + LINK_MS, seed: parent.seed,
          });
          this.touch(parent);
        }
        if (child && child.parent === undefined) child.parent = cmd.parent;
        break;
      }
      case "deliver": {
        const a = this.wake(cmd.id);
        const to = this.actors.get(cmd.to);
        if (!to || to.retired || to.id === a.id) {
          // Kein Ziel im Raum: dann wird es eben eine Ansage in den Raum hinein.
          this.speak(a, cmd.text ?? "");
          this.maybeHuddle();
          break;
        }
        const side = this.pos(a).x <= to.x ? -DELIVER_GAP : DELIVER_GAP;
        this.enqueue(a, {
          kind: "deliver", work: true, queuedAt: this._t,
          from: { x: 0, y: 0 }, to: { x: to.x + side, y: to.y }, home: { x: 0, y: 0 },
          t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true,
          targetId: to.id, text: cmd.text, fired: false,
        });
        this.touch(a);
        break;
      }
      case "gate": {
        const a = this.wake(cmd.id);
        a.waiting = true;
        a.gate = cmd.kind;
        // Wer auf einen Menschen wartet, läuft nicht mehr los. Ein bereits laufender Weg
        // darf zu Ende gehen — mitten im Raum stehenzubleiben sähe nach einem Absturz aus.
        a.say = cmd.text;
        a.sayAt = this._t;
        a.pose = a.pose === "walk" ? "walk" : "stand";
        this.emitAt("emote", a, this._t, GATE_EMOTE_MS, "!");
        this.touch(a);
        break;
      }
      case "resume": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.waiting = false;
        a.gate = undefined;
        this.touch(a);
        break;
      }
      case "status": {
        const a = this.actors.get(cmd.id);
        if (!a) break;
        a.status = cmd.status;
        a.verdict = verdictOf(cmd.status);
        break;
      }
      case "done": {
        const a = this.wake(cmd.id);
        a.done = this._t;
        a.doneOk = cmd.ok;
        a.verdict = cmd.ok ? "ok" : "err";
        if (cmd.text !== undefined && cmd.text !== "") this.speak(a, cmd.text);
        this.emitAt("emote", a, this._t, LINK_MS, cmd.ok ? "✓" : "✗");
        const p = this.privOf(a);
        // Gestreut, damit nicht zwölf Leute gleichzeitig zur Tür gehen.
        const spread = rnd01(mix(a.seed, SALT_LINGER)) * DONE_LINGER_SPREAD_MS;
        p.exitAt = this._t + DONE_LINGER_MS + spread;
        this.touch(a);
        break;
      }
    }
  }

  // ── Eingang 2: Zeit ────────────────────────────────────────────────────────

  /** Die eine Tür, durch die Zeit hereinkommt.
   *
   *  `dtMs` wird **nicht** geklemmt: `MAX_FRAME_MS` ist die Sache des Aufrufers (die rAF-Schleife
   *  in Schicht 2), denn ein Klemmen hier bräche genau die Invariante, für die es die Engine gibt —
   *  `tick(200)` wäre plötzlich `tick(100)`. */
  tick(dtMs: number): void {
    if (!(dtMs > 0)) return;
    this._t += dtMs;
    const t = this._t;
    for (const a of this.actors.values()) this.stepActor(a, this.privOf(a));
    // Effekte sterben an ihrem Zeitpunkt, nicht nach n Bildern — auch bei einem einzigen
    // großen Tick ist danach exakt dieselbe Menge übrig wie bei acht kleinen.
    if (this.fxs.length > 0) this.fxs = this.fxs.filter((f) => f.until > t);
  }

  // ── Ausgang ────────────────────────────────────────────────────────────────

  /** Das Einzige, was Schicht 1 zu sehen bekommt. Die Aktoren sind nach `y` sortiert
   *  (Maler-Algorithmus, hinten zuerst) und liegen in einer **eigenen** Liste — die Engine
   *  gibt ihre Sammlung nie heraus.
   *
   *  Abgegangene Figuren bleiben enthalten: das Dock zeigt sie weiter, die Bühne überspringt
   *  `retired`. Zwei Listen wären zwei Wahrheiten. */
  frame(): Frame {
    const actors = [...this.actors.values()];
    actors.sort((x, y) => x.y - y.y); // stabil (ES2019) → Gleichstand behält Einfügereihenfolge
    return { t: this._t, actors, fx: this.fxs.slice() };
  }

  // ── Aktoren ────────────────────────────────────────────────────────────────

  private privOf(a: ActorState): Priv {
    const p = this.priv.get(a.id);
    if (p) return p;
    const fresh: Priv = {
      fx: a.x, fy: a.y, pace: 1, trip: null, trips: [],
      freeAt: this._t, lastAct: this._t, spokeAt: -HUDDLE_WINDOW_MS, exitAt: 0,
    };
    this.priv.set(a.id, fresh);
    return fresh;
  }

  /** Legt die Figur an oder gibt die vorhandene zurück. Idempotent. */
  private ensureActor(id: string, parent?: string): ActorState {
    const found = this.actors.get(id);
    if (found) return found;
    if (this.actors.size >= MAX_ACTORS) this.evict();

    const seed = hash32(id);
    const a: ActorState = {
      id, role: "", issue: null, phase: null, model: null,
      x: 0, y: 0, sub: { x: 0, y: 0 },
      // Beginnt stehend, nicht laufend: bis der gestaffelte Einlass an der Reihe ist, wartet
      // die Figur vor der Tür — eine laufende Figur, die sich nicht bewegt, sähe kaputt aus.
      pose: "stand", flip: false, away: false,
      verdict: null, status: "running", deskIndex: -2,
      busy: 0, waiting: false, fails: 0, resolved: 0, edits: 0,
      seed,
    };
    if (parent !== undefined) a.parent = parent;
    this.actors.set(id, a);

    const p = this.privOf(a);
    p.pace = 1 + (rnd01(mix(seed, SALT_PACE)) - 0.5) * 2 * PACE_SPREAD;
    this.seat(a);
    this.enter(a, p);
    return a;
  }

  /** Weckt eine Figur, die es geben muss, weil gleich etwas mit ihr passiert. Ein `retired`
   *  Agent, der wieder redet, kommt durch dieselbe Tür zurück, durch die er gegangen ist —
   *  das kommt bei Fortsetzungsläufen nach einer Kontext-Kompaktierung wirklich vor. */
  private wake(id: string): ActorState {
    const a = this.ensureActor(id);
    if (a.retired) {
      a.retired = undefined;
      a.away = false;
      a.done = undefined;
      const p = this.privOf(a);
      p.trip = null;
      p.trips.length = 0;
      p.exitAt = 0;
      this.seat(a);
      this.enter(a, p);
    }
    return a;
  }

  /** Sitzvergabe: der Wurzellauf (kein `parent`) an den Chefplatz, alle anderen über
   *  `seatOf` — `hash32(id) % 12` mit linearer Sondierung, ohne Warteschlange. Ist alles
   *  belegt, wird die Figur Geist am Fenster (`deskIndex === -2`). Eine Warteschlange trüge
   *  Zustand über das Zurücksetzen hinweg und derselbe Agent säße beim zweiten Ansehen woanders. */
  private seat(a: ActorState): void {
    if (a.parent === undefined && !this.bossTaken) {
      this.bossTaken = true;
      a.deskIndex = -1;
      a.away = false;
      return;
    }
    const slot = seatOf(a.id, this.podTaken);
    if (slot >= 0) this.podTaken.add(slot);
    a.deskIndex = slot;
    a.away = slot === -2;
  }

  private unseat(a: ActorState): void {
    if (a.deskIndex === -1) this.bossTaken = false;
    else if (a.deskIndex >= 0) this.podTaken.delete(a.deskIndex);
    a.deskIndex = -2;
  }

  /** Der Sitz dieser Figur — aus **diesem** Raum, nicht aus `podSeat()`: der Konstruktor darf
   *  eine andere Geometrie bekommen haben, und zwei Quellen für denselben Punkt wären eine
   *  zu viel. */
  private seatOfActor(a: ActorState): Seat | undefined {
    if (a.deskIndex === -2) return undefined;
    return this.room.seats[a.deskIndex === -1 ? POD_SEATS : a.deskIndex];
  }

  /** Der Fußpunkt, an dem diese Figur zu Hause ist. */
  private home(a: ActorState): Pt {
    return this.seatOfActor(a)?.sit ?? this.room.away;
  }

  private homeFlip(a: ActorState): boolean {
    return this.seatOfActor(a)?.flip ?? false;
  }

  /** Ankunft durch die Tür. Kommen mehrere Läufe im selben Augenblick (Backfill eines
   *  Ereignisfensters ist der Normalfall), werden sie um `ARRIVE_STAGGER_MS` gestaffelt —
   *  sonst springen zwölf Leute gleichzeitig herein und man sieht nichts. */
  private enter(a: ActorState, p: Priv): void {
    const at = Math.max(this._t, this.lastArriveAt + ARRIVE_STAGGER_MS);
    this.lastArriveAt = at;
    const door = this.room.door;
    p.fx = door.x;
    p.fy = door.y;
    this.sync(a, p);
    p.freeAt = at;
    p.lastAct = at;
    p.trip = null;
    p.trips.length = 0;
    this.enqueue(a, {
      kind: "arrive", work: true, queuedAt: at,
      from: { x: door.x, y: door.y }, to: this.home(a), home: this.home(a),
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: false, fired: false,
    });
  }

  /** Platz schaffen, wenn der Raum voll ist: zuerst die, die schon draußen sind, dann die
   *  fertigen, zuletzt die älteste Figur überhaupt. Insertion-Order der `Map` ist hier
   *  Altersreihenfolge. */
  private evict(): void {
    let victim: ActorState | undefined;
    for (const a of this.actors.values()) if (a.retired) { victim = a; break; }
    if (!victim) for (const a of this.actors.values()) if (a.done !== undefined) { victim = a; break; }
    if (!victim) for (const a of this.actors.values()) { victim = a; break; }
    if (!victim) return;
    this.unseat(victim);
    this.actors.delete(victim.id);
    this.priv.delete(victim.id);
  }

  // ── Regungen ───────────────────────────────────────────────────────────────

  /** „Es ist etwas passiert" — setzt die Kaffee-Uhr zurück. */
  private touch(a: ActorState): void {
    this.privOf(a).lastAct = this._t;
  }

  private speak(a: ActorState, text: string, at?: number): void {
    const t = at ?? this._t;
    a.say = text;
    a.sayAt = t;
    a.think = undefined;
    a.thinkAt = undefined;
    const p = this.privOf(a);
    p.spokeAt = t;
    p.lastAct = t;
  }

  private pos(a: ActorState): Pt {
    const p = this.privOf(a);
    return { x: p.fx, y: p.fy };
  }

  /** Schreibt die Fließkomma-Position in den Aktor zurück: ganze Szenenpixel nach `x`/`y`,
   *  der Rest in den Subpixel-Akkumulator. Gerundet wird erst beim Zeichnen
   *  (PIXEL-CONTRACT.md 2.3) — wer hier rundete, verlöre bei kleinen `dt` jede Bewegung. */
  private sync(a: ActorState, p: Priv): void {
    const ix = Math.floor(p.fx);
    const iy = Math.floor(p.fy);
    a.x = ix;
    a.sub.x = p.fx - ix;
    a.y = iy;
    a.sub.y = p.fy - iy;
  }

  private emit(kind: "spark" | "drop", a: ActorState, t0: number, ms: number): void {
    this.fxs.push({ kind, x: a.x, y: a.y, t0, until: t0 + ms, seed: a.seed });
  }

  private emitAt(kind: "emote", a: ActorState, t0: number, ms: number, text: string): void {
    this.fxs.push({ kind, x: a.x, y: a.y, t0, until: t0 + ms, text, seed: a.seed });
  }

  // ── Huddle ─────────────────────────────────────────────────────────────────

  /** Reden `HUDDLE_MIN` Figuren innerhalb von `HUDDLE_WINDOW_MS`, treffen sie sich am runden
   *  Tisch. Die Prüfung sitzt in `apply`, nicht in `tick`: sie hängt damit am Zeitstempel des
   *  Ereignisses und nicht daran, wo eine Tick-Grenze zufällig lag. */
  private maybeHuddle(): void {
    if (this._t < this.huddleUntil) return;
    const since = this._t - HUDDLE_WINDOW_MS;
    const crowd: ActorState[] = [];
    for (const a of this.actors.values()) {
      if (a.retired || a.waiting || a.done !== undefined) continue;
      if (this.privOf(a).spokeAt >= since) crowd.push(a);
    }
    if (crowd.length < HUDDLE_MIN) return;
    const spots = this.room.huddle;
    let k = 0;
    for (const a of crowd) {
      const p = this.privOf(a);
      p.spokeAt = -HUDDLE_WINDOW_MS; // zählt nicht zweimal
      const spot = spots[k % spots.length];
      k++;
      if (!spot) continue;
      this.enqueue(a, {
        kind: "huddle", work: false, queuedAt: this._t,
        from: { x: 0, y: 0 }, to: { x: spot.x, y: spot.y }, home: { x: 0, y: 0 },
        t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true, fired: false,
      });
    }
    this.huddleUntil = this._t + HUDDLE_HOLD_MS;
  }

  // ── Wegewarteschlange ──────────────────────────────────────────────────────

  /** Reiht einen Weg ein. Echte Arbeit (`deliver`, `arrive`, `exit`) stellt sich **vor**
   *  Spaziergänge — ein Kaffeegang darf eine Übergabe nicht verzögern. Mehr als
   *  `MAX_QUEUED_TRIPS` Vormerkungen werden verworfen, sonst rennt eine Figur nach einem
   *  Ereignisschwall minutenlang Aufträge ab, die längst überholt sind. */
  private enqueue(a: ActorState, tr: Trip): void {
    const p = this.privOf(a);
    if (tr.work) {
      let i = p.trips.length;
      while (i > 0 && !p.trips[i - 1].work) i--;
      p.trips.splice(i, 0, tr);
    } else {
      p.trips.push(tr);
    }
    if (p.trips.length > MAX_QUEUED_TRIPS) p.trips.length = MAX_QUEUED_TRIPS;
  }

  /** Rechnet einen Weg vollständig aus. Ab hier ist er eine Funktion der Zeit. */
  private startTrip(a: ActorState, p: Priv, tr: Trip): void {
    const t0 = Math.max(p.freeAt, tr.queuedAt);
    const speed = SPEED_PX_PER_S * p.pace;
    tr.from = { x: p.fx, y: p.fy };
    tr.home = tr.kind === "exit" ? { x: p.fx, y: p.fy } : this.home(a);
    tr.t0 = t0;
    tr.tArrive = t0 + (dist(tr.from, tr.to) / speed) * 1000;
    const hold =
      tr.kind === "deliver" ? SPEAK_HOLD_MS :
      tr.kind === "coffee" ? COFFEE_HOLD_MS :
      tr.kind === "huddle" ? HUDDLE_HOLD_MS : 0;
    tr.holdUntil = tr.tArrive + hold;
    tr.tBack = tr.back ? tr.holdUntil + (dist(tr.to, tr.home) / speed) * 1000 : tr.holdUntil;
    tr.fired = false;
    p.trip = tr;
  }

  private makeCoffee(at: number): Trip {
    const c = this.room.coffee;
    return {
      kind: "coffee", work: false, queuedAt: at,
      from: { x: 0, y: 0 }, to: { x: c.x, y: c.y }, home: { x: 0, y: 0 },
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: true, fired: false,
    };
  }

  private makeExit(at: number): Trip {
    const d = this.room.door;
    return {
      kind: "exit", work: true, queuedAt: at,
      from: { x: 0, y: 0 }, to: { x: d.x, y: d.y }, home: { x: 0, y: 0 },
      t0: 0, tArrive: 0, holdUntil: 0, tBack: 0, back: false, fired: false,
    };
  }

  // ── Ein Aktor, ein Tick ────────────────────────────────────────────────────

  private stepActor(a: ActorState, p: Priv): void {
    const t = this._t;

    // Idle-Skip. Nicht Kosmetik: `Replay.seek` über drei Stunden Log sind zehntausende Ticks,
    // und in einem Büro sitzen die meisten Leute die meiste Zeit still. Wer nichts vorhat,
    // kostet hier eine Bedingung statt eines halben Dutzend Rechnungen.
    if (a.retired) return;
    if (
      !p.trip && p.trips.length === 0 && a.busy === 0 && !a.waiting &&
      a.sayAt === undefined && a.thinkAt === undefined &&
      a.link === undefined && a.heard === undefined &&
      p.exitAt === 0 && t < p.lastAct + IDLE_COFFEE_MS
    ) return;

    // Ablaufende Zustände. Alle Schwellen sind Zeitpunkte, keine Zähler (PIXEL-CONTRACT.md 3.4).
    if (a.busy !== 0 && t >= a.busy) {
      a.busy = 0;
      a.act = undefined;
      a.tool = undefined;
      a.target = undefined;
    }
    if (a.sayAt !== undefined && t >= a.sayAt + BUBBLE_MS) {
      a.say = undefined;
      a.sayAt = undefined;
    }
    if (a.thinkAt !== undefined && t >= a.thinkAt + BUBBLE_MS) {
      a.think = undefined;
      a.thinkAt = undefined;
    }
    if (a.heard !== undefined && t >= a.heard) a.heard = undefined;
    if (a.link !== undefined && t >= a.link.until) a.link = undefined;

    // Abgang: fällig zum gespeicherten Zeitpunkt, nicht zum Tick-Zeitpunkt.
    if (p.exitAt !== 0 && t >= p.exitAt) {
      const at = p.exitAt;
      p.exitAt = 0;
      p.trips.length = 0;
      p.freeAt = Math.max(p.freeAt, at);
      this.enqueue(a, this.makeExit(at));
    }

    // Kaffee: das einzige Lebenszeichen in einer langen Werkzeugkette.
    if (
      p.exitAt === 0 && !a.waiting && !p.trip && p.trips.length === 0 &&
      a.deskIndex !== -2 && !a.retired && t >= p.lastAct + IDLE_COFFEE_MS
    ) {
      const at = p.lastAct + IDLE_COFFEE_MS;
      p.lastAct = at; // der nächste Kaffee frühestens in einer weiteren Leerlaufperiode
      this.enqueue(a, this.makeCoffee(at));
    }

    this.stepTrips(a, p);
  }

  /** Arbeitet die Wege ab. Die Schleife ist nötig, weil ein einziger großer Tick mehrere
   *  Wege überspringen kann — und jeder davon beginnt exakt dann, wann der vorige endete,
   *  nicht erst bei `this.t`. Genau das macht `tick(200)` und `tick(25)×8` gleich. */
  private stepTrips(a: ActorState, p: Priv): void {
    const t = this._t;
    for (let guard = 0; guard < 16; guard++) {
      let tr = p.trip;
      if (!tr) {
        const next = p.trips.shift();
        if (!next) {
          // Zu Hause und untätig.
          if (!a.retired) {
            const h = this.home(a);
            p.fx = h.x;
            p.fy = h.y;
            this.sync(a, p);
            a.pose = a.deskIndex === -2 ? "stand" : "sit";
            a.flip = this.homeFlip(a);
          }
          return;
        }
        this.startTrip(a, p, next);
        tr = next;
      }

      // Hinweg. Vor `t0` steht die Figur noch am Ausgangspunkt — das ist der gestaffelte
      // Ankömmling, der vor der Tür wartet, bis er an der Reihe ist.
      if (t < tr.tArrive) {
        this.place(a, p, tr.from, tr.to, tr.t0, tr.tArrive);
        a.pose = t < tr.t0 ? "stand" : "walk";
        return;
      }
      if (!tr.fired) {
        // Erst exakt ans Ziel setzen, dann die Wirkung auslösen. Sonst läse `onArrive` die
        // Position aus dem *vorigen* Tick — und die liegt bei `tick(200)` woanders als bei
        // `tick(25)×8`. Genau daran hängen die Blickrichtung des Zuhörers und der Startpunkt
        // der Übergabelinie.
        p.fx = tr.to.x;
        p.fy = tr.to.y;
        this.sync(a, p);
        tr.fired = true;
        this.onArrive(a, p, tr);
        if (tr.kind === "exit") { p.trip = null; p.freeAt = tr.tArrive; return; }
      }
      // Aufenthalt am Ziel.
      if (t < tr.holdUntil) {
        p.fx = tr.to.x;
        p.fy = tr.to.y;
        this.sync(a, p);
        // `SETTLE_MS`: nach der Ankunft wird noch einen Moment getrippelt, bevor die Pose
        // umschlägt — sonst friert die Figur mitten im Schritt ein.
        a.pose = t < tr.tArrive + SETTLE_MS ? "walk" : "stand";
        return;
      }
      // Rückweg.
      if (tr.back && t < tr.tBack) {
        this.place(a, p, tr.to, tr.home, tr.holdUntil, tr.tBack);
        a.pose = "walk";
        return;
      }
      // Fertig — der nächste Weg beginnt exakt hier.
      p.freeAt = tr.back ? tr.tBack : tr.holdUntil;
      p.trip = null;
      if (!a.retired) {
        p.fx = tr.back ? tr.home.x : tr.to.x;
        p.fy = tr.back ? tr.home.y : tr.to.y;
        this.sync(a, p);
        a.pose = a.deskIndex === -2 ? "stand" : "sit";
        a.flip = this.homeFlip(a);
      }
    }
  }

  private place(a: ActorState, p: Priv, from: Pt, to: Pt, t0: number, t1: number): void {
    const u = t1 <= t0 ? 1 : Math.min(1, Math.max(0, (this._t - t0) / (t1 - t0)));
    p.fx = from.x + (to.x - from.x) * u;
    p.fy = from.y + (to.y - from.y) * u;
    this.sync(a, p);
    if (to.x !== from.x) a.flip = to.x < from.x;
  }

  /** Die Wirkung der Ankunft — ausgelöst zum **geplanten** Ankunftszeitpunkt `tr.tArrive`,
   *  nicht zu `this.t`. Sonst hinge die Startzeit der Sprechblase daran, wie grob getickt wurde. */
  private onArrive(a: ActorState, p: Priv, tr: Trip): void {
    switch (tr.kind) {
      case "arrive":
        a.pose = "sit";
        a.flip = this.homeFlip(a);
        break;
      case "deliver": {
        const to = tr.targetId !== undefined ? this.actors.get(tr.targetId) : undefined;
        if (tr.text !== undefined && tr.text !== "") this.speak(a, tr.text, tr.tArrive);
        if (to) {
          to.heard = tr.tArrive + HEARD_MS;
          // **Nie die Live-Position eines anderen Aktors lesen.** `tick` läuft die Aktoren in
          // Einfügereihenfolge ab; wer nach uns dran ist, steht noch auf dem Stand des vorigen
          // Ticks. Bei `tick(200)` ist der 200 ms alt, bei `tick(25)×8` nur 25 ms — und schon
          // ist die dt-Split-Invarianz dahin. Der Sitz ist dagegen eine reine Funktion des
          // Platzes. (Die mitwandernde Linie zeichnet ohnehin `ActorState.link`, das nur eine
          // Id trägt und die Position erst beim Zeichnen auflöst.)
          const anchor = this.home(to);
          this.fxs.push({
            kind: "link", x: a.x, y: a.y, to: { x: anchor.x, y: anchor.y },
            t0: tr.tArrive, until: tr.tArrive + LINK_MS, seed: a.seed,
          });
          a.link = { to: to.id, until: tr.tArrive + LINK_MS };
        }
        break;
      }
      case "exit":
        // Durch die Tür. Der Platz wird frei — der nächste Lauf soll ihn haben.
        this.unseat(a);
        a.retired = true;
        a.away = true;
        a.say = undefined;
        a.sayAt = undefined;
        a.think = undefined;
        a.thinkAt = undefined;
        p.trips.length = 0;
        break;
      case "coffee":
      case "huddle":
        break;
    }
  }
}
