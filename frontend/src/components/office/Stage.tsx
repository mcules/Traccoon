// Schicht 2 — die Bühne. Das Fenster in den Raum, und die einzige Stelle, an der aus
// Simulationszeit sichtbare Pixel werden.
//
// ══ Die Regel, an der dieses Feature steht oder fällt ════════════════════════════════════════
//
// **Die Bühne rendert nie pro Bild neu.** Im rAF-Pfad wird kein einziger React-Zustand
// angefasst. Alles Bewegliche lebt in Refs: Replay, Kamera, Frame, rAF-Handle, Zeiger,
// gespiegelte Props. React sieht von dieser Komponente nur, was sich in Menschentempo ändert —
// Auswahl, Überfahren, `revision` (im Feed auf 5/s gedrosselt), Theme.
//
// Der Grund ist nicht Eleganz, sondern Messbarkeit: der Projekt-Reiter läuft neben einer
// Query, die alle 8 Sekunden nachfasst, in Tabs, die Leute stundenlang offen lassen. Ein
// `setState` je Bild wären 60 Renderdurchläufe der Elternkomponente je Sekunde, und die
// Elternkomponente hält Dock, Inspektor und Zeitleiste.
//
// ══ Die drei Effekte, und der vierte, der keiner ist ════════════════════════════════════════
//
//   · Replay aus dem Log      `[revision]`  — die Ein-Zahl-Veraltungsprüfung
//   · Seek                    `[seekTs]`    — zurückspulen bzw. live
//   · rAF-Schleife            `[]`          — genau einmal beim Einhängen
//
// In dieser Reihenfolge stehen sie auch in der Datei, und die Reihenfolge ist Absicht: React
// führt Effekte in Deklarationsreihenfolge aus, also existiert der `Replay` bereits, wenn der
// Seek ihn sucht, und beide sind fertig, bevor das erste Bild gerechnet wird.
//
// Davor steht ein vierter, winziger: er spiegelt `seekTs`, `speed`, `grade`, `selected`,
// `hover` und die zwei Rückrufe in Refs und tut sonst nichts. Er ist die Ausnahme, die die
// anderen drei erst schlank macht — ohne ihn müsste die Schleife `speed` in der
// Abhängigkeitsliste führen und würde bei jedem Tempowechsel mitten im Bild neu starten.
//
// ══ Die eine dokumentierte Ausnahme vom Pixel-Vertrag ═══════════════════════════════════════
//
// Das `drawImage` unten (Puffer → sichtbarer Canvas) ist die einzige vom Zeichenvertrag
// ausgenommene Stelle, und sie lebt laut PIXEL-CONTRACT.md Regel 4 genau hier. Alles andere in
// dieser Datei zeichnet nichts: `renderFrame` malt in den 480×270-Puffer, diese Datei blittet
// ihn ganzzahlig hoch und füllt den Rest als Briefkasten.

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent } from "react";
import type { ActorState, Frame, Grade } from "./types.ts";
import { LINK_MS, MAX_FRAME_MS, PIX } from "./const.ts";
import { Replay } from "./replay.ts";
import type { RecorderApi } from "./useOfficeFeed.ts";
import { GRADES } from "./pixel/palette.ts";
import type { Pal } from "./pixel/palette.ts";
import { FIG_H } from "./pixel/person.ts";
import type { Cam } from "./pixel/scene.ts";
import { CAM_FULL, actorAt, hitTest, renderFrame } from "./pixel/scene.ts";

// ═══ Stellschrauben ══════════════════════════════════════════════════════════════════════════

/** Höchster Zoom. Bei 4 zeigt der Puffer noch 120×67 Pixel — darunter sieht man einzelne
 *  Sprites und sonst nichts mehr, und die Ansicht verliert genau das, wofür es sie gibt. */
const MAX_ZOOM = 4;

/** Zeitkonstante der Kamerabewegung. `dt/220` heißt: nach ~220 ms ist der Rest der Strecke
 *  einmal ausgeglichen — schnell genug, um zu folgen, langsam genug, um nicht zu zucken. */
const CAM_EASE_MS = 220;

/** Ein Tastendruck schwenkt so viele Pufferpixel. */
const PAN_STEP = 24;

/** Höchstens vier Hover-Meldungen je Sekunde an React. Ohne Drosselung rendert die
 *  Elternkomponente bei jeder Mausbewegung — und die hält Dock und Inspektor. */
const HOVER_MS = 250;

/** Takt der Schleife bei `prefers-reduced-motion`. Kein rAF: zwei Bilder je Sekunde reichen,
 *  um Zustandswechsel zu zeigen, und mehr will jemand mit dieser Einstellung nicht sehen. */
const CALM_TICK_MS = 500;

/** Hängt die Wiedergabe im Livebetrieb weiter als das hinter dem jüngsten Ereignis zurück,
 *  wird aufgeschlossen statt nachgelaufen. Passiert nach jedem angehaltenen Tab. */
