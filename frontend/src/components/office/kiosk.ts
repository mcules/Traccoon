// Layer 0: the camera choice of the wall screen.
//
// ══ The insight this rests on ════════════════════════════════════════════════════════════════
//
// The camera does not need an identity but a **point**. And `Frame.fx` is already the complete
// stream of what happens in the room: every `tool` produces a `spark`, every `edit` a `drop`,
// every `spawn`/`deliver` a `link`, every `gate`/`done` an `emote`. The engine also keeps the
// list clean already: `tick` throws away everything whose `until` has passed.
//
// That is why the kiosk costs **no rebuilding of the engine**: no new field in `ActorState`, no
// `lastAct` that would have to be lifted out of `Priv`. Every such field would stand in the
// `Frame` and turn `golden.json` red, for information that is in the `fx` stream anyway.
//
// All times are `engine.t` (simulation time, not wall clock), all coordinates are **scene**
// coordinates (`SCENE`, 1600x900), exactly like `Fx.x/y`. Converting into buffer pixels
// (`POS_SCALE`) is the business of the stage and deliberately does not happen here: otherwise
// there would be two places holding the same scaling.

import type { Frame, Fx, FxKind, Pt } from "./types.ts";
import { KIOSK_HOLD_MS, KIOSK_IDLE_MS, SCENE } from "./const.ts";

/**
 * What the kiosk has to keep between two frames.
 *
 * `x`/`y`/`zoom` are the **target** of the camera, not its actual position: the stage moves
 * there with its existing easing. `pickedAt` carries the hold rule, `lastFxT0` is both a
 * debounce (an fx is never chosen twice) and an activity clock (the silence rule measures by
 * it).
 */
export interface KioskCam {
  x: number;
  y: number;
  zoom: number;
  pickedAt: number;
  lastFxT0: number;
}

/**
 * A fixed ranking, **no scoring system**. A scoring system would need weights, and nobody
 * could justify those: "an error counts 2.5 sparks" is an invented number. The order here on
 * the other hand is a statement about the room: `emote` (gate or completion) is the rarest and
 * most important, `link` (handover, spawn) the actual choreography, `drop` (written file) the
 * result, `spark` (tool step) the most everyday thing.
 */
const RANK: Record<FxKind, number> = { emote: 3, link: 2, drop: 1, spark: 0 };

/** The whole room: the centre of `SCENE`. Times `POS_SCALE` that is exactly `CAM_FULL`, but
 *  that stands in layer 1 and is rightly unreachable from here. */
const FULL: Pt = { x: SCENE.w / 2, y: SCENE.h / 2 };

/** Fresh kiosk state: the whole room, nothing seen yet.
 *
 *  `pickedAt` lies a full hold time in the past and `lastFxT0` **before** the zero point: the
 *  first choice should not wait six seconds for a room in which something is just starting,
 *  and an fx at `t0 === 0` is an fx like any other. */
export function newKioskCam(): KioskCam {
  return { x: FULL.x, y: FULL.y, zoom: 1, pickedAt: -KIOSK_HOLD_MS, lastFxT0: -1 };
}

/** Back to the whole room. Gives `null` when the camera already stands there; otherwise the
 *  kiosk would report a "change" in every frame and the stage would redraw continuously. */
function toFull(st: KioskCam, t: number): Pt | null {
  if (st.zoom === 1 && st.x === FULL.x && st.y === FULL.y) return null;
  st.x = FULL.x;
  st.y = FULL.y;
  st.zoom = 1;
  st.pickedAt = t;
  return { x: st.x, y: st.y };
}

/**
 * Where the kiosk camera should look, or `null` when everything stays as it is.
 *
 * `st` is **written on** in the process (hold and debounce state). That is not a hidden side
 * effect but the task: `st` is the camera state, and the return value only tells the stage
 * whether it has to take it over this time. `st.zoom` belongs to the result.
 *
 * The rules, in exactly this order:
 *
 *  1. **Empty room** (no figure with `retired !== true`): do not choose at all, whole room.
 *  2. **Hold**: a chosen target applies unchanged for `KIOSK_HOLD_MS`. Without that the
 *     camera would jitter between sparks twelve times a second.
 *  3. **Choose**: among all new `fx` by `RANK`, and within a kind the most recent `t0`.
 *  4. **Silence**: `KIOSK_IDLE_MS` without a single new fx means back to the whole room and
 *     staying there. That is the honest picture: nothing is happening, so one sees the whole
 *     silent room and not a random empty desk from close up.
 */
export function pickTarget(f: Frame, st: KioskCam): Pt | null {
  const t = f.t;

  // The simulation time can **jump backwards**: a seek on the timeline or a newly built
  // `Replay` (room change in the kiosk) starts from the front again. Then `t - pickedAt` and
  // `t - lastFxT0` are negative and the camera would hold still forever. Holding a target from
  // another timeline would be pointless anyway, so drop everything and choose again in the
  // same frame.
  if (t < st.pickedAt || t < st.lastFxT0) {
    st.pickedAt = t - KIOSK_HOLD_MS;
    st.lastFxT0 = -1;
  }

  // 1. Leerer Raum.
  let peopled = false;
  for (const a of f.actors) {
    if (a.retired !== true) { peopled = true; break; }
  }
  if (!peopled) return toFull(st, t);

  // One pass through the stream: the best candidate **and** the most recent `t0` seen.
  let best: Fx | undefined;
  let newest = st.lastFxT0;
  for (const fx of f.fx) {
    if (fx.t0 <= st.lastFxT0) continue;
    if (fx.t0 > newest) newest = fx.t0;
    if (best === undefined) { best = fx; continue; }
    const r = RANK[fx.kind];
    const rb = RANK[best.kind];
    if (r > rb || (r === rb && fx.t0 > best.t0)) best = fx;
  }
  // Written on during the hold as well: `lastFxT0` is the activity clock. Otherwise a hold of
  // six seconds would count as silence as soon as it happened to fall into the 20 second
  // window, and the camera would pull back out of a full room.
  st.lastFxT0 = newest;

  // 2. Halten.
  if (t - st.pickedAt < KIOSK_HOLD_MS) return null;

  if (best === undefined) {
    // 4. Stille.
    if (t - st.lastFxT0 >= KIOSK_IDLE_MS) return toFull(st, t);
    return null;
  }

  // 3. Choose. Zoom 2, not 3: the buffer then shows 240x135, so half the room; the neighbours
  // of the target stay in the picture and a handover line does not run out of it.
  st.x = best.x;
  st.y = best.y;
  st.zoom = 2;
  st.pickedAt = t;
  return { x: st.x, y: st.y };
}
