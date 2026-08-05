// Schicht 1 — der einzige Einstieg der Zeichenschicht.
//
// `renderFrame(ctx, frame, cam, grade)` malt einen kompletten Bildpuffer. Sie bekommt einen
// `Frame` und sonst nichts: keine Engine, keinen Recorder, keine Uhr. Das ist die Naht, an der
// das Zurückspulen hängt — sähe die Zeichenschicht die Engine, könnte sie beim Malen Zustand
// fortschreiben, und das Bild hinge davon ab, wie oft gezeichnet wurde.
//
// ── Warum die Raumgeometrie hier ein zweites Mal steht ───────────────────────
//
// `room.ts` ist Schicht 0, und Schicht 1 darf aus Schicht 0 nur `types`, `ids` und `const`
// sehen (Regel 4; der Prüfer erzwingt es). Die Möbelplätze unten sind deshalb aus **denselben
// Brüchen** von `SCENE` gerechnet wie dort, nicht abgeschrieben. Damit die beiden nicht
// auseinanderlaufen können, ohne dass es jemand merkt, exportiert diese Datei `SEATS_PX`:
// Welle M kann `SEATS_PX[i]` gegen `round(ROOM.seats[i].sit × POS_SCALE)` halten, und die
// Doppelung wird von einer stillen Gefahr zu einer geprüften Zusage.
//
// ── Die Schichtreihenfolge ───────────────────────────────────────────────────
//
//   1 Hülle          Wand · Fenster · Board · Uhr · Tür · Boden · Schrank
//   2 Licht          Fensterlicht (Tag) bzw. Lampenkegel (Abend)
//   3 Teppich
//   4 Bodenschicht   Möbel, Arbeitsplätze **und Figuren gemeinsam** nach `yBase` sortiert
//   5 Luft           Dampf, Staub, Funken, Zettel
//   6 Spawn-Linien
//   7 Überlagerungen Schilder, Blasen, Emotes
//   8 Abstufung      — passiert in der Palette, nicht als Filter
//
// Schicht 4 ist die eine, die man nicht umstellen darf: Möbel und Figuren liegen in **einer**
// Liste und werden zusammen sortiert (Maler-Algorithmus). Zwei getrennte Durchgänge wären
// einfacher zu schreiben und sofort falsch — eine Figur vor dem Schreibtisch der vorderen Reihe
// verschwände hinter ihm.

import type {
  ActorState, Ctx, Frame, Fx, Grade, MoodKind, Pt, ScreenKind, ToolAct,
} from "../types.ts";
import { LINK_MS, PIX, POS_SCALE, SCENE } from "../const.ts";
import { fillA } from "./art.ts";
import type { Pal } from "./palette.ts";
import { GRADES, lookOf, palFor } from "./palette.ts";
import {
  SIZE, WALL_H, WINDOW_STEP, drawBoard, drawCabinet, drawChair, drawClock, drawCoffee,
  drawDesk, drawDoor, drawFloor, drawMonitor, drawPlant, drawRoundtable, drawRug,
  drawTableChair, drawWall, drawWindow, drawWindowLight,
} from "./furniture.ts";
import { FIG_H, FIG_W, actorBox, drawActor, drawGhost } from "./person.ts";
import {
  dust, emotePop, footPuff, linkLine, nameplate, revealOf, spark, speechBubble, steam,
  thoughtBubble,
} from "./props.ts";

// ═══ Kamera ══════════════════════════════════════════════════════════════════

/** `x`/`y` sind der **Pufferpunkt**, der in der Bildmitte steht; `zoom` wird auf eine ganze
 *  Zahl gerundet. Nicht-ganzzahliges Skalieren macht aus Pixelkunst Matsch: eine 1 Pixel breite
 *  Kante würde auf 1,5 Pixel abgebildet und liefe über zwei Spalten mit halber Deckkraft. */
export type Cam = { x: number; y: number; zoom: number };

/** Die Kamera, die den ganzen Raum zeigt. */
export const CAM_FULL: Cam = { x: PIX.w / 2, y: PIX.h / 2, zoom: 1 };

interface CamFit { z: number; ox: number; oy: number }

