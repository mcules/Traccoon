// The film: events in, GIF bytes out.
//
// The wiring is short because every piece already exists: `Recorder` deduplicates and
// translates (`mapEvent`), `Replay` replays, `renderFrame` paints, `raster.mjs` clips and
// `gif.mjs` encodes. New is only **which** moments are painted, and that is decided by
// `cut.mjs`.
//
// Two rules this file carries and that are easily violated:
//
//  1. **One single `Replay` for the whole day.** `advance()` runs through the stretches that
//     are not shown as well; only that way do the figures of chapter 3 still sit where chapter
//     5 expects them. That is not expensive: `settle()` clamps every gap to `MAX_GAP_MS`, so a
//     skipped hour costs at most 20 s of simulation time.
//  2. **Never `frameAt` in the loop.** That builds a new engine every time and replays from
//     the front; with 300 frames over 2500 events those would be billions of actor steps
//     instead of tens of thousands.
//
// And the third, which one does not see at all: **no time zone library**. The clock computes
// with integers from `ts + tz_offset_min*60000`. Python knows the offset of the day and sends
// it along; `toLocale*` or `Intl` would make the picture depend on the ICU version of the base image.

import { ART } from "../../src/components/office/const.ts";
import { Recorder } from "../../src/components/office/recorder.ts";
import { Replay } from "../../src/components/office/replay.ts";
import { renderFrame } from "../../src/components/office/pixel/scene.ts";
import { imagePlan } from "./cut.mjs";
import { hudLine, chapterCard } from "./hud.mjs";
import { rasterCtx } from "./raster.mjs";
import { gif } from "./gif.mjs";

/** How many frames a chapter card stays (at 12 fps a third of a second). Fewer and one does
 *  not read the time, more and eight cards eat a sixth of the film. */
const CARD_IMAGES = 4;

// The film stays on the art level (480x270) for now: its picture is as coarse today as the art
// is, and a GIF four times as large would bring not one more stroke of detail. As soon as the
// art is drawn finely, it moves onto the buffer as well.
const CAM_FILM = { x: ART.w / 2, y: ART.h / 2, zoom: 1 };
/** Below this many frames a chapter is no longer a chapter but a twitch. */
const MIN_IMAGES = 6;

/**
 * Builds the film. `order` is the body of `POST /film`, field by field.
 *
 * Returns: the GIF bytes and the numbers that go into the answer as `X-Film-*`; Python writes
 * the caption out of them ("8 of 67 scenes").
 */
export function buildFilm(order) {
  const t0 = Date.now();
  const events = Array.isArray(order.events) ? order.events : [];
  const grade = order.grade === "day" ? "day" : "night";
  const fps = num(order.fps, 12);
  const seconds = num(order.seconds, 25);
  const offset = num(order.tz_offset_min, 0);
  const title = typeof order.title === "string" ? order.title : "";

  const rec = new Recorder();
  // The roster is **rebuilt** from the `run_start` rows instead of being sent along: the same
  // fields already stand in the event (role, phase, model, parent run), and without them every
  // figure would have the empty role, so all would look the same (the role determines shirt,
  // hair and torso) and the handover at the end of a run would drop out, because `mapEvent`
  // knows the parent run only from the roster. A second field in the contract would be superfluous.
  rec.setRoster(rosterFrom(events));
  for (const ev of events) rec.push(ev);

  const log = rec.entries();
  const bounds = rec.bounds();
  if (log.length === 0) return null;

  const plan = imagePlan(log, {
    fps, seconds, chapter: num(order.chapter, 8),
    minImages: MIN_IMAGES, cardImages: CARD_IMAGES,
  });
  if (plan.images.length === 0) return null;

  const marks = sessionMarks(events);
  const replay = new Replay(log);
  const { ctx, buf, reset } = rasterCtx(ART.w, ART.h);
  const images = [];

  // The first jump is a `seek`, not an `advance`: a fresh `Replay` stands at the beginning but
  // has applied **no** event yet, and `advance(0)` is a no-op. Without this line the first
  // frame of every chapter would show an empty room.
  replay.seek(plan.images[0].ts);

  let cardIdx = -1;
  let cardRun = 0;
  for (const b of plan.images) {
    const dt = b.ts - replay.position;
    if (dt > 0) replay.advance(dt);

    reset();
    const frame = replay.frame();
    renderFrame(ctx, frame, CAM_FILM, grade);

    const time = clockTime(b.ts, offset);
    hudLine(ctx, grade, line(time, marks, b.ts, frame));

    if (b.chapter === null) {
      cardIdx = -1;
      cardRun = 0;
    } else {
      if (b.chapter !== cardIdx) { cardIdx = b.chapter; cardRun = 0; }
      chapterCard(ctx, grade, title, time, fade(cardRun, CARD_IMAGES));
      cardRun++;
    }

    // The rasteriser writes into the **same** buffer; without a copy the GIF would contain the
    // last frame 300 times. The bug looks like an encoder bug and is none.
    images.push(buf.slice());
  }

  const verzoegerung = Math.max(20, Math.round(1000 / (fps > 0 ? fps : 12)));
  const kodiert = gif(images, {
    w: ART.w, h: ART.h,
    delaysMs: images.map(() => verzoegerung),
    loop: 0,
  });

  return {
    bytes: kodiert.bytes,
    chapter: plan.chapter.length,
    islands: plan.chapter.length + plan.uebersprungen,
    images: images.length,
    capped: plan.capped || bounds.dropped,
    durationMs: Date.now() - t0,
  };
}