const LIVE_CATCHUP_MS = 4000;

/** Die eingefrorene Animationsuhr des Ruhemodus (siehe `calmFrame`). */
const CALM_CLOCK = 0;

/** So alt tut der Ruhemodus jede Blase — damit `revealOf` auf 1 steht und der Text **ganz**
 *  dasteht, statt sich einzutippen. */
const CALM_SETTLED_MS = 600_000;

// ═══ Oberfläche ══════════════════════════════════════════════════════════════════════════════

export interface StageProps {
  /** Das Log. Die Bühne liest nur (`entries`), sie schreibt nie hinein. */
  recorder: RecorderApi;
  /** Die Ein-Zahl-Veraltungsprüfung des Recorders. Nur hieran hängt der Replay-Abgleich. */
  revision: number;
  /** `null` = live. Sonst der Zeitpunkt, auf den die Zeitleiste zurückgespult hat. */
  seekTs: number | null;
  /** Faktor auf die vergehende Zeit. `0` = angehalten, `1` = Echtzeit. */
  speed: number;
  /** Tag- oder Abendbüro. Kommt aus `useTheme`, nicht aus der Uhr. */
  grade: Grade;
  /** Ausgewählter Agent (heller Ring, Schild, Kameraverfolgung). */
  selected?: string;
  /** Agent unter dem Zeiger. */
  hover?: string;
  onSelect(id: string | undefined): void;
  onHover(id: string | undefined): void;
  className?: string;
}

// ═══ Kamera ══════════════════════════════════════════════════════════════════════════════════

/** Der gelebte Kamerazustand: `x/y` ist, wo sie steht, `wantX/wantY`, wo sie hin soll. Die
 *  Trennung ist der ganze Grund, warum ein Schwenk weich aussieht und ein Sprung trotzdem
 *  sofort ankommt (dann werden beide gemeinsam gesetzt). */
interface CamState { x: number; y: number; zoom: number; wantX: number; wantY: number }

/**
 * Hält die Kamera im Raum.
 *
 * Bei ganzzahligem `zoom` zeigt der Puffer `PIX.w/z × PIX.h/z` Pixel um `x/y`. Wird `x` näher
 * als die halbe Sichtbreite an den Rand gelassen, liefe der Blick über die Wand hinaus — und
 * weil `renderFrame` den Puffer bewusst **nicht** leert (Wand und Boden decken ihn ab), stünde
 * dort das vorige Bild statt Nichts. Die Klemmung ist also nicht Kosmetik, sondern die Zusage,
 * auf der das Nicht-Leeren beruht.
 */
function clampCam(c: CamState): void {
  const halfW = PIX.w / (2 * c.zoom);
  const halfH = PIX.h / (2 * c.zoom);
  c.x = Math.min(PIX.w - halfW, Math.max(halfW, c.x));
  c.y = Math.min(PIX.h - halfH, Math.max(halfH, c.y));
  c.wantX = Math.min(PIX.w - halfW, Math.max(halfW, c.wantX));
  c.wantY = Math.min(PIX.h - halfH, Math.max(halfH, c.wantY));
}

/** Wo ein Pufferpunkt unter dieser Kamera landet — dieselbe Rechnung wie `camFit` in
 *  `pixel/scene.ts`, die dort privat ist. Die Doppelung ist bewusst und klein: sie wird für
 *  die DOM-Schilder gebraucht, und ein Export nur dafür würde die Zeichenschicht um eine
 *  Schnittstelle erweitern, die niemand sonst braucht. */
function camOffset(cam: Cam): { z: number; ox: number; oy: number } {
  const z = Math.max(1, Math.round(cam.zoom));
  return { z, ox: Math.round(PIX.w / 2 - cam.x * z), oy: Math.round(PIX.h / 2 - cam.y * z) };
}

// ═══ Ruhemodus ═══════════════════════════════════════════════════════════════════════════════