function camFit(cam: Cam): CamFit {
  const z = Math.max(1, Math.round(cam.zoom));
  return {
    z,
    ox: Math.round(PIX.w / 2 - cam.x * z),
    oy: Math.round(PIX.h / 2 - cam.y * z),
  };
}

/**
 * Die Kamera als `Ctx`-Hülle.
 *
 * Verschieben und Zoomen müssten eigentlich `translate`/`scale` sein — beides ist verboten
 * (Regel 2.1), und zwar zu Recht: eine Transformationsmatrix rastert Kanten auf Subpixel. Statt
 * dessen rechnet diese Hülle jedes Rechteck selbst um. Weil alle Eingaben ganzzahlig sind und
 * `z` eine ganze Zahl ist, sind auch alle Ausgaben ganzzahlig — die Pixel bleiben Pixel.
 *
 * Bei Identität wird der Kontext **unverändert** durchgereicht: der häufigste Fall soll keinen
 * Aufruf-Umweg kosten, und die goldenen Ops-Hashes des Prüfers bleiben so die des nackten
 * Kontexts.
 */
function viewOf(base: Ctx, fit: CamFit): Ctx {
  if (fit.z === 1 && fit.ox === 0 && fit.oy === 0) return base;
  const { z, ox, oy } = fit;
  return {
    get fillStyle(): string | object { return base.fillStyle; },
    set fillStyle(v: string | object) { base.fillStyle = v; },
    get globalAlpha(): number { return base.globalAlpha; },
    set globalAlpha(v: number) { base.globalAlpha = v; },
    fillRect(x: number, y: number, w: number, h: number): void {
      base.fillRect(x * z + ox, y * z + oy, w * z, h * z);
    },
  };
}

// ═══ Der Grundriss ═══════════════════════════════════════════════════════════
//
// Szenenmaße wie in `room.ts`, danach **einmal** mit `POS_SCALE` in Pufferpixel gerundet.
// Ab hier ist jede Zahl eine Pufferzahl; `POS_SCALE` kommt in dieser Datei sonst nur noch
// für Aktor- und Effektpositionen vor (Regel 1).

/** Szenen- → Pufferkoordinate. */
function px(v: number): number {
  return Math.round(v * POS_SCALE);
}

const BANK_X: readonly number[] = [Math.round(SCENE.w * 0.23), Math.round(SCENE.w * 0.79)];
const ROW_Y: readonly number[] = [
  Math.round(SCENE.h * 0.37), Math.round(SCENE.h * 0.58), Math.round(SCENE.h * 0.78),
];
const SEAT_DX = 92;
const SEAT_DY = 14;

export interface SeatPx {
  /** Fußpunkt der sitzenden Figur. */
  sit: Pt;
  /** Mitte der eigenen Tischhälfte. */
  desk: Pt;
  /** Mitte des Monitors auf dieser Tischhälfte. */
  mon: Pt;
  /** Sitzt links vom Tisch und blickt nach rechts? */
  flip: boolean;
}

/**
 * Die dreizehn Plätze in Pufferpixeln.
 *
 * Ein Schreibtisch ist in `room.ts` eine **Zweierbank**: zwei Sitze im Abstand `±SEAT_DX` um
 * eine gemeinsame Tischmitte. Der 26 Pixel breite Tisch-Art deckt diese 55 Pixel nicht ab —
 * deshalb steht hier **je Sitz** eine Tischhälfte, um 14 Pixel zur Bankmitte verschoben. Die
 * zwei Hälften stoßen genau in der Mitte aneinander und ergeben eine 54 Pixel lange Bank. Ein
 * einzelner Tisch in der Mitte hätte beide Figuren 14 Pixel neben dem Tisch sitzen lassen —
 * technisch korrekt und optisch offensichtlich falsch.
 */