// ── Zeit, ganzzahlig ─────────────────────────────────────────────────────────

/** `ts + offset` to `HH:MM:SS`. Pure integer arithmetic, no `Date`, no `Intl`. The offset is
 *  fixed for the whole day: Python computed it, because only Python knows which zone the
 *  viewer sits in and whether the clock was changed on that day. */
export function clockTime(ms, versatzMin) {
  const t = Math.floor(ms) + Math.round(versatzMin) * 60000;
  let s = Math.floor(t / 1000) % 86400;
  if (s < 0) s += 86400;
  return p2((s / 3600) | 0) + ":" + p2(((s % 3600) / 60) | 0) + ":" + p2(s % 60);
}

function p2(n) {
  return n < 10 ? "0" + n : String(n);
}

// ── The HUD line ─────────────────────────────────────────────────────────────

/** Time · session respectively ticket · number of figures in the room.
 *
 *  The number stands there without a word: every label would be language, and in this feature
 *  language is built exclusively by Python (the caption says what the film shows anyway). */
function line(time, marks, ts, frame) {
  let people = 0;
  for (const a of frame.actors) if (a.retired !== true) people++;
  const where = markeBei(marks, ts);
  return where ? `${time} | ${where} | ${people}` : `${time} | ${people}`;
}

/** Label changes over the day: one entry per event, ascending by `ts`.
 *
 *  Necessary because `LogEntry` does not carry the session: the recorder translates into
 *  commands, and commands know only figures. A day contains many sessions, and without this
 *  track the line would show the same ticket over the whole film. */
function sessionMarks(events) {
  const namen = new Map();
  for (const ev of events) {
    const key = typeof ev.issue_key === "string" && ev.issue_key.length > 0 ? ev.issue_key : null;
    if (key !== null && !namen.has(ev.sid)) namen.set(ev.sid, key);
  }
  const out = [];
  for (const ev of events) {
    const at = Date.parse(ev.ts);
    if (!Number.isFinite(at)) continue;
    out.push({ ts: at, text: namen.get(ev.sid) ?? ev.sid ?? "" });
  }
  out.sort((a, b) => a.ts - b.ts);
  return out;
}

/** The last mark with `ts <= at`. Searched linearly: the frames come in ascending order, but a
 *  pointer over two data series would be one state variable more for the same answer. */
function markeBei(marks, at) {
  let text = marks.length > 0 ? marks[0].text : "";
  for (const m of marks) {
    if (m.ts > at) break;
    text = m.text;
  }
  return text;
}

// ── Kleinkram ────────────────────────────────────────────────────────────────

/** Fading the chapter card in and out: the first and the last frame half, full in between. A
 *  longer transition would only waste reading time with four frames. */
function fade(i, m) {
  if (m <= 1) return 1;
  return i === 0 || i === m - 1 ? 0.5 : 1;
}

function num(v, ersatz) {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : ersatz;
}

/** The roster from the `run_start` rows. Only the five fields `mapEvent` really reads: a
 *  complete `RosterEntry` would be an invention here, not completeness. */
function rosterFrom(events) {
  const out = [];
  const gesehen = new Set();
  for (const ev of events) {
    if (ev.kind !== "run_start" || gesehen.has(ev.agent_id)) continue;
    gesehen.add(ev.agent_id);
    out.push({
      agent_id: ev.agent_id,
      run_id: ev.run_id,
      agent: typeof ev.agent === "string" ? ev.agent : "",
      phase: ev.phase ?? null,
      issue_key: ev.issue_key ?? null,
      model: ev.model ?? null,
      parent_run_id: ev.parent_run_id ?? null,
    });
  }
  return out;
}
