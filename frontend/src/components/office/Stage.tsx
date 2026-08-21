// Layer 2, the stage. The window into the room, and the only place where simulation time
// turns into visible pixels.
//
// ══ The rule this feature stands or falls on ════════════════════════════════════════════════
//
// **The stage never renders per frame.** Not a single React state is touched in the rAF path;
// angefasst. Alles Bewegliche lebt in Refs: Replay, Kamera, Frame, rAF-Handle, Zeiger,
// mirrored props. React only sees what changes at human pace: selection, hover, `revision`
// (throttled to 5/s in the feed), theme.
//
// The reason is not elegance but measurability: the project tab runs next to a query that
// refetches every 8 seconds, in tabs people leave open for hours. One `setState` per frame
// would be 60 render passes of the parent per second, and the parent holds the dock, the
// inspector and the timeline.
//
// ══ The three effects, and the fourth that is none ═════════════════════════════════════════
//
//   · replay from the log   `[revision]`  — the one number staleness check
//   · seek                  `[seekTs]`    — rewind or live
//   · rAF loop              `[]`          — exactly once on mount
//
// They stand in the file in this order, and the order is deliberate: React runs effects in
// declaration order, so the replay already exists when the seek looks for it, and both are
// finished before the first frame is computed.
//
// Before them stands a fourth, tiny one: it mirrors `seekTs`, `speed`, `grade`, `selected`,
// `hover` and the two callbacks into refs and does nothing else. It is the exception that
// makes the other three lean: without it the loop would have to list `speed` among its
// dependencies and would restart mid frame on every change of speed.
//
// ══ The one documented exception to the pixel contract ═════════════════════════════════════
//
// The `drawImage` below (buffer to visible canvas) is the only place exempt from the drawing
// contract, and it lives here by rule 4 of PIXEL-CONTRACT.md. Nothing else in this file draws:
// `renderFrame` paints into the 480×270 buffer, and this file blits it.
// ihn ganzzahlig hoch.
//
// ══ Integer in the backing store, fitted with CSS ══════════════════════════════════════════
//
// Two separate sizes, and confusing them is the expensive mistake:
//
//   · **Backing store** (`canvas.width/height`) = `480·s × 270·s` with an integer `s`. Rule 1
//     applies only here: a blit at factor 1.5 would run over half columns and turn pixel art into mush.
//     Matsch.
//   · **CSS area** (`canvas.style.width/height`) = the largest 16:9 rectangle that fits into
//     the container. 480×270 **is** 16:9, so nothing is distorted, and one direction always
//     fills completely. The browser scales up to that area, hard pixelated.
//     (`.pixel-canvas`, `image-rendering: pixelated`).
//
// Before, both were done in one step: blit at an integer factor **and** leave the rest as a
// letterbox. On 1920×1080 that gave factor 3 instead of 3.76, so wide empty areas all around,
// on the wall screen of all places, where the area is the whole point.
//
// What hangs on it: **hit testing**. The pointer position arrives in CSS pixels, `hitTest`
// wants buffer pixels, and there are now **two** factors in between (CSS to backing store to
// buffer), not just the blit factor. Forget one of them and you select a character too far to
// the right. `toBuffer` therefore consistently computes over `getBoundingClientRect()`.

import { tr } from "../../i18n";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent } from "react";
import type { ActorState, Frame, Grade } from "./types.ts";
import { ART, LINK_MS, MAX_FRAME_MS, PIX, POS_SCALE } from "./const.ts";
import type { KioskCam } from "./kiosk.ts";
import { newKioskCam, pickTarget } from "./kiosk.ts";
import { Replay } from "./replay.ts";
import type { RecorderApi } from "./useOfficeFeed.ts";
import { GRADES } from "./pixel/palette.ts";
import type { Pal } from "./pixel/palette.ts";
import { FIG_H } from "./pixel/person.ts";
import type { Cam } from "./pixel/scene.ts";
import { CAM_FULL, actorAt, hitTest, renderFrame } from "./pixel/scene.ts";

// ═══ Stellschrauben ══════════════════════════════════════════════════════════════════════════

/** Highest zoom. At 4 the buffer still shows 120×67 pixels; below that you see single sprites
 *  and nothing else, and the view loses exactly what it exists for. */
const MAX_ZOOM = 4;

/** Cap for the integer backing store factor. At 8 the canvas is 3840×2160, exactly a 4K screen
 *  at `devicePixelRatio = 1`. Above that only memory grows: what the browser scales up from
 *  the backing store does not look sharper beyond this fineness, because the
 *  Quelle 480×270 bleibt. */
const MAX_BLIT = 8;