function buildSeats(): SeatPx[] {
  const out: SeatPx[] = [];
  for (const bx of BANK_X) {
    for (const ry of ROW_Y) {
      for (const side of [0, 1]) {
        const left = side === 0;
        const sit = { x: px(bx + (left ? -SEAT_DX : SEAT_DX)), y: px(ry + SEAT_DY) };
        const dir = left ? 1 : -1;
        out.push({
          sit,
          desk: { x: sit.x + dir * 14, y: px(ry) },
          // Monitor zur Bankmitte hin — die zwei Schirme einer Bank stehen Rücken an Rücken,
          // wie an einer echten Doppelbank. Nach außen gesetzt sähen sie aus wie Trennwände.
          mon: { x: sit.x + dir * 19, y: px(ry) },
          flip: !left,
        });
      }
    }
  }
  // Chefplatz: 61 % / 32 %. Er sitzt **vor** seinem Tisch, mit gleicher x-Mitte — die einzige
  // Figur im Raum, die uns den Rücken zudreht. `person.ts` liest das an `deskIndex === -1` ab.
  const cx = Math.round(SCENE.w * 0.61);
  const cy = Math.round(SCENE.h * 0.32);
  out.push({
    sit: { x: px(cx), y: px(cy + SEAT_DY) },
    desk: { x: px(cx), y: px(cy) },
    mon: { x: px(cx), y: px(cy) },
    flip: true,
  });
  return out;
}

/** Öffentlich, damit Welle M die Doppelung gegen `room.ts` prüfen kann. */
export const SEATS_PX: readonly SeatPx[] = buildSeats();

const DOOR = { x: px(Math.round(SCENE.w * 0.73)), y: px(Math.round(SCENE.h * 0.135)) };
const TABLE = { x: px(Math.round(SCENE.w * 0.51)), y: px(Math.round(SCENE.h * 0.55)) };
const COFFEE = { x: px(Math.round(SCENE.w * 0.065)), y: px(Math.round(SCENE.h * 0.25)) };

/** Fensterbänder links und rechts der Wandmitte. Die Zahlen sind Pufferpixel und aus keiner
 *  Szenengröße abgeleitet — die Wand ist Kulisse, kein Ort, an dem etwas passiert. */
const WIN_Y = 32;
const WIN_LEFT_X = 20;
const WIN_LEFT_N = 5;
const WIN_RIGHT_X = 396;
const WIN_RIGHT_N = 3;
const BOARD_X = 190;
const CLOCK_X = 246;
const CABINET_X = 196;
const CABINET_Y = 60;

// ═══ Monitorbild und Stimmung ════════════════════════════════════════════════
//
// `toolAct.ts` ist Schicht 0 und für Schicht 1 nicht sichtbar (Regel 4). Die zwei Tabellen
// unten sind deshalb Kopien von `screenFor`/`moodFor` — bewusst **wortgleich**, damit ein
// Vergleich der beiden Stellen im Diff eine Zeile ist und keine Übersetzungsarbeit.

const SCREEN_BY_ACT: Record<ToolAct, ScreenKind> = {
  read: "code", write: "code", run: "log", browse: "page", delegate: "link", other: "blank",
};

const SCREEN_BY_TOOL: Record<string, ScreenKind> = {
  codegraph: "search", gedaechtnis_suchen: "search", fs_list: "search",
  screenshot: "page", read_attachment: "page",
};

/** `waiting` gewinnt vor allem anderen: eine Figur, die auf einen Menschen wartet, ist genau
 *  das, was der Zuschauer sehen soll — auch wenn im Hintergrund noch ein Werkzeug offen ist. */
function screenOf(a: ActorState): ScreenKind {
  if (a.waiting) return "wait";
  if (a.tool !== undefined) {
    const hit = SCREEN_BY_TOOL[a.tool];
    if (hit !== undefined) return hit;
  }
  if (a.act === undefined) return "blank";
  return SCREEN_BY_ACT[a.act];
}

/** Fertig schlägt alles, dann Warten, dann Fehler, sonst Arbeit. */
function moodOf(a: ActorState): MoodKind {
  if (a.done !== undefined) return "done";
  if (a.waiting) return "wait";
  if (a.fails > 0) return "error";
  return "work";
}

// ═══ Die Bodenschicht ════════════════════════════════════════════════════════

interface Piece {
  /** Sortierschlüssel = Fußpunkt. */
  y: number;
  draw(): void;
}

/** Oberkante der Tischplatte — worauf ein Monitor gestellt wird. */
function deskTop(yBase: number): number {
  return yBase - SIZE.desk.h + 1;
}

// ═══ Der Bildaufbau ══════════════════════════════════════════════════════════

