// The cut: 300 frames are made out of a whole office day.
//
// The sentence everything rests on is a property of `replay.ts::settle()` and not an
// assumption: the simulation time between two events is `min(MAX_GAP_MS, ts - anchor)`. As
// soon as `ts - anchor` reaches the limit the value saturates, `want === spent`, and every
// further advance integrates **nothing**. From 20 s after the last event on, every frame is
// therefore bit identical to the previous one, and skipping silence provably loses nothing.
// Out of 14 hours of wall clock 20 to 80 minutes of simulation time arise that way.
//
// What does **not** follow from that: a fixed frame rate. 45 minutes in 25 s at 12 fps are
// 2.25 s of simulation time per frame; a walk through the room (about 3 s) would get a single
// frame and the figures would jump from seat to seat. Hence chapters: a few islands in real time.

import { MAX_GAP_MS, REPLAY_CAP } from "../../src/components/office/const.ts";

/** Status values that count as a failure, the same summary as in the personnel file
 *  (`loop_exhausted` is an abort, not a state of its own for the viewer). */
const FEHLER_STATUS = new Set(["failed", "loop_exhausted"]);

/**
 * Activity islands: contiguous time windows between which more than `luecke` of silence lies.
 *
 * `to` is deliberately **not** the last timestamp but `last ts + luecke`: until then the room
 * still moves (the walk to the table ends, the bubble expires), and only after that does it
 * demonstrably stand still. Whoever cuts at `last ts` cuts in the middle of the movement.
 *
 * The log is sorted by `ts` here, and only here. For the replay that would be a mistake (it
 * would put the effect before the cause), but nothing is replayed while measuring: what is
 * sought are the time windows in which anything happened at all. The copy protects the caller.
 */
export function islands(log, luecke) {
  const spalte = luecke > 0 ? luecke : MAX_GAP_MS;
  const nachZeit = log.slice().sort((a, b) => (a.ts - b.ts) || (a.seq - b.seq));

  const out = [];
  let cur = null;
  for (const e of nachZeit) {
    if (cur === null || e.ts - cur.to > spalte) {
      cur = { from: e.ts, to: e.ts, events: 0, agenten: new Set(), fehler: 0, gates: 0, weight: 0 };
      out.push(cur);
    }
    cur.to = e.ts;
    cur.events++;
    for (const c of e.cmds) zaehle(cur, c);
  }

  for (const i of out) {
    i.to += spalte;
    // Deterministic and without a knob: errors weigh the most (they explain the day), gates
    // after them (a waiting room is the most common cause of silence), the number of
    // participants fills a picture, and the sheer number of events enters only
    // logarithmically; otherwise every long tool chain would win against every interesting moment.
    i.weight = 3 * i.fehler + 2 * i.gates + i.agenten.size + 0.5 * Math.log(1 + i.events);
  }
  return out;
}

function zaehle(insel, c) {
  if (typeof c.id === "string") insel.agenten.add(c.id);
  if (c.k === "toolEnd" && c.ok === false) insel.fehler++;
  else if (c.k === "done" && c.ok === false) insel.fehler++;
  else if (c.k === "status" && FEHLER_STATUS.has(c.status)) insel.fehler++;
  else if (c.k === "deploy" && c.state === "fail") insel.fehler++;
  else if (c.k === "gate") insel.gates++;
}

/**
 * The frame plan: which moment gets which frame.
 *
 * `chapter` stands here in addition to the four options named in the contract, because the
 * number of chapters comes from the HTTP request and the budget computation needs it.
 *
 * Returns: `images[]` in playing order (`chapter` = number of the chapter card, `null` = an
 * ordinary frame), the chosen `chapter[]`, the number of islands not shown and `capped`.
 */
export function imagePlan(log, opts) {
  const fps = opts.fps > 0 ? opts.fps : 12;
  const seconds = opts.seconds > 0 ? opts.seconds : 25;
  const cardImages = opts.cardImages >= 0 ? opts.cardImages : 4;
  const minImages = opts.minImages > 0 ? opts.minImages : 6;
  const wunsch = opts.chapter > 0 ? Math.floor(opts.chapter) : 8;

  const budget = Math.max(1, Math.round(seconds * fps));
  const all = islands(log, MAX_GAP_MS);
  const leer = { images: [], chapter: [], uebersprungen: 0, capped: log.length >= REPLAY_CAP };
  if (all.length === 0) return leer;

  // A tie breaks on `from`: two islands of equal weight must not depend on how the sort of the
  // runtime happens to shovel, because otherwise the same day is a different film twice.
  //
  const rank = all.slice().sort((a, b) => (b.weight - a.weight) || (a.from - b.from));

  // A chapter below `minImages` would be a twitch instead of a scene: better fewer chapters.
  const passt = Math.floor(budget / (cardImages + minImages));
  const n = Math.max(1, Math.min(rank.length, wunsch, passt));
  const chosen = rank.slice(0, n).sort((a, b) => a.from - b.from);

  const rest = budget - n * cardImages;
  const anteile = verteile(chosen, rest, minImages);

  const images = [];
  for (let k = 0; k < chosen.length; k++) {
    const chap = chosen[k];
    for (let i = 0; i < cardImages; i++) images.push({ ts: chap.from, chapter: k });
    const m = anteile[k];
    const span = chap.to - chap.from;
    for (let i = 0; i < m; i++) {
      const ts = m > 1 ? chap.from + Math.round((span * i) / (m - 1)) : chap.from;
      images.push({ ts, chapter: null });
    }
  }

  return {
    images,
    chapter: chosen.map((i) => ({ from: i.from, to: i.to, weight: i.weight })),
    uebersprungen: all.length - chosen.length,
    // The recorder truncates at the **oldest** end: a log at the truncation limit has most
    // likely lost the morning. Losing that silently would be the worst error of this feature,
    // which is why the number travels as a header all the way into the caption.
    capped: log.length >= REPLAY_CAP,
  };
}

/**
 * Frames per chapter by the square root of the weight.
 *
 * The square root and not the weight itself: distributed linearly, the strongest island of a
 * day of errors would get two thirds of the film and the other seven chapters twelve frames
 * each. The square root damps just far enough that the ranking stays visible.
 */
function verteile(chapter, rest, minImages) {
  let summe = 0;
  for (const k of chapter) summe += Math.sqrt(Math.max(0, k.weight));
  const anteile = chapter.map((k) =>
    Math.max(minImages, summe > 0 ? Math.round((rest * Math.sqrt(Math.max(0, k.weight))) / summe) : minImages));

  // Rounding and the lower bound blow the budget in both directions. Balancing always happens
  // at the largest respectively the smallest chapter, on a tie at the front one: the same rule
  // as above, and for the same reason.
  let ist = anteile.reduce((a, b) => a + b, 0);
  while (ist > rest) {
    let idx = -1;
    for (let i = 0; i < anteile.length; i++) {
      if (anteile[i] > minImages && (idx < 0 || anteile[i] > anteile[idx])) idx = i;
    }
    if (idx < 0) break;
    anteile[idx]--;
    ist--;
  }
  while (ist < rest) {
    let idx = 0;
    for (let i = 1; i < chapter.length; i++) if (chapter[i].weight > chapter[idx].weight) idx = i;
    anteile[idx]++;
    ist++;
  }
  return anteile;
}