/** Time constant of the camera movement. `dt/220` means the remaining distance is evened out
 *  once after about 220 ms: fast enough to follow, slow enough not to twitch. */
const CAM_EASE_MS = 220;

/** One key press pans this many buffer pixels. */
const PAN_STEP = 24;

/** At most four hover reports per second to React. Without throttling the parent renders on
 *  every mouse move, and it holds the dock and the inspector. */
const HOVER_MS = 250;

/** Beat of the loop under `prefers-reduced-motion`. No rAF: two frames per second are enough
 *  to show changes of state, and somebody with that setting wants no more. */
const CALM_TICK_MS = 500;

/** If playback in live mode lags further behind the newest event than this, it catches up
 *  instead of trailing. Happens after every tab that was paused. */
const LIVE_CATCHUP_MS = 4000;

/** The frozen animation clock of the calm mode (see `calmFrame`). */
const CALM_CLOCK = 0;

/** This old the calm mode pretends every bubble to be, so that `revealOf` stands at 1 and the
 *  text is **fully** there instead of typing itself in. */
const CALM_SETTLED_MS = 600_000;

// ═══ Interface ═══════════════════════════════════════════════════════════════════════════════

export interface StageProps {
  /** The log. The stage only reads (`entries`), it never writes into it. */
  recorder: RecorderApi;
  /** The one number staleness check of the recorder. Only the replay comparison hangs on it. */
  revision: number;
  /** `null` means live. Otherwise the moment the timeline rewound to. */
  seekTs: number | null;
  /** Factor on the passing time. `0` is paused, `1` is real time. */
  speed: number;
  /** Day or evening office. Comes from `useTheme`, not from the clock. */
  grade: Grade;
  /** Selected agent (brighter ring, name plate, camera follow). */
  selected?: string;
  /** Agent under the pointer. */
  hover?: string;
  /** Agents the session tab does not mean right now: they are drawn pale.
   *  `undefined` means no filter is active. Deliberately no removal: see `TopBar.tsx`, point 2. */
  dimmed?: ReadonlySet<string>;
  /** Wall screen mode: the camera picks its target itself (`kiosk.ts`), keyboard, wheel and
   *  hover are off. The only thing this file needs from outside for it; everything else
   *  (frame, header, changing rooms) belongs to the view above. */
  kiosk?: boolean;
  onSelect(id: string | undefined): void;
  onHover(id: string | undefined): void;
  className?: string;
}

// ═══ Kamera ══════════════════════════════════════════════════════════════════════════════════

/** The lived camera state: `x/y` is where it stands, `wantX/wantY` where it should go. The
 *  separation is the whole reason a pan looks smooth and a jump still arrives instantly (both
 *  are then set together). */
interface CamState { x: number; y: number; zoom: number; wantX: number; wantY: number }

/**
 * Keeps the camera inside the room.
 *
 * At an integer `zoom` the buffer shows `PIX.w/z × PIX.h/z` pixels around `x/y`. If `x` were
 * allowed closer to the edge than half the view width, the view would run past the wall, and
 * because `renderFrame` deliberately does **not** clear the buffer (wall and floor cover it),
 * the previous frame would stand there instead of nothing. The clamp is therefore not cosmetic
 * but the promise the non clearing rests on.
 */
function clampCam(c: CamState): void {
  // Half the view width comes from the BUFFER (this many art units it shows at this scale), the
  // bound from the ART LAYER (this far the room reaches). Both were the same while ART_SCALE = 1
  // held; since the buffer is twice as fine they are two things.
  const halfW = PIX.w / (2 * c.zoom);
  const halfH = PIX.h / (2 * c.zoom);
  c.x = Math.min(ART.w - halfW, Math.max(halfW, c.x));
  c.y = Math.min(ART.h - halfH, Math.max(halfH, c.y));
  c.wantX = Math.min(ART.w - halfW, Math.max(halfW, c.wantX));
  c.wantY = Math.min(ART.h - halfH, Math.max(halfH, c.wantY));
}

/** Where a buffer point lands under this camera, the same computation as `camFit` in
 *  `pixel/scene.ts`, which is private there. The duplication is deliberate and small: it is
 *  needed for the DOM name plates, and an export just for that would widen the drawing layer
 *  by an interface nobody else needs. */
function camOffset(cam: Cam): { z: number; ox: number; oy: number } {
  const z = Math.max(1, Math.round(cam.zoom));
  return { z, ox: Math.round(PIX.w / 2 - cam.x * z), oy: Math.round(PIX.h / 2 - cam.y * z) };
}

// ═══ Ruhemodus ═══════════════════════════════════════════════════════════════════════════════