export interface RenderOpts {
  /** Ausgewählter Agent — bekommt ein helles Schild und einen Ring auf dem Boden. */
  selected?: string;
  /** Agent unter dem Zeiger. */
  hover?: string;
}

/**
 * Malt ein vollständiges Bild in den 480×270-Puffer.
 *
 * Der Aufrufer hat den Puffer **nicht** zu leeren: die Wand und der Boden decken ihn
 * vollständig ab. Ein `clearRect` wäre ein vierter Kontextaufruf im Vertrag und ein
 * überflüssiger Vollbild-Schreibvorgang je Bild.
 */
export function renderFrame(
  ctx: Ctx, frame: Frame, cam: Cam, grade: Grade, opts?: RenderOpts,
): void {
  const fit = camFit(cam);
  const v = viewOf(ctx, fit);
  const env = GRADES[grade];
  const day = grade === "day";
  const t = frame.t;

  // ── 1 Hülle ───────────────────────────────────────────────────────────────
  drawWall(v, env);
  for (let i = 0; i < WIN_LEFT_N; i++) drawWindow(v, WIN_LEFT_X + i * WINDOW_STEP, WIN_Y, env);
  for (let i = 0; i < WIN_RIGHT_N; i++) drawWindow(v, WIN_RIGHT_X + i * WINDOW_STEP, WIN_Y, env);
  drawBoard(v, BOARD_X, 34, env);
  drawClock(v, CLOCK_X, 26, env);
  drawDoor(v, DOOR.x, WALL_H, env, { open: anyoneTravelling(frame) });
  drawFloor(v, env);
  drawCabinet(v, CABINET_X, CABINET_Y, env);
  drawPlant(v, CABINET_X, CABINET_Y - SIZE.cabinet.h, env, { small: true });

  // ── 2 Licht ───────────────────────────────────────────────────────────────
  if (day) {
    // Zwei Lichtteppiche unter den Fensterbändern. Sie sind der einzige Grund, warum der
    // Tagraum nach Tag aussieht und nicht nach „hellerer Nacht".
    drawWindowLight(v, WIN_LEFT_X + 2 * WINDOW_STEP, WALL_H + 58, 104, 58, env);
    drawWindowLight(v, WIN_RIGHT_X + WINDOW_STEP, WALL_H + 58, 76, 58, env);
  } else {
    // Abends kommt das Licht von den Schirmen. Ein weicher Fleck unter jedem **besetzten**
    // Platz; die leeren bleiben dunkel, und genau das macht sichtbar, wie voll der Raum ist.
    for (const a of frame.actors) {
      if (a.retired === true || a.pose !== "sit") continue;
      const s = seatFor(a);
      if (!s) continue;
      fillA(v, env, "lamp", 0.05, s.mon.x - 18, s.mon.y - 10, 36, 22);
      fillA(v, env, "lamp", 0.04, s.mon.x - 12, s.mon.y - 4, 24, 14);
    }
  }

  // ── 3 Teppich ─────────────────────────────────────────────────────────────
  drawRug(v, TABLE.x, TABLE.y + 41, 112, 62, env);

  // ── 4 Bodenschicht ────────────────────────────────────────────────────────
  const world: Piece[] = [];
  buildRoom(world, v, env, frame);
  buildActors(world, v, env, frame, grade, opts);
  // Stabil (ES2019): bei gleichem Fußpunkt gewinnt die Einfügereihenfolge — und die ist
  // „erst der Stuhl, dann die Figur darauf".
  world.sort((a, b) => a.y - b.y);
  for (const p of world) p.draw();

  // ── 5 Luft ────────────────────────────────────────────────────────────────
  // Dampf steigt von der **Oberkante** der Maschine auf. Vom Fußpunkt aus gerechnet stiege er
  // im Gerät selbst auf und wäre unsichtbar — der klassische Fehler bei der Fußpunkt-Regel.
  steam(v, COFFEE.x, COFFEE.y - 6 - SIZE.coffee.h + 1, env, t, 0x4b4146);
  dust(v, env, t, 0x53544142);
  for (const fx of frame.fx) drawAirFx(v, env, fx, t);
  for (const a of frame.actors) {
    if (a.retired === true || a.pose !== "walk") continue;
    // Staubwölkchen im Takt des Laufzyklus: das Alter kommt aus `t`, nicht aus einem Zähler.
    footPuff(v, px(a.x), px(a.y), env, ((t % 480) / 480));
  }

  // ── 6 Spawn-Linien ────────────────────────────────────────────────────────
  drawLinks(v, env, frame, t);

  // ── 7 Überlagerungen ──────────────────────────────────────────────────────
  drawOverlays(v, env, frame, grade, opts);

  // ── 8 Abstufung ───────────────────────────────────────────────────────────
  // Nichts zu tun. Tag und Abend sind zwei Palettentabellen, kein Filter über dem fertigen
  // Bild: ein abgedunkeltes Tagbild würde die Monitore mitverdunkeln, obwohl sie abends genau
  // die Lichtquelle sind, die alles andere sichtbar macht.
}