/**
 * Derselbe Raum, nur ohne Bewegung — für `prefers-reduced-motion`.
 *
 * **Reduzierte Bewegung heißt nicht „langsamer", sondern „weniger".** Ein halbiertes Bildtempo
 * wäre für jemanden mit Bewegungsempfindlichkeit keine Erleichterung, sondern dasselbe Ruckeln
 * in zäh. Deshalb wird hier nicht gebremst, sondern die **Animationsuhr eingefroren**: der
 * Frame geht mit `t = CALM_CLOCK` in die Zeichenschicht, und damit stehen Laufzyklus, Blinzeln,
 * Staub, Dampf und die Punkte der Denkblase still. Sichtbar ändert sich nur noch, was sich
 * wirklich geändert hat — wer wo sitzt, wer was sagt, welcher Monitor was zeigt.
 *
 * Die Simulation läuft dabei **normal weiter** (`Replay.advance` im ruhigen Takt). Sie
 * anzuhalten hieße, den Raum anzulügen; eingefroren ist die Darstellung, nicht die Zeit.
 *
 * Drei Umschriften sind nötig, damit das eingefrorene `t` nichts kaputtmacht:
 *
 *   · `pose: "walk" → "stand"` — kein Laufzyklus, keine Staubwölkchen unter den Füßen. Die
 *     Figur steht dann an ihrer aktuellen Stelle und ist nach `SETTLE_MS` ohnehin am Ziel.
 *   · `sayAt`/`thinkAt` weit in die Vergangenheit — `revealOf` steht damit auf 1 und die Blase
 *     erscheint **ganz**, statt sich Zeichen für Zeichen einzutippen.
 *   · `link.until` auf `CALM_CLOCK + LINK_MS` — das Band zwischen Eltern- und Kindlauf hat
 *     damit Alter 0 und steht voll da, statt bei eingefrorener Uhr auszublassen.
 *
 * Die Effekte in der Luft (Funke, fallender Zettel, Emote-Pop) fallen ganz weg: sie sind reine
 * Partikel, sie tragen keine Information, die nicht auch am Monitor oder am Blasenrand steht.
 *
 * Kopiert wird flach und nur dort, wo etwas zu ändern ist — die `ActorState` aus `Frame` gehören
 * der Engine und dürfen **nie** verändert werden (`Engine.frame()` gibt die Objekte selbst
 * heraus, nur die Liste ist eine Kopie).
 */
function calmFrame(f: Frame): Frame {
  const actors: ActorState[] = [];
  for (const a of f.actors) {
    const needs = a.pose === "walk" || a.say !== undefined || a.think !== undefined
      || a.link !== undefined;
    if (!needs) { actors.push(a); continue; }
    actors.push({
      ...a,
      pose: a.pose === "walk" ? "stand" : a.pose,
      sayAt: a.say !== undefined ? CALM_CLOCK - CALM_SETTLED_MS : a.sayAt,
      thinkAt: a.think !== undefined ? CALM_CLOCK - CALM_SETTLED_MS : a.thinkAt,
      link: a.link !== undefined ? { to: a.link.to, until: CALM_CLOCK + LINK_MS } : undefined,
    });
  }
  // Die Reihenfolge bleibt die der Engine (nach `y` sortiert) — der Maler-Algorithmus der
  // Zeichenschicht hängt daran.
  return { t: CALM_CLOCK, actors, fx: [] };
}

// ═══ Die Bühne ═══════════════════════════════════════════════════════════════════════════════