/**
 * The same room, only without movement, for `prefers-reduced-motion`.
 *
 * **Reduced motion does not mean "slower" but "less".** A halved frame rate would be no relief
 * for somebody sensitive to motion but the same juddering in slow motion. So nothing is slowed
 * here, the **animation clock is frozen**: the frame goes into the drawing layer with
 * `t = CALM_CLOCK`, and with that the walk cycle, blinking, dust, steam and the dots of the
 * thought bubble stand still. Visibly only what really changed changes: who sits where, who
 * says what, which monitor shows what.
 *
 * The simulation keeps running **normally** (`Replay.advance` at the calm beat). Stopping it
 * would mean lying about the room; what is frozen is the display, not the time.
 *
 * Three rewrites are needed so the frozen `t` breaks nothing:
 *
 *   · `pose: "walk" → "stand"`, no walk cycle and no dust under the feet. The character then
 *     stands at its current place and is at its target after `SETTLE_MS` anyway.
 *   · `sayAt`/`thinkAt` far into the past, so `revealOf` stands at 1 and the bubble appears
 *     **whole** instead of typing itself in character by character.
 *   · `link.until` at `CALM_CLOCK + LINK_MS`, so the band between parent and child run has age
 *     0 and stands fully there instead of fading out under a frozen clock.
 *
 * The effects in the air (spark, falling note, emote pop) fall away entirely: they are pure
 * particles and carry no information that is not also on the monitor or at the bubble's edge.
 *
 * Copying is shallow and only where something has to change: the `ActorState` from `Frame`
 * belong to the engine and must **never** be modified (`Engine.frame()` hands out the objects
 * themselves, only the list is a copy).
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
  // The order stays the engine's (sorted by `y`), because the painter's algorithm of the
  // drawing layer depends on it.
  // The server rack stays as it is: it is not a particle but a state of the room.
  // `since` is pulled to `CALM_CLOCK`: with a frozen clock a rising bar would otherwise stand
  // at an arbitrary phase of the last real moment.
  return { t: CALM_CLOCK, actors, fx: [], rack: { ...f.rack, since: CALM_CLOCK } };
}

// ═══ The stage ═══════════════════════════════════════════════════════════════════════════════

export default function Stage(props: StageProps): JSX.Element {
  const {
    recorder, revision, seekTs, speed, grade, selected, hover, dimmed, kiosk, onSelect, onHover,
    className,
  } = props;

  // ── DOM ────────────────────────────────────────────────────────────────────────────────────
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const selTagRef = useRef<HTMLSpanElement | null>(null);
  const hovTagRef = useRef<HTMLSpanElement | null>(null);
  const midTagRef = useRef<HTMLSpanElement | null>(null);

  // ── Alles Bewegliche ───────────────────────────────────────────────────────────────────────
  const replayRef = useRef<Replay | null>(null);
  const frameRef = useRef<Frame | null>(null);
  const camRef = useRef<CamState>({
    x: CAM_FULL.x, y: CAM_FULL.y, zoom: CAM_FULL.zoom, wantX: CAM_FULL.x, wantY: CAM_FULL.y,
  });
  /** The camera the **last painted** frame came about with. The hit test has to compute against
   *  it and not against the current one: between two frames the camera has already moved on a
   *  little, and a click would otherwise land just beside the target. */
  const hitCamRef = useRef<Cam>({ ...CAM_FULL });
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const prevNowRef = useRef(0);
  const dirtyRef = useRef(true);

  /** The geometry of the stage, computed once per resize.
   *
   *  · `scale` — integer factor of the **backing store**: `canvas.width === PIX.w * scale`.
   *  · `unit`  — **CSS** pixels per buffer pixel. That is the factor the DOM name plates
   *              compute with, and it is generally fractional (which is the whole point).
   *  · `offX/offY` — position of the canvas **inside the host**, in CSS pixels. The stage is
   *              fitted centred; the plates live in the host, not in the canvas. */
  const blitRef = useRef({ scale: 1, unit: 1, offX: 0, offY: 0 });

  // ── Calm states ────────────────────────────────────────────────────────────────────────────
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
  const dimRef = useRef<ReadonlySet<string> | undefined>(dimmed);
  const kioskRef = useRef(kiosk === true);
  const onSelectRef = useRef(onSelect);
  const onHoverRef = useRef(onHover);

  // ── Kiosk ──────────────────────────────────────────────────────────────────────────────────
  //
  // The camera state of the wall screen lives in a ref, not in React state: a target in state
  // would cost a render pass **per camera movement**, and this whole file is built around never
  // doing that. The choice itself is made by `kiosk.ts` (layer 0, testably pure); here stands
  // only what it does with the camera.
  const kioskStRef = useRef<KioskCam>(newKioskCam());
  /** Last written label of the centre of the image: the DOM is touched only on a real change,
   *  not sixty times a second. */
  const midTextRef = useRef<string>("");

  // ── Zeiger ─────────────────────────────────────────────────────────────────────────────────
  const hoverPtRef = useRef<{ x: number; y: number } | null>(null);
  const hoverTimerRef = useRef<number | null>(null);
  const hoverSentRef = useRef<string | undefined>(undefined);

  // ── Effect 4: the mirrors ──────────────────────────────────────────────────────────────────
  //
  // Tiny and without a dependency list: it does nothing but assign. Exactly for that it may run
  // on every render, and exactly for that a change of speed does not restart the loop.
  useEffect(() => {
    seekRef.current = seekTs;
    speedRef.current = speed;
    if (gradeRef.current !== grade) {
      gradeRef.current = grade;
      palRef.current = GRADES[grade];
      // The border around the fitted stage lies in the host, so it changes colour here as well:
      // `layout()` does not run on a theme change, because no size changes.
      if (hostRef.current) hostRef.current.style.backgroundColor = palRef.current.wallLo;
    }
    selRef.current = selected;
    hovRef.current = hover;
    dimRef.current = dimmed;
    if (kioskRef.current !== (kiosk === true)) {
      kioskRef.current = kiosk === true;
      // The label of the image centre is a different DOM element as soon as the kiosk is
      // switched on or off. Without resetting, the marker would hold a text that does not stand
      // in the new (empty) element at all, and the line would stay hidden forever.
      midTextRef.current = "";
    }
    onSelectRef.current = onSelect;
    onHoverRef.current = onHover;
    dirtyRef.current = true;
  });

  // ── Effect 2: the replay ───────────────────────────────────────────────────────────────────
  //
  // `revision` is the one number staleness check: it moves on every growth **and** on every
  // drop at the head. `Replay.extend` on pure growth only advances the log pointer (no rebuild,
  // no three hours of ticks) and honestly rebuilds only when the beginning already played is
  // not the same any more. That covers the drop case, so a second check on `bounds().dropped`
  // here would be a second truth about
  // denselben Sachverhalt.
  useEffect(() => {
    const log = recorder.entries();
    let rp = replayRef.current;
    if (rp === null) { rp = new Replay(log); replayRef.current = rp; }
    else rp.extend(log);
    // Follow up, because `extend` rebuilds in the drop case: a different room stands there then,
    // and at `speed === 0` the loop would never get round to fetching it.
    frameRef.current = rp.frame();
    dirtyRef.current = true;
  }, [recorder, revision]);

  // ── Effect 3: rewind or live ───────────────────────────────────────────────────────────────
  //
  // A `seek` is expensive (new engine, log from the start) and therefore happens **only** here:
  // when the user touches the timeline. The loop never seeks, it only runs on.
  useEffect(() => {
    const rp = replayRef.current;
    if (rp === null) return;
    if (seekTs === null) rp.toLive();
    else rp.seek(seekTs);
    frameRef.current = rp.frame();
    dirtyRef.current = true;
  }, [seekTs]);

  // ── Effect 1: the loop ─────────────────────────────────────────────────────────────────────
  useEffect(() => {
    const host = hostRef.current;
    const cvs = canvasRef.current;
    if (!host || !cvs) return;

    // Create the buffer. `OffscreenCanvas` saves a DOM element; where it is missing a detached
    // `<canvas>` does exactly the same. Two branches instead of one union call, because
    // `getContext` has different overloads on the two types.
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
    // Buffer and contexts stay **local** to this loop: they are created with it and thrown away
    // with it. A ref on them would be a second lifetime for the same object. `Ctx` (the allowed
    // subset fillStyle/globalAlpha/fillRect) is satisfied structurally by a real 2D context, and
    // the drawing layer sees nothing else.

    // ── Size ─────────────────────────────────────────────────────────────────────────────────
    const layout = (): void => {
      const r = host.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const hostW = Math.max(1, r.width);
      const hostH = Math.max(1, r.height);

      // ── Einpassen (CSS) ───────────────────────────────────────────────────────────────────
      // The largest 16:9 rectangle in the container. Because 480×270 is itself 16:9 one common
      // factor is enough: there is no distortion to prevent, only one direction that fills
      // completely and one that shares the rest.
      const fit = Math.min(hostW / PIX.w, hostH / PIX.h);
      const cssW = Math.max(1, Math.round(PIX.w * fit));
      const cssH = Math.max(1, Math.round(PIX.h * fit));
      const offX = Math.round((hostW - cssW) / 2);
      const offY = Math.round((hostH - cssH) / 2);

      // ── Drawing (backing store) ───────────────────────────────────────────────────────────
      // Integer, otherwise pixel art turns to mush: a line one pixel wide would run over two
      // columns at half opacity at factor 1.5. **Rounded down**, never up: a backing store that
      // is too large would have to be shrunk by the browser, and `pixelated` throws away whole
      // source rows while doing so, so an edge one pixel wide would simply disappear. Scaling
      // up loses nothing, single pixels only become differently wide.
      const scale = Math.max(1, Math.min(MAX_BLIT, Math.floor(cssW * dpr / PIX.w)));
      const bw = PIX.w * scale;
      const bh = PIX.h * scale;
      if (cvs.width !== bw || cvs.height !== bh) { cvs.width = bw; cvs.height = bh; }

      cvs.style.width = `${cssW}px`;
      cvs.style.height = `${cssH}px`;
      cvs.style.left = `${offX}px`;
      cvs.style.top = `${offY}px`;
      // The border around the fitted stage belongs to the room, not to the application, hence a
      // palette colour and not `bg-surface`. The canvas used to paint it itself; now it lies
      // outside, so the host carries it.
      host.style.backgroundColor = palRef.current.wallLo;

      blitRef.current = { scale, unit: cssW / PIX.w, offX, offY };
      // A resize resets the context state, so smoothing has to be switched off again afterwards.
      vctx.imageSmoothingEnabled = false;
      dirtyRef.current = true;
    };
    layout();

    // ── Hanging a plate on its character ─────────────────────────────────────────────────────
    //
    // The plates are real `<span>` above the canvas, not painted pixels: they should be
    // selectable and a screen reader should read them. Only **the position** is set here (text
    // and existence belong to React), which is why a wandering agent costs no render pass.
    // Renderdurchlauf.
    const place = (el: HTMLSpanElement | null, id: string | undefined, f: Frame,
      cam: Cam): void => {
      if (!el) return;
      const p = id !== undefined ? actorAt(f, id) : undefined;
      if (!p) { el.style.visibility = "hidden"; return; }
      const { z, ox, oy } = camOffset(cam);
      const b = blitRef.current;
      // Buffer pixel to CSS pixel in the host: the fit factor plus the position of the canvas
      // in the host. The integer backing store factor does **not** appear here: it describes
      // how finely something is painted, not how large the image is on screen.
      const sx = b.offX + (p.x * z + ox) * b.unit;
      const sy = b.offY + (p.y * z + oy) * b.unit;
      // Above the head, centred. `translate(-50%, -100%)` sits in the element's style.
      el.style.left = `${Math.round(sx)}px`;
      el.style.top = `${Math.round(sy - FIG_H * z * b.unit)}px`;
      // Stacked as in the canvas: whoever stands further down stands in front. So the plate of
      // a character in front covers the one behind it, and not the other way round.
      el.style.zIndex = String(Math.max(0, Math.round(sy)));
      el.style.visibility = "visible";
    };

    // ── One frame ────────────────────────────────────────────────────────────────────────────
    const paint = (): boolean => {
      const f = frameRef.current;
      if (!f) return false;
      const c = camRef.current;
      const cam: Cam = { x: c.x, y: c.y, zoom: c.zoom };
      hitCamRef.current = cam;

      renderFrame(bctx, calmRef.current ? calmFrame(f) : f, cam, gradeRef.current, {
        selected: selRef.current,
        hover: hovRef.current,
        dimmed: dimRef.current,
      });

      // ── The one exempt place (PIXEL-CONTRACT.md rule 4) ───────────────────────────────────
      // The backing store is by construction exactly `PIX × scale`, so the blit covers it
      // completely: no letterbox any more, no pre fill. The border around the fitted area is
      // carried by the host (see `layout`).
      const b = blitRef.current;
      vctx.imageSmoothingEnabled = false;
      vctx.drawImage(buf, 0, 0, PIX.w, PIX.h, 0, 0, PIX.w * b.scale, PIX.h * b.scale);

      place(selTagRef.current, selRef.current, f, cam);
      place(hovTagRef.current, hovRef.current !== selRef.current ? hovRef.current : undefined,
        f, cam);
      if (kioskRef.current) midTag(f, cam);
      return true;
    };

    // ── Who stands in the centre of the image? ───────────────────────────────────────────────
    //
    // The wall screen should say who it currently shows, but **without selecting**: a set
    // `selected` would drag the bright ring, the DOM plate and the camera follow along with it,
    // and would be a render pass as well. `hitTest` on the image centre is existing code and
    // answers exactly the question asked here. The text is set imperatively, like the position
    // of the other plates.
    const midTag = (f: Frame, cam: Cam): void => {
      const el = midTagRef.current;
      if (!el) return;
      const text = tagOf(f, hitTest(f, cam, PIX.w / 2, PIX.h / 2)) ?? "";
      if (text === midTextRef.current) return;
      midTextRef.current = text;
      el.textContent = text;
      el.style.visibility = text === "" ? "hidden" : "visible";
    };

    // ── Kamera je Bild ───────────────────────────────────────────────────────────────────────
    const moveCam = (dt: number, f: Frame | null): boolean => {
      const c = camRef.current;
      // Follow: only when zoomed, and only when the character walks out of the centre. Pulling
      // along on every step would take panning out of the user's hands.
      const sel = selRef.current;
      // In kiosk mode `kioskCam` steers the camera. Following a character selected at the same
      // time (by a touch of the wall screen, say) would be two hands on the same wheel.
      if (c.zoom > 1 && sel !== undefined && f && !kioskRef.current) {
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

    // ── Kiosk-Kamera je Bild ─────────────────────────────────────────────────────────────────
    //
    // Runs **inside this loop**, right before `moveCam`, so the existing easing
    // (`CAM_EASE_MS`) carries the pan in the same frame.
    //
    // `zoomAt` is deliberately **not** called: it sets `wantX = x` and would let the camera
    // jump. Instead `c.zoom` directly, then the target in `wantX/wantY`, then `clampCam`: the
    // zoom sits at once, the way there stays smooth.
    //
    // **Calm mode (`prefers-reduced-motion`) survives this**, and that is no accident:
    // `calmFrame` throws `fx` away, but it is applied only in `paint`. Here stands
    // `frameRef.current`, the **real** frame including the effect stream, so the fx camera works
    // there too. Whoever moves `calmFrame` forward one day blinds the wall screen.
    const kioskCam = (f: Frame | null): boolean => {
      if (!kioskRef.current || !f) return false;
      const tgt = pickTarget(f, kioskStRef.current);
      if (tgt === null) return false;
      const c = camRef.current;
      // `Fx.x/y` are scene coordinates (1600×900), the camera computes in buffer pixels.
      c.zoom = kioskStRef.current.zoom;
      c.wantX = Math.round(tgt.x * POS_SCALE);
      c.wantY = Math.round(tgt.y * POS_SCALE);
      clampCam(c);
      return true;
    };

    // ── The beat ─────────────────────────────────────────────────────────────────────────────
    const step = (): void => {
      const now = performance.now();
      // Clamped on both sides. A tab that lay in the background would otherwise bring minutes
      // in a single `dt`, and clamping is the loop's business, not the engine's: `Replay`
      // deliberately does not clamp `dtMs`, because that would break the dt split invariant.
      //
      // Different in calm mode: the beat there is `CALM_TICK_MS` (500 ms), and with the rAF cap
      // of 100 ms the room would run at a fifth of real time, so playback would fall further
      // and further behind. The cap therefore has to lie above the own beat; the engine splits
      // the span into `LIVE_STEP_MS` steps itself anyway.
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
            // Catch up instead of trailing: after a paused tab playback lags by the whole
            // pause, and nobody wants to watch the past being replayed when they chose live.
            if (rp.to - rp.position > LIVE_CATCHUP_MS) rp.toLive();
          } else {
            // Rewound: time sits on `seekTs` and runs on from there as soon as playback
            // resumes. Paused (`speed === 0`) it simply stands.
            rp.advance(dt * sp);
          }
          moved = true;
        }
        if (moved || frameRef.current === null) frameRef.current = rp.frame();
      }

      // Nothing moved, camera still, nothing requested, so nothing is painted either. At
      // `speed === 0` that is the normal case, and a paused image should not cost 60 full
      // frames per second. The marker only falls when something really was painted.
      if (kioskCam(frameRef.current)) dirtyRef.current = true;
      const cammoved = moveCam(dt, frameRef.current);
      if ((moved || cammoved || dirtyRef.current) && paint()) dirtyRef.current = false;
    };

    // ── Start and stop ───────────────────────────────────────────────────────────────────────
    const tickRaf = (): void => {
      rafRef.current = window.requestAnimationFrame(tickRaf);
      step();
    };

    const stop = (): void => {
      if (rafRef.current !== null) { window.cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      if (timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = null; }
    };

    /** The only place that decides whether anything is computed at all. Pausing is not thrift
     *  but the fact that this view lives in tabs people leave open for days. */
    const sync = (): void => {
      const want = shownRef.current && wakeRef.current;
      const running = rafRef.current !== null || timerRef.current !== null;
      if (want === running) return;
      if (!want) { stop(); return; }
      // Reset the clock on waking, otherwise the first `dt` would be the whole pause.
      prevNowRef.current = performance.now();
      dirtyRef.current = true;
      const rp = replayRef.current;
      if (rp && seekRef.current === null && speedRef.current > 0) rp.toLive();
      if (calmRef.current) {
        // Once right away, otherwise nothing at all would stand there for half a second after mounting.
        step();
        timerRef.current = window.setInterval(step, CALM_TICK_MS);
      } else {
        tickRaf();
      }
    };

    // ── Beobachter ───────────────────────────────────────────────────────────────────────────
    const ro = new ResizeObserver(layout);
    ro.observe(host);

    // Outside the viewport nothing is computed. That is the normal case in a project tab where
    // somebody scrolled down.
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
      // The beat itself changes: stop and start again in the right mode through `sync`.
      stop();
      sync();
    };
    calmRef.current = mq.matches;
    mq.addEventListener("change", onCalm);

    // Zoom on the mouse wheel. A listener of its own instead of `onWheel`, because React
    // registers the root listener passively and `preventDefault` would have no effect there,
    // so the page would scroll away under the stage.
    const onWheel = (e: WheelEvent): void => {
      // In kiosk mode the camera belongs to the room. The `preventDefault` falls away as well:
      // the page does not scroll there anyway, and a silent catcher on a wall screen is only a
      // place where somebody looks for a bug later.
      if (kioskRef.current) return;
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
    // Deliberately empty: everything this loop needs it reads from refs. A dependency here
    // would restart the loop mid frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Zeiger → Puffer ────────────────────────────────────────────────────────────────────────
  //
  // Only the stage knows its geometry, so it converts the pointer position; `hitTest` expects
  // buffer coordinates and undoes the camera itself.
  //
  // **Two factors, not one.** Since the area is fitted by CSS the CSS size of the canvas is no
  // longer a multiple of 480×270, it depends on the container. The path therefore goes CSS
  // pixel to backing store (`canvas.width / rect.width`) to buffer pixel (`/ scale`). Both
  // factors come from the same measurement, so the computation still holds when something
  // shifted the size between `layout()` and the click.
  //
  // An offset no longer has to be subtracted: the blit covers the backing store completely, and
  // `rect` is the canvas itself. The border around the image lies outside and by itself yields
  // values outside 0..480, which `hitTest` answers as "hit nothing".
  const toBuffer = useCallback((clientX: number, clientY: number) => {
    const cvs = canvasRef.current;
    if (!cvs) return null;
    const r = cvs.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    const b = blitRef.current;
    return {
      x: (clientX - r.left) * (cvs.width / r.width) / b.scale,
      y: (clientY - r.top) * (cvs.height / r.height) / b.scale,
    };
  }, []);

  const pick = useCallback((clientX: number, clientY: number): string | undefined => {
    const f = frameRef.current;
    const pt = toBuffer(clientX, clientY);
    if (!f || !pt) return undefined;
    return hitTest(f, hitCamRef.current, pt.x, pt.y);
  }, [toBuffer]);

  /** Reports the hit under the pointer, but only when it changed. */
  const reportHover = useCallback(() => {
    const pt = hoverPtRef.current;
    const f = frameRef.current;
    const id = pt && f ? hitTest(f, hitCamRef.current, pt.x, pt.y) : undefined;
    if (id === hoverSentRef.current) return;
    hoverSentRef.current = id;
    onHoverRef.current(id);
  }, []);

  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    // Do not even start in kiosk mode: the 250 ms throttle should never fire, otherwise a fly
    // on the touchscreen would wake dock and inspector every four seconds.
    if (kioskRef.current) return;
    hoverPtRef.current = toBuffer(e.clientX, e.clientY);
    // Leading and trailing inside the window: the first move reports at once, the last one of
    // the window follows at the end. Without the trailing call a pointer that comes to rest
    // between two windows would stay on the old hit.
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
    // The click fetches the focus, otherwise the camera keys would go nowhere.
    hostRef.current?.focus();
    onSelectRef.current(pick(e.clientX, e.clientY));
  }, [pick]);

  // ── Zoom ───────────────────────────────────────────────────────────────────────────────────
  //
  // Integer and anchor preserving: the point under the pointer stays where it is. The
  // computation is the inverse of `camFit`: from `screen = PIX.w/2 + (b - cam) * z` follows for
  // the same `screen` at a new `z'`: `cam' = b - (b - cam) * z / z'`.
  const zoomAt = useCallback((dir: number, clientX?: number, clientY?: number) => {
    const c = camRef.current;
    const z0 = c.zoom;
    const z1 = Math.min(MAX_ZOOM, Math.max(1, z0 + dir));
    if (z1 === z0) return;
    const anchor = clientX !== undefined && clientY !== undefined
      ? toBuffer(clientX, clientY) : null;
    // Without a pointer (keyboard) the anchor is the centre of the image, so the camera itself,
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
  // **Camera only, and only on focus.** The global keyboard map (selection, playback, timeline)
  // belongs to the view above; a `window` listener here would get in its way and
  // active inside every text field of the page.
  const onKeyDown = useCallback((e: ReactKeyboardEvent<HTMLDivElement>) => {
    // No stage keys in kiosk mode. The view above only lets `Escape` through there, and a pan
    // nobody can undo would stand until the next target.
    if (kioskRef.current) return;
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
  // These three values are formed at render pace, not at frame pace: `revision` rises at most
  // five times a second, selection and hover at human pace.
  const empty = useMemo(() => {
    const b = recorder.bounds();
    // The real `Recorder` returns zeros on an empty log, the interface allows `null`. Both mean
    // the same: nothing has happened yet.
    return !b || (b.t1 === 0 && b.seq1 === 0);
  }, [recorder, revision]);

  const selText = tagOf(frameRef.current, selected);
  const hovText = hover !== selected ? tagOf(frameRef.current, hover) : undefined;
  const roomCount = frameRef.current?.actors.filter((a) => a.retired !== true).length ?? 0;

  return (
    <div
      ref={hostRef}
      className={`relative overflow-hidden bg-surface outline-none ${className ?? ""}`}
      tabIndex={kiosk === true ? -1 : 0}
      role="group"
      aria-label={kiosk === true
        ? tr("stage.office_stage_wall_screen")
        : tr("stage.office_stage_alt_arrow")}
      onKeyDown={onKeyDown}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      onClick={onClick}
    >
      {/* Set absolutely, because size and position come out of `layout()`: the stage is the
          largest 16:9 rectangle in the host and sits in the middle of it. Taken out of the flow
          it cannot push the host apart — otherwise the `ResizeObserver` would pull itself up by
          its own hair.
          `.pixel-canvas` keeps the browser from smoothing while scaling up. */}
      <canvas
        ref={canvasRef}
        className="pixel-canvas absolute block"
        style={{ left: 0, top: 0 }}
        role="img"
        aria-label={
          empty
            ? tr("stage.empty_office_no_agent")
            : tr("stage.pixel_office_count_agents", { count: roomCount })
        }
      />

      {/* Name tags as real spans: selectable and readable aloud. The loop sets their position
          imperatively, their text comes from React — a wandering agent therefore costs no
          render pass. */}
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

      {/* The wall screen: who stands in the middle of the picture. Discreetly at the bottom,
          not over the head of the figure — from three metres one reads a fixed line, not a
          wandering sign. Text and
          visibility the loop sets imperatively, which is why nothing stands in here. */}
      {kiosk === true && (
        <span
          ref={midTagRef}
          className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 whitespace-nowrap
                     rounded border border-line bg-card/70 px-2 py-1 text-sm text-muted"
          style={{ visibility: "hidden" }}
          aria-live="off"
        />
      )}

      {/* The empty state: a quiet, empty room and one sentence. No spinner, no
          error message — nothing is broken after all, only nothing happened. */}
      {empty && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded border border-line bg-card/90 px-3 py-2 text-sm text-muted">
            {/* Deliberately without "in this project": the stage also stands on the
                cross-project page, and empty here only means "in the loaded
                window nothing happened" — not "there was never an agent here". */}
            {tr("stage.no_agent_run_see")}
          </p>
        </div>
      )}
    </div>
  );
}

/** Label of a plate: role and, when known, ticket or model. Written out, unlike the painted
 *  plate in the canvas, which has to truncate at nine characters. */
function tagOf(f: Frame | null, id: string | undefined): string | undefined {
  if (!f || id === undefined) return undefined;
  for (const a of f.actors) {
    if (a.id !== id) continue;
    // A character whose `ensureActor` arrived without a role would otherwise have an empty
    // plate, and the id (`run:8871`) says more than nothing in that case.
    const name = a.role !== "" ? a.role : a.id;
    const extra = a.issue ?? a.model ?? undefined;
    return extra ? `${name} · ${extra}` : name;
  }
  return undefined;
}