/** Steht gerade jemand in der Tür? Dann ist sie offen. Ein Detail, das den Raum erzählen
 *  lässt, ohne dass ein Ereignis dafür erfunden werden müsste. */
function anyoneTravelling(frame: Frame): boolean {
  for (const a of frame.actors) {
    if (a.retired === true) continue;
    if (a.pose === "walk" && Math.abs(px(a.y) - DOOR.y) < 26) return true;
  }
  return false;
}

/** Der Sitz eines Aktors — `deskIndex` 0..11 = Pod, `-1` = Chefplatz, `-2` = auswärts. */
function seatFor(a: ActorState): SeatPx | undefined {
  if (a.deskIndex >= 0 && a.deskIndex < SEATS_PX.length - 1) return SEATS_PX[a.deskIndex];
  if (a.deskIndex === -1) return SEATS_PX[SEATS_PX.length - 1];
  return undefined;
}

/** Möbel und Arbeitsplätze in die Sortierliste legen. */
function buildRoom(world: Piece[], v: Ctx, env: Pal, frame: Frame): void {
  // Wer sitzt wo — für Stuhl (besetzt/frei) und Monitorbild.
  const byDesk = new Map<number, ActorState>();
  for (const a of frame.actors) {
    if (a.retired === true || a.pose !== "sit") continue;
    byDesk.set(a.deskIndex, a);
  }

  for (let i = 0; i < SEATS_PX.length; i++) {
    const s = SEATS_PX[i];
    const idx = i === SEATS_PX.length - 1 ? -1 : i;
    const who = byDesk.get(idx);
    const screen: ScreenKind = who ? screenOf(who) : "blank";
    const mood: MoodKind = who ? moodOf(who) : "work";

    world.push({
      y: s.desk.y,
      draw(): void {
        drawDesk(v, s.desk.x, s.desk.y, env);
        drawMonitor(v, s.mon.x, deskTop(s.desk.y), env, { screen, mood, flip: s.flip });
      },
    });
    // Der Stuhl kommt **vor** der Figur in die Liste. Beide haben denselben Fußpunkt, und die
    // Sortierung ist stabil — also sitzt die Figur auf dem Stuhl und nicht dahinter.
    world.push({
      y: s.sit.y,
      draw(): void {
        drawChair(v, s.sit.x, s.sit.y, env, { occupied: who !== undefined, flip: s.flip });
      },
    });
  }

  // Runder Tisch mit vier Stühlen. Die hinteren zwei stehen vor dem Tisch in der Liste,
  // die vorderen dahinter — beides ergibt sich aus ihrem Fußpunkt, nicht aus der Reihenfolge.
  world.push({ y: TABLE.y - 6, draw: () => drawTableChair(v, TABLE.x - 26, TABLE.y - 6, env) });
  world.push({ y: TABLE.y - 6, draw: () => drawTableChair(v, TABLE.x + 26, TABLE.y - 6, env) });
  world.push({ y: TABLE.y, draw: () => drawRoundtable(v, TABLE.x, TABLE.y, env) });
  world.push({
    y: TABLE.y + 14,
    draw: () => drawTableChair(v, TABLE.x - 30, TABLE.y + 14, env, { flip: true }),
  });
  world.push({ y: TABLE.y + 14, draw: () => drawTableChair(v, TABLE.x + 30, TABLE.y + 14, env) });

  world.push({ y: COFFEE.y - 6, draw: () => drawCoffee(v, COFFEE.x, COFFEE.y - 6, env) });
  world.push({ y: 214, draw: () => drawPlant(v, 12, 214, env) });
  world.push({ y: 258, draw: () => drawPlant(v, PIX.w - 14, 258, env) });
}