export default function Stage(props: StageProps): JSX.Element {
  const {
    recorder, revision, seekTs, speed, grade, selected, hover, onSelect, onHover, className,
  } = props;

  // ── DOM ────────────────────────────────────────────────────────────────────────────────────
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const selTagRef = useRef<HTMLSpanElement | null>(null);
  const hovTagRef = useRef<HTMLSpanElement | null>(null);

  // ── Alles Bewegliche ───────────────────────────────────────────────────────────────────────
  const replayRef = useRef<Replay | null>(null);
  const frameRef = useRef<Frame | null>(null);
  const camRef = useRef<CamState>({
    x: CAM_FULL.x, y: CAM_FULL.y, zoom: CAM_FULL.zoom, wantX: CAM_FULL.x, wantY: CAM_FULL.y,
  });
  /** Die Kamera, mit der das **zuletzt gemalte** Bild entstanden ist. Die Trefferprüfung muss
   *  gegen sie rechnen und nicht gegen die aktuelle: zwischen zwei Bildern ist die Kamera schon
   *  ein Stück weitergewandert, und ein Klick träfe sonst knapp daneben. */
  const hitCamRef = useRef<Cam>({ ...CAM_FULL });
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const prevNowRef = useRef(0);
  const dirtyRef = useRef(true);

  /** Blit-Geometrie, einmal je Größenänderung gerechnet: ganzzahliger Faktor und der
   *  Briefkasten-Versatz, beides in **Geräte**pixeln, dazu das Verhältnis zu CSS-Pixeln. */
  const blitRef = useRef({ scale: 1, ox: 0, oy: 0, ratio: 1 });

  // ── Ruhezustände ───────────────────────────────────────────────────────────────────────────
  const shownRef = useRef(true);      // im Sichtfeld?
  const wakeRef = useRef(true);       // Tab im Vordergrund?
  const calmRef = useRef(false);      // prefers-reduced-motion?

  // ── Gespiegelte Props ──────────────────────────────────────────────────────────────────────
  const seekRef = useRef<number | null>(seekTs);
  const speedRef = useRef(speed);
  const gradeRef = useRef<Grade>(grade);
  const palRef = useRef<Pal>(GRADES[grade]);
  const selRef = useRef<string | undefined>(selected);
  const hovRef = useRef<string | undefined>(hover);
  const onSelectRef = useRef(onSelect);
  const onHoverRef = useRef(onHover);

  // ── Zeiger ─────────────────────────────────────────────────────────────────────────────────
  const hoverPtRef = useRef<{ x: number; y: number } | null>(null);
  const hoverTimerRef = useRef<number | null>(null);
  const hoverSentRef = useRef<string | undefined>(undefined);

  // ── Effekt 4: die Spiegel ──────────────────────────────────────────────────────────────────
  //
  // Winzig und ohne Abhängigkeitsliste: er tut nichts als zuweisen. Genau deshalb darf er bei
  // jedem Render laufen — und genau deshalb startet ein Geschwindigkeitswechsel die Schleife
  // nicht neu.
  useEffect(() => {
    seekRef.current = seekTs;
    speedRef.current = speed;
    if (gradeRef.current !== grade) { gradeRef.current = grade; palRef.current = GRADES[grade]; }
    selRef.current = selected;
    hovRef.current = hover;
    onSelectRef.current = onSelect;
    onHoverRef.current = onHover;
    dirtyRef.current = true;
  });

  // ── Effekt 2: der Replay ───────────────────────────────────────────────────────────────────
  //
  // `revision` ist die Ein-Zahl-Veraltungsprüfung: sie bewegt sich bei jedem Zuwachs **und**
  // bei jedem Verwurf am Kopf. `Replay.extend` schreibt bei reinem Zuwachs nur den Logzeiger
  // fort (kein Neuaufbau, keine drei Stunden Ticks) und baut nur dann ehrlich neu, wenn der
  // bereits abgespielte Anfang nicht mehr derselbe ist. Damit ist der Verwurfsfall bereits
  // abgedeckt — eine zweite Prüfung auf `bounds().dropped` hier wäre eine zweite Wahrheit über
  // denselben Sachverhalt.
  useEffect(() => {
    const log = recorder.entries();
    let rp = replayRef.current;
    if (rp === null) { rp = new Replay(log); replayRef.current = rp; }
    else rp.extend(log);
    // Nachziehen, weil `extend` im Verwurfsfall neu aufbaut: dann steht ein anderer Raum da,
    // und bei `speed === 0` käme die Schleife nie dazu, ihn zu holen.
    frameRef.current = rp.frame();
    dirtyRef.current = true;
  }, [recorder, revision]);

  // ── Effekt 3: zurückspulen bzw. live ───────────────────────────────────────────────────────
  //
  // Ein `seek` ist teuer (neue Engine, Log von vorn) und passiert deshalb **nur** hier: wenn
  // der Nutzer die Zeitleiste anfasst. Die Schleife spult nie, sie läuft nur weiter.
  useEffect(() => {
    const rp = replayRef.current;
    if (rp === null) return;
    if (seekTs === null) rp.toLive();
    else rp.seek(seekTs);
    frameRef.current = rp.frame();
    dirtyRef.current = true;
  }, [seekTs]);

  // ── Effekt 1: die Schleife ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const host = hostRef.current;
    const cvs = canvasRef.current;
    if (!host || !cvs) return;

    // Puffer anlegen. `OffscreenCanvas` spart ein DOM-Element; wo es fehlt, tut ein
    // abgekoppeltes `<canvas>` genau dasselbe. Zwei Zweige statt eines Unions-Aufrufs, weil
    // `getContext` auf beiden Typen verschiedene Überladungen hat.
    let buf: HTMLCanvasElement | OffscreenCanvas;
    let bctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
    if (typeof OffscreenCanvas !== "undefined") {
      const off = new OffscreenCanvas(PIX.w, PIX.h);
      bctx = off.getContext("2d", { alpha: false });
      buf = off;
    } else {
      const el = document.createElement("canvas");
      el.width = PIX.w;
      el.height = PIX.h;
      bctx = el.getContext("2d", { alpha: false });
      buf = el;
    }
    const vctx = cvs.getContext("2d", { alpha: false });
    if (!bctx || !vctx) return;
    // Puffer und Kontexte bleiben **lokal** in dieser Schleife: sie werden mit ihr angelegt und
    // mit ihr weggeworfen. Ein Ref darauf wäre eine zweite Lebensdauer für dasselbe Objekt.
    // `Ctx` (der erlaubte Ausschnitt fillStyle/globalAlpha/fillRect) erfüllt ein echter
    // 2D-Kontext strukturell — die Zeichenschicht sieht nichts anderes.

    // ── Größe ────────────────────────────────────────────────────────────────────────────────
    const layout = (): void => {
      const r = host.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(1, Math.round(r.width * dpr));
      const h = Math.max(1, Math.round(r.height * dpr));
      if (cvs.width !== w || cvs.height !== h) { cvs.width = w; cvs.height = h; }
      // Ganzzahlig, sonst wird Pixelkunst zu Matsch: ein 1 Pixel breiter Strich liefe bei
      // Faktor 1,5 über zwei Spalten mit halber Deckkraft.
      const scale = Math.max(1, Math.floor(Math.min(w / PIX.w, h / PIX.h)));
      blitRef.current = {
        scale,
        ox: Math.round((w - PIX.w * scale) / 2),
        oy: Math.round((h - PIX.h * scale) / 2),
        ratio: r.width > 0 ? w / r.width : dpr,
      };
      vctx.imageSmoothingEnabled = false;
      dirtyRef.current = true;
    };
    layout();

    // ── Ein Schild an seine Figur hängen ─────────────────────────────────────────────────────
    //
    // Die Schilder sind echte `<span>` über dem Canvas, keine gemalten Pixel: man soll sie
    // markieren können und ein Screenreader soll sie lesen. Gesetzt wird hier **nur die Lage**
    // (Text und Existenz gehören React) — deshalb kostet ein wandernder Agent keinen
    // Renderdurchlauf.
    const place = (el: HTMLSpanElement | null, id: string | undefined, f: Frame,
      cam: Cam): void => {
      if (!el) return;
      const p = id !== undefined ? actorAt(f, id) : undefined;
      if (!p) { el.style.visibility = "hidden"; return; }
      const { z, ox, oy } = camOffset(cam);
      const b = blitRef.current;
      const cssScale = b.scale / b.ratio;
      const sx = (b.ox + (p.x * z + ox) * b.scale) / b.ratio;
      const sy = (b.oy + (p.y * z + oy) * b.scale) / b.ratio;
      // Über dem Kopf, mittig. `translate(-50%, -100%)` steht im Stil des Elements.
      el.style.left = `${Math.round(sx)}px`;
      el.style.top = `${Math.round(sy - FIG_H * z * cssScale)}px`;
      // Stapelung wie im Canvas: wer weiter unten steht, steht vorn. Damit deckt das Schild
      // einer vorderen Figur das einer hinteren ab — und nicht umgekehrt.
      el.style.zIndex = String(Math.max(0, Math.round(sy)));
      el.style.visibility = "visible";
    };

    // ── Ein Bild ─────────────────────────────────────────────────────────────────────────────
    const paint = (): boolean => {
      const f = frameRef.current;
      if (!f) return false;
      const c = camRef.current;
      const cam: Cam = { x: c.x, y: c.y, zoom: c.zoom };
      hitCamRef.current = cam;

      renderFrame(bctx, calmRef.current ? calmFrame(f) : f, cam, gradeRef.current, {
        selected: selRef.current,
        hover: hovRef.current,
      });

      // ── Die eine ausgenommene Stelle (PIXEL-CONTRACT.md Regel 4) ───────────────────────────
      const b = blitRef.current;
      vctx.imageSmoothingEnabled = false;
      // Briefkasten in einer Palettenfarbe: der Rand gehört zum Raum, nicht zur Anwendung.
      // Schwarz sähe im Tagbüro aus wie ein Defekt.
      vctx.fillStyle = palRef.current.wallLo;
      vctx.fillRect(0, 0, cvs.width, cvs.height);
      vctx.drawImage(buf, 0, 0, PIX.w, PIX.h, b.ox, b.oy, PIX.w * b.scale, PIX.h * b.scale);

      place(selTagRef.current, selRef.current, f, cam);
      place(hovTagRef.current, hovRef.current !== selRef.current ? hovRef.current : undefined,
        f, cam);
      return true;
    };

    // ── Kamera je Bild ───────────────────────────────────────────────────────────────────────
    const moveCam = (dt: number, f: Frame | null): boolean => {
      const c = camRef.current;
      // Verfolgung: nur bei Zoom, und nur wenn die Figur aus der Mitte läuft. Ein Nachziehen
      // bei jedem Schritt nähme dem Nutzer das Schwenken aus der Hand.
      const sel = selRef.current;
      if (c.zoom > 1 && sel !== undefined && f) {
        const p = actorAt(f, sel);
        if (p) {
          const halfW = PIX.w / (2 * c.zoom) * 0.7;
          const halfH = PIX.h / (2 * c.zoom) * 0.7;
          if (Math.abs(p.x - c.x) > halfW || Math.abs(p.y - c.y) > halfH) {
            c.wantX = p.x;
            c.wantY = p.y;
            clampCam(c);
          }
        }
      }
      const k = Math.min(1, dt / CAM_EASE_MS);
      const dx = c.wantX - c.x;
      const dy = c.wantY - c.y;
      if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) {
        if (dx !== 0 || dy !== 0) { c.x = c.wantX; c.y = c.wantY; return true; }
        return false;
      }
      c.x += dx * k;
      c.y += dy * k;
      return true;
    };

    // ── Der Takt ─────────────────────────────────────────────────────────────────────────────
    const step = (): void => {
      const now = performance.now();
      // Beidseitig geklemmt. Ein Tab, der im Hintergrund lag, brächte sonst Minuten in einem
      // einzigen `dt` — und die Klemmung ist Sache der Schleife, nicht der Engine: `Replay`
      // klemmt `dtMs` bewusst nicht, weil das die dt-Split-Invarianz bräche.
      //
      // Abweichung im Ruhemodus: dort ist der Takt `CALM_TICK_MS` (500 ms), und mit dem
      // rAF-Deckel von 100 ms liefe der Raum mit einem Fünftel der Echtzeit — die Wiedergabe
      // fiele immer weiter zurück. Der Deckel muss also über dem eigenen Takt liegen; die
      // Engine zerlegt die Spanne ohnehin selbst in `LIVE_STEP_MS`-Schritte.
      const cap = calmRef.current ? CALM_TICK_MS * 2 : MAX_FRAME_MS;
      const dt = Math.min(cap, Math.max(0, now - prevNowRef.current));
      prevNowRef.current = now;

      const rp = replayRef.current;
      let moved = false;
      if (rp) {
        const sp = speedRef.current;
        if (sp > 0) {
          if (seekRef.current === null) {
            rp.advance(dt * sp);
            // Aufschließen statt nachlaufen: nach einem angehaltenen Tab hinkt die Wiedergabe
            // um die ganze Pause hinterher, und niemand will die Vergangenheit nachspielen
            // sehen, wenn er „live" gewählt hat.
            if (rp.to - rp.position > LIVE_CATCHUP_MS) rp.toLive();
          } else {
            // Zurückgespult: die Zeit sitzt auf `seekTs` und läuft von dort weiter, sobald
            // wieder abgespielt wird. Angehalten (`speed === 0`) steht sie einfach.
            rp.advance(dt * sp);
          }
          moved = true;
        }
        if (moved || frameRef.current === null) frameRef.current = rp.frame();
      }

      // Nichts bewegt, Kamera steht, nichts angefragt → auch nichts malen. Bei `speed === 0`
      // ist das der Normalfall, und ein angehaltenes Bild soll keine 60 Vollbilder je Sekunde
      // kosten. Der Merker fällt erst, wenn wirklich gemalt wurde.
      const cammoved = moveCam(dt, frameRef.current);
      if ((moved || cammoved || dirtyRef.current) && paint()) dirtyRef.current = false;
    };

    // ── Start und Stopp ──────────────────────────────────────────────────────────────────────
    const tickRaf = (): void => {
      rafRef.current = window.requestAnimationFrame(tickRaf);
      step();
    };

    const stop = (): void => {
      if (rafRef.current !== null) { window.cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      if (timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = null; }
    };

    /** Die einzige Stelle, die entscheidet, ob überhaupt gerechnet wird. Angehalten wird nicht
     *  aus Sparsamkeit, sondern weil diese Ansicht in Tabs lebt, die tagelang offen stehen. */
    const sync = (): void => {
      const want = shownRef.current && wakeRef.current;
      const running = rafRef.current !== null || timerRef.current !== null;
      if (want === running) return;
      if (!want) { stop(); return; }
      // Beim Aufwachen die Uhr neu setzen, sonst wäre das erste `dt` die ganze Pause.
      prevNowRef.current = performance.now();
      dirtyRef.current = true;
      const rp = replayRef.current;
      if (rp && seekRef.current === null && speedRef.current > 0) rp.toLive();
      if (calmRef.current) {
        // Einmal sofort: sonst stünde nach dem Einhängen eine halbe Sekunde lang gar nichts da.
        step();
        timerRef.current = window.setInterval(step, CALM_TICK_MS);
      } else {
        tickRaf();
      }
    };

    // ── Beobachter ───────────────────────────────────────────────────────────────────────────
    const ro = new ResizeObserver(layout);
    ro.observe(host);

    // Außerhalb des Sichtfelds wird nicht gerechnet. Das ist der Normalfall in einem
    // Projekt-Reiter, in dem jemand nach unten gescrollt hat.
    const io = new IntersectionObserver((es) => {
      shownRef.current = es.some((e) => e.isIntersecting);
      sync();
    }, { threshold: 0 });
    io.observe(host);

    const onVisibility = (): void => { wakeRef.current = !document.hidden; sync(); };
    document.addEventListener("visibilitychange", onVisibility);
    wakeRef.current = !document.hidden;

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onCalm = (): void => {
      calmRef.current = mq.matches;
      // Der Takt selbst wechselt: stoppen und über `sync` im richtigen Modus neu starten.
      stop();
      sync();
    };
    calmRef.current = mq.matches;
    mq.addEventListener("change", onCalm);

    // Zoom am Mausrad. Ein eigener Listener statt `onWheel`, weil React den Wurzel-Listener
    // passiv anmeldet und `preventDefault` dort wirkungslos bliebe — die Seite scrollte dann
    // unter der Bühne weg.
    const onWheel = (e: WheelEvent): void => {
      e.preventDefault();
      zoomAt(e.deltaY < 0 ? 1 : -1, e.clientX, e.clientY);
    };
    cvs.addEventListener("wheel", onWheel, { passive: false });

    prevNowRef.current = performance.now();
    sync();

    return () => {
      stop();
      ro.disconnect();
      io.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      mq.removeEventListener("change", onCalm);
      cvs.removeEventListener("wheel", onWheel);
      if (hoverTimerRef.current !== null) {
        window.clearTimeout(hoverTimerRef.current);
        hoverTimerRef.current = null;
      }
    };
    // Absichtlich leer: alles, was diese Schleife braucht, liest sie aus Refs. Eine
    // Abhängigkeit hier wäre ein Neustart der Schleife mitten im Bild.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Zeiger → Puffer ────────────────────────────────────────────────────────────────────────
  //
  // Nur die Bühne kennt den Blit-Faktor, also rechnet sie die Zeigerposition um; `hitTest`
  // erwartet Pufferkoordinaten und macht die Kameraumkehr selbst.
  const toBuffer = useCallback((clientX: number, clientY: number) => {
    const cvs = canvasRef.current;
    if (!cvs) return null;
    const r = cvs.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    const b = blitRef.current;
    const ratio = cvs.width / r.width;
    return {
      x: ((clientX - r.left) * ratio - b.ox) / b.scale,
      y: ((clientY - r.top) * ratio - b.oy) / b.scale,
    };
  }, []);

  const pick = useCallback((clientX: number, clientY: number): string | undefined => {
    const f = frameRef.current;
    const pt = toBuffer(clientX, clientY);
    if (!f || !pt) return undefined;
    return hitTest(f, hitCamRef.current, pt.x, pt.y);
  }, [toBuffer]);

  /** Meldet den Treffer unter dem Zeiger — aber nur, wenn er sich geändert hat. */
  const reportHover = useCallback(() => {
    const pt = hoverPtRef.current;
    const f = frameRef.current;
    const id = pt && f ? hitTest(f, hitCamRef.current, pt.x, pt.y) : undefined;
    if (id === hoverSentRef.current) return;
    hoverSentRef.current = id;
    onHoverRef.current(id);
  }, []);

  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    hoverPtRef.current = toBuffer(e.clientX, e.clientY);
    // Vorn und hinten im Fenster: der erste Zug meldet sofort, der letzte des Fensters kommt
    // am Ende nach. Ohne den Nachzügler bliebe ein Zeiger, der zwischen zwei Fenstern stehen
    // bleibt, auf dem alten Treffer sitzen.
    if (hoverTimerRef.current !== null) return;
    reportHover();
    hoverTimerRef.current = window.setTimeout(() => {
      hoverTimerRef.current = null;
      reportHover();
    }, HOVER_MS);
  }, [reportHover, toBuffer]);

  const onPointerLeave = useCallback(() => {
    hoverPtRef.current = null;
    if (hoverTimerRef.current !== null) {
      window.clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    reportHover();
  }, [reportHover]);

  const onClick = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    // Der Klick holt den Fokus her, sonst gingen die Kameratasten ins Leere.
    hostRef.current?.focus();
    onSelectRef.current(pick(e.clientX, e.clientY));
  }, [pick]);

  // ── Zoom ───────────────────────────────────────────────────────────────────────────────────
  //
  // Ganzzahlig und ankerbewahrend: der Punkt unter dem Zeiger bleibt liegen. Die Rechnung ist
  // die Umkehrung von `camFit` — aus `screen = PIX.w/2 + (b - cam) * z` folgt für gleiches
  // `screen` bei neuem `z'`: `cam' = b - (b - cam) * z / z'`.
  const zoomAt = useCallback((dir: number, clientX?: number, clientY?: number) => {
    const c = camRef.current;
    const z0 = c.zoom;
    const z1 = Math.min(MAX_ZOOM, Math.max(1, z0 + dir));
    if (z1 === z0) return;
    const anchor = clientX !== undefined && clientY !== undefined
      ? toBuffer(clientX, clientY) : null;
    // Ohne Zeiger (Tastatur) ist der Anker die Bildmitte — also die Kamera selbst, und dann
    // bleibt sie einfach stehen.
    const ax = anchor ? c.x + (anchor.x - PIX.w / 2) / z0 : c.x;
    const ay = anchor ? c.y + (anchor.y - PIX.h / 2) / z0 : c.y;
    c.zoom = z1;
    c.x = ax - (ax - c.x) * z0 / z1;
    c.y = ay - (ay - c.y) * z0 / z1;
    c.wantX = c.x;
    c.wantY = c.y;
    clampCam(c);
    dirtyRef.current = true;
  }, [toBuffer]);

  // ── Tastatur ───────────────────────────────────────────────────────────────────────────────
  //
  // **Nur Kamera, nur bei Fokus.** Die globale Tastaturkarte (Auswahl, Abspielen, Zeitleiste)
  // gehört der Ansicht darüber; ein `window`-Listener hier würde ihr in die Quere kommen und
  // wäre außerdem in jedem Textfeld der Seite aktiv.
  const onKeyDown = useCallback((e: ReactKeyboardEvent<HTMLDivElement>) => {
    const c = camRef.current;
    const pan = (dx: number, dy: number): void => {
      c.wantX += dx / c.zoom;
      c.wantY += dy / c.zoom;
      clampCam(c);
      dirtyRef.current = true;
      e.preventDefault();
    };
    switch (e.key) {
      case "ArrowLeft": if (e.altKey) pan(-PAN_STEP, 0); return;
      case "ArrowRight": if (e.altKey) pan(PAN_STEP, 0); return;
      case "ArrowUp": if (e.altKey) pan(0, -PAN_STEP); return;
      case "ArrowDown": if (e.altKey) pan(0, PAN_STEP); return;
      case "+": case "=": zoomAt(1); e.preventDefault(); return;
      case "-": case "_": zoomAt(-1); e.preventDefault(); return;
      case "0": case "Home":
        c.zoom = 1;
        c.x = CAM_FULL.x; c.y = CAM_FULL.y; c.wantX = CAM_FULL.x; c.wantY = CAM_FULL.y;
        clampCam(c);
        dirtyRef.current = true;
        e.preventDefault();
        return;
      default:
    }
  }, [zoomAt]);

  // ── Was React sieht ────────────────────────────────────────────────────────────────────────
  //
  // Diese drei Werte werden im Rendertakt gebildet, nicht im Bildtakt: `revision` steigt
  // höchstens fünfmal je Sekunde, Auswahl und Überfahren im Menschentempo.
  const empty = useMemo(() => {
    const b = recorder.bounds();
    // Der echte `Recorder` gibt bei leerem Log Nullen zurück, die Schnittstelle erlaubt `null`.
    // Beides heißt dasselbe: es ist noch nichts passiert.
    return !b || (b.t1 === 0 && b.seq1 === 0);
  }, [recorder, revision]);

  const selText = tagOf(frameRef.current, selected);
  const hovText = hover !== selected ? tagOf(frameRef.current, hover) : undefined;
  const roomCount = frameRef.current?.actors.filter((a) => a.retired !== true).length ?? 0;

  return (
    <div
      ref={hostRef}
      className={`relative overflow-hidden bg-surface outline-none ${className ?? ""}`}
      tabIndex={0}
      role="group"
      aria-label="Büro-Bühne. Alt und Pfeiltasten schwenken, Plus und Minus zoomen, Pos1 zentriert."
      onKeyDown={onKeyDown}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      onClick={onClick}
    >
      <canvas
        ref={canvasRef}
        className="block h-full w-full"
        role="img"
        aria-label={
          empty
            ? "Leeres Büro — noch kein Agentenlauf."
            : `Pixel-Büro mit ${roomCount} ${roomCount === 1 ? "Agent" : "Agenten"} im Raum.`
        }
      />

      {/* Namensschilder als echte Spans: markierbar und vorlesbar. Ihre Lage setzt die
          Schleife imperativ, ihr Text kommt von React — ein wandernder Agent kostet damit
          keinen Renderdurchlauf. */}
      {selText !== undefined && (
        <span
          ref={selTagRef}
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full whitespace-nowrap
                     rounded border border-line bg-card/90 px-1.5 py-0.5 text-[11px] font-medium text-ink
                     shadow-sm"
          style={{ visibility: "hidden" }}
        >
          {selText}
        </span>
      )}
      {hovText !== undefined && (
        <span
          ref={hovTagRef}
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full whitespace-nowrap
                     rounded border border-line bg-card/75 px-1.5 py-0.5 text-[11px] text-muted"
          style={{ visibility: "hidden" }}
        >
          {hovText}
        </span>
      )}

      {/* Leerer Zustand: ein ruhiger, leerer Raum und ein Satz. Kein Spinner, keine
          Fehlermeldung — es ist ja nichts kaputt, es ist nur nichts passiert. */}
      {empty && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded border border-line bg-card/90 px-3 py-2 text-sm text-muted">
            Noch kein Agentenlauf in diesem Projekt.
          </p>
        </div>
      )}
    </div>
  );
}

/** Beschriftung eines Schildes: Rolle und, wenn bekannt, Ticket oder Modell. Ausgeschrieben —
 *  anders als auf dem gemalten Schild im Canvas, das auf neun Zeichen kürzen muss. */
function tagOf(f: Frame | null, id: string | undefined): string | undefined {
  if (!f || id === undefined) return undefined;
  for (const a of f.actors) {
    if (a.id !== id) continue;
    // Eine Figur, deren `ensureActor` noch ohne Rolle kam, hätte sonst ein leeres Schild —
    // die Id (`run:8871`) sagt in dem Fall mehr als nichts.
    const name = a.role !== "" ? a.role : a.id;
    const extra = a.issue ?? a.model ?? undefined;
    return extra ? `${name} · ${extra}` : name;
  }
  return undefined;
}