/** Figuren in dieselbe Sortierliste legen — das ist der Kern von Schicht 4. */
function buildActors(
  world: Piece[], v: Ctx, env: Pal, frame: Frame, grade: Grade, opts?: RenderOpts,
): void {
  const sel = opts?.selected;
  const hov = opts?.hover;
  for (const a of frame.actors) {
    if (a.retired === true) continue;
    const cx = px(a.x);
    const yBase = px(a.y);
    // Außerhalb des Puffers wird nicht gezeichnet. Der Sammelpunkt für „auswärts" liegt in
    // `room.ts` bei `y = -100`, also weit über der Decke — ohne diese Prüfung liefe die
    // Zeichenschleife für jede abgeschobene Figur einmal komplett durch.
    if (cx < -FIG_W || cx > PIX.w + FIG_W || yBase < -FIG_H || yBase > PIX.h + FIG_H) continue;

    const pal = palFor(grade, lookOf(a.seed));
    const ring = a.id === sel ? "acc" : a.id === hov ? "wallHi" : undefined;
    const ghost = a.deskIndex === -2;
    world.push({
      y: yBase,
      draw(): void {
        if (ring) {
          // Markierungsring auf dem Boden statt Umriss um die Figur: ein Umriss müsste
          // Pixel für Pixel um ein zusammengesetztes Sprite gelegt werden, ein Ring sind
          // vier Rechtecke — und er verdeckt die Figur nicht.
          fillA(v, env, ring, 0.55, cx - 7, yBase, 14, 1);
          fillA(v, env, ring, 0.35, cx - 8, yBase - 1, 1, 2);
          fillA(v, env, ring, 0.35, cx + 7, yBase - 1, 1, 2);
          fillA(v, env, ring, 0.25, cx - 6, yBase - 2, 12, 1);
        }
        if (ghost) drawGhost(v, cx, yBase, pal, frame.t, a.seed);
        else drawActor(v, a, frame.t, pal);
      },
    });
  }
}

/** Kurzlebige Effekte, die in der Luft liegen (Funke, fallender Zettel). */
function drawAirFx(v: Ctx, env: Pal, fx: Fx, t: number): void {
  const span = Math.max(1, fx.until - fx.t0);
  const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
  const cx = px(fx.x);
  const y = px(fx.y);
  if (fx.kind === "spark") {
    spark(v, cx, y - FIG_H + 6, env, age, fx.seed);
  } else if (fx.kind === "drop") {
    // Ein Blatt fällt auf den Tisch: es sinkt und legt sich flach hin. Vier Rechtecke.
    const drop = Math.round(age * 8);
    const yy = y - FIG_H + 2 + drop;
    fillA(v, env, "paper", 1 - age * 0.3, cx + 6, yy, 5, 4);
    fillA(v, env, "ink", 0.6 - age * 0.3, cx + 7, yy + 1, 3, 1);
  }
}

/** Spawn- und Übergabelinien. */
function drawLinks(v: Ctx, env: Pal, frame: Frame, t: number): void {
  const at = new Map<string, Pt>();
  for (const a of frame.actors) at.set(a.id, { x: px(a.x), y: px(a.y) - FIG_H + 6 });

  for (const fx of frame.fx) {
    if (fx.kind !== "link" || !fx.to) continue;
    const span = Math.max(1, fx.until - fx.t0);
    const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
    linkLine(v, { x: px(fx.x), y: px(fx.y) - FIG_H + 6 },
      { x: px(fx.to.x), y: px(fx.to.y) - FIG_H + 6 }, env, age);
  }

  // Die mitwandernde Linie: solange `link` steht, hängt sie an den **aktuellen** Positionen
  // beider Figuren. Der `Fx` oben ist der Blitz beim Auslösen, das hier ist das Band.
  for (const a of frame.actors) {
    if (a.link === undefined || a.retired === true) continue;
    const from = at.get(a.id);
    const to = at.get(a.link.to);
    if (!from || !to) continue;
    linkLine(v, from, to, env, Math.max(0, Math.min(1, 1 - (a.link.until - t) / LINK_MS)));
  }
}

// ═══ Überlagerungen ══════════════════════════════════════════════════════════

/** Der Rollenname, gekürzt auf das, was auf ein Schild passt. `exec_agent` → `EXEC`.
 *  Der Teil vor dem ersten Unterstrich ist bei allen Traccoon-Rollen der aussagekräftige
 *  (`plan_agent`, `exec_agent`, `review_agent`, `assistant`). */
function shortRole(role: string): string {
  const cut = role.indexOf("_");
  const head = cut > 0 ? role.slice(0, cut) : role;
  return head.length > 9 ? head.slice(0, 9) : head;
}

function drawOverlays(
  v: Ctx, env: Pal, frame: Frame, grade: Grade, opts?: RenderOpts,
): void {
  const t = frame.t;
  const sel = opts?.selected;
  const hov = opts?.hover;

  for (const a of frame.actors) {
    if (a.retired === true) continue;
    const cx = px(a.x);
    const yBase = px(a.y);
    if (cx < -FIG_W || cx > PIX.w + FIG_W || yBase < 0 || yBase > PIX.h + FIG_H) continue;

    const chosen = a.id === sel;
    const hovered = a.id === hov;
    // Schilder für alle, aber blass: ein Raum mit zwölf gleich hellen Etiketten liest sich als
    // Tabelle, nicht als Raum. Hell wird nur, wonach gefragt wurde.
    nameplate(v, cx, yBase + 11, env, shortRole(a.role), {
      sub: chosen ? (a.issue ?? a.model ?? undefined) : undefined,
      selected: chosen,
      dim: !chosen && !hovered,
    });

    const top = yBase - FIG_H - 2;
    if (a.say !== undefined) {
      speechBubble(v, cx, top, env, a.say, {
        reveal: revealOf(a.say, a.sayAt !== undefined ? t - a.sayAt : 0),
        verdict: a.verdict,
      });
    } else if (a.think !== undefined) {
      thoughtBubble(v, cx, top, env, a.think, t);
    }
  }

  // Emotes zuletzt: sie sind das Kurzsignal und dürfen von nichts überdeckt werden.
  for (const fx of frame.fx) {
    if (fx.kind !== "emote" || fx.text === undefined) continue;
    const g = fx.text;
    if (g !== "✓" && g !== "✗" && g !== "!") continue;
    const span = Math.max(1, fx.until - fx.t0);
    const age = Math.max(0, Math.min(1, (t - fx.t0) / span));
    emotePop(v, px(fx.x) + 9, px(fx.y) - FIG_H - 3, env, g, age);
  }
}

// ═══ Trefferprüfung ══════════════════════════════════════════════════════════

/**
 * Welcher Agent steht unter dem Punkt? `px`/`py` sind **Pufferkoordinaten** (die Bühne rechnet
 * die Mauszeigerposition dorthin um, weil nur sie den Blit-Faktor kennt).
 *
 * Geprüft wird von **vorn nach hinten**, also rückwärts durch die nach `y` sortierte Liste.
 * Vorn heißt weiter unten heißt später gezeichnet — und was oben liegt, muss auch klickbar
 * sein. Eine Prüfung in Listenreihenfolge träfe die Figur dahinter, und der Fehler sähe aus
 * wie ein Rundungsproblem.
 */
export function hitTest(
  frame: Frame, cam: Cam, pxCoord: number, pyCoord: number,
): string | undefined {
  const fit = camFit(cam);
  const bx = (pxCoord - fit.ox) / fit.z;
  const by = (pyCoord - fit.oy) / fit.z;

  for (let i = frame.actors.length - 1; i >= 0; i--) {
    const a = frame.actors[i];
    if (a.retired === true) continue;
    const box = actorBox(px(a.x), px(a.y));
    if (bx >= box.x && bx < box.x + box.w && by >= box.y && by < box.y + box.h) return a.id;
  }
  return undefined;
}

/** Wo eine Figur im Puffer steht — für die Bühne (Kamera folgt der Auswahl) und den Inspektor. */
export function actorAt(frame: Frame, id: string): Pt | undefined {
  for (const a of frame.actors) {
    if (a.id === id) return { x: px(a.x), y: px(a.y) };
  }
  return undefined;
}
