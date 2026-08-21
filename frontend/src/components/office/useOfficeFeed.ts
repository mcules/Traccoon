// Layer 2, the heart of it: socket plus snapshot become a gapless, duplicate free log.
//
// ══ The sequence, and it is critical for determinism ═══════════════════════════════════════
//
//   1. Open the WebSocket and send `subscribe`.
//   2. **Buffer** incoming events while the backfill runs.
//   3. Fetch the snapshot through `officeApi.events(…)`, page by page, until one page is
//      shorter than `limit`.
//   4. Replay the buffered events. `Recorder.push` drops duplicates by their `seq` on its own;
//      nothing is compared here.
//   5. Live gehen.
//
// The order is not style but the only one that loses no event: socket first, then snapshot. The
// other way round every row written between reading the database and subscribing would fall
// into a hole, and a missing `run_end` means a character never leaves the room.
// into a hole, and a missing `run_end` means a character never leaves the room.
//
// ══ Why polling incrementally with `after_seq` never happens ═══════════════════════════════
//
// `seq` comes from `run_steps.id`, so from a `SERIAL` column, and that is assigned **before**
// the commit. Two parallel workers (`WORKER_CONCURRENCY > 1`) can therefore make their rows
// visible in reverse order: 1005 first, then 1003. A poller that remembers its high water mark
// would skip 1003 forever without ever noticing.
//
// `after_seq` is used exclusively for (a) paging inside **one** snapshot and (b) filling a gap
// immediately after a reconnect. In both cases the buffer or the following full snapshot covers
// whatever the paging might have missed. When the client detects a hole (every reconnect counts
// as a hole, because it cannot know what happened during the disconnection) it fetches a
// **full new** snapshot. That is cheap: `Recorder.push` throws away everything already known.

//
// ══ State discipline that saves the frame rate ═════════════════════════════════════════════
//
// · The `Recorder` lives in a **ref**. The stage never renders per frame.
// · The only render signal is `revision`, and the bump is throttled to `REVISION_THROTTLE_MS`.
//   A burst of 60 steps triggers one render pass, not sixty.
// · Deduplication happens **exclusively** in `Recorder.push`. Exactly for that reason the
//   backfill race, reconnect duplicates and a manual reload are all handled by the same line:
//   there is no second place where a rule could drift.
// · The snapshot is `staleTime: Infinity`: it is a moment, not a value that could go stale. It
//   is carried forward through the socket, not through a refetch.
// · On an `ApiError` with status 401 `src/api.ts` already redirects hard, so there is
//   deliberately no second handling here.
//
// ══ Two modes of operation, one path ═══════════════════════════════════════════════════════
//
// With a `sid` the room is **one** session; events of other sessions are dropped, because the
// server filters by project and not by room.
//
// Without a `sid` and with `opts.alleSitzungen` the room is the **window**: the snapshot comes
// from `GET /office/events`, and live every event the socket delivers is accepted, because it
// already filters server side to what this user may see (`api/office_ws.py`). That only works
// because `seq` comes from `run_steps.id`, a SERIAL column: monotonic across runs **and**
// projects, so twenty sessions form ONE sequence and `Recorder.push` deduplicates them by the
// same rule as a single one. The seat (`hash32(run_id) % 12`) is independent of the session
// anyway.
//
// Both modes take the same path through this file: there is one recorder, one paging loop and
// one transition from buffer to live, not two.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getToken } from "../../api";
import {
  EVENT_PAGE_LIMIT, EVENT_VERSION, officeApi, officeWsUrl, sidKey, subscribeMessage,
  type CostRollup, type EventPage, type Scope, type Sid, type WsIn,
} from "./api.ts";
import { REPLAY_CAP } from "./const.ts";
import type { Ev, EvRunEnd, EvRunStart, LogEntry, Roster, RosterEntry } from "./types.ts";

// The recorder module (`./recorder.ts`, layer 0) arrived later. While the file was missing the
// `@ts-ignore` kept the build green; the line below it is already the final one. Once it lands:
// delete the comment and check that `RecorderApi` below matches the class, at which point
// `RecorderApi` is superfluous and is replaced by `import type { Recorder }`.
// @ts-ignore -- module follows (office/recorder.ts)
import { Recorder } from "./recorder.ts";

// ── The interface this is built against ─────────────────────────────────────────────────────

/** What the feed needs from the `Recorder`.
 *  Do **not** implement it here: the recorder is layer 0 and belongs to the engine. */
export interface RecorderApi {
  /** Takes an event in. `false` means already known (`seq` seen before) and dropped. The ONE
   *  place where deduplication happens. */
  push(ev: Ev): boolean;
  /** Counts up on every actual intake. The feed throttles its `revision` from it. */
  readonly revision: number;
  /** The log in arrival order, as a **copy**. `readonly`, because the copy belongs to the
   *  caller but is passed on (`new Replay(log)`): a list somebody may change on the way would
   *  be exactly the aliasing bug the copy prevents. */
  entries(): readonly LogEntry[];
  /** Time and `seq` bounds of the log, `null` while it is empty. */
  bounds(): { t0: number; t1: number; seq0: number; seq1: number } | null;
  /** Alles vergessen (Sitzungswechsel). */
  reset(): void;
}

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** At most five render passes per second out of the event stream. Deliberately trailing only:
 *  the first event of a burst costs 200 ms of delay, but the whole burst costs exactly one
 *  pass. */
const REVISION_THROTTLE_MS = 200;

/** Waits of the reconnect. The last value applies permanently afterwards: a backend that is
 *  restarting should come back without a hundred tabs overrunning it.  */
const RECONNECT_MS = [1000, 2000, 5000, 10_000, 20_000];

/** Against middleboxes that cut silent connections (the server answers with `pong`). */
const PING_MS = 45_000;

/** If the socket does not come up, the snapshot is fetched anyway. A view without a live
 *  stream is usable; an empty view is not. */
const SOCKET_GRACE_MS = 4000;

/** Cap of the buffer. It only takes effect in a failure case, when the snapshot
 *  does not come back at all (a dead endpoint) and the socket still delivers, the buffer would
 *  otherwise grow without bound. Dropping happens at the **oldest** end, exactly as with the
 *  event window of the backend; and once the snapshot works again the gap is filled anyway. */
const BUFFER_CAP = REPLAY_CAP;

/** The snapshot fetches no more pages than this. `REPLAY_CAP` is the limit of what the engine
 *  replays anyway, and without a cap a backend that ignores `limit` could send the browser
 *  into an endless loop. */
const MAX_PAGES = Math.ceil(REPLAY_CAP / EVENT_PAGE_LIMIT) + 1;

// ── Return value ────────────────────────────────────────────────────────────────────────────

/** Totals over the whole session. Tokens come from the roster (authoritative per run), cost
 *  from the cost call, which is the only one that knows the difference between "priced and
 *  free" and "not in the catalog". */
export interface FeedTotals {
  runs: number;
  running: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  /** Billed, from `CostEntry`. */
  cost_usd_billed: number;
  /** Estimated against the current catalog, right on a fallback run as well. */
  cost_usd_estimated: number;
  /** At least one model turn has no catalog entry, so the display puts a "≥" in front. */
  cost_partial: boolean;
}

/** Default window of the "all sessions" mode, in hours.
 *
 *  Has to match `api/office.py::EVENTS_SINCE_HOURS_DEFAULT`: the interface writes the number
 *  into the header ("the last 12 hours"), and a heading that names a different window than the
 *  one measured would be worse than none. Why twelve: measured against the data there are 14
 *  runs in it, at 24 h there are 23, and `office/const.ts` allows `MAX_ACTORS = 24` characters
 *  at once, so the room would permanently sit at the edge and evict a character with every new
 *  run. */
export const ALL_WINDOW_H = 12;

/** Extra switches of the feed. */
export interface OfficeFeedOpts {
  /** Without a `sid`, mix **all** sessions into one room instead of staying empty. */
  allSessions?: boolean;
  /** Fenster dieses Modus in Stunden. */
  sinceHours?: number;
}

export interface OfficeFeed {
  /** Lives in a ref and only changes identity when the session changes. */
  recorder: RecorderApi;
  /** The only render signal. Rises throttled. */
  revision: number;
  roster: Roster;
  totals: FeedTotals;
  /** Socket open **and** backfill done: events arrive in real time. */
  live: boolean;
  error?: string;
}

const EMPTY_TOTALS: FeedTotals = {
  runs: 0, running: 0, in_tokens: 0, out_tokens: 0, cache_read_tokens: 0,
  cost_usd_billed: 0, cost_usd_estimated: 0, cost_partial: false,
};

// ── Schnappschuss ───────────────────────────────────────────────────────────────────────────

interface Snapshot {
  events: Ev[];
  agents: Roster;
  page: EventPage | null;
}

/** Page by page, until one page is shorter than `limit`.
 *
 *  The paging uses `after_seq`, and that is the one permitted use: it runs forward inside
 *  **one** operation, and whatever becomes visible afterwards with a smaller `seq` already
 *  lies in the buffer of the socket (which started earlier). The recorder inserts it at its
 *  place and throws the duplicate away.
 *
 *  `lade` is the only difference between "one session" and "all sessions": both endpoints
 *  deliver the same shape, so **one** loop pages through both. Two loops would be two
 *  opportunities to lose the order differently. */
async function fetchSnapshot(load: (afterSeq?: number) => Promise<EventPage>): Promise<Snapshot> {
  const events: Ev[] = [];
  let agents: Roster = [];
  let page: EventPage | null = null;
  let afterSeq: number | undefined;

  for (let i = 0; i < MAX_PAGES; i++) {
    const p = await load(afterSeq);
    const batch = p.events ?? [];
    // The roster stands only on the first page; later pages do not overwrite it with an empty
    // field, otherwise the room would have no cast after paging.
    if (p.agents && p.agents.length) agents = p.agents;
    for (const ev of batch) events.push(ev);
    page = p;
    if (batch.length < EVENT_PAGE_LIMIT) break;
    const last = batch[batch.length - 1];
    const next = p.seq_to ?? last?.seq;
    if (next === undefined || next === null) break;
    afterSeq = next;
  }
  return { events, agents, page };
}

// ── Roster ──────────────────────────────────────────────────────────────────────────────────

/** A run that enters the room live stands in no roster yet, because that came with the
 *  snapshot. Until the next snapshot the entry is built from the `run_start` event; the
 *  numbers are filled in by the `run_end`. */
function rosterFromRunStart(ev: EvRunStart): RosterEntry {
  return {
    agent_id: ev.agent_id, run_id: ev.run_id, agent: ev.agent, phase: ev.phase,
    status: "running", issue_key: ev.issue_key, project_id: ev.project_id,
    project_key: null, provider: ev.provider, model: ev.model,
    parent_run_id: ev.parent_run_id, spawn_depth: ev.spawn_depth,
    started_at: ev.ts, ended_at: null, iterations: 0,
    in_tokens: 0, out_tokens: 0, cache_read_tokens: 0, cost_usd: 0, cost_priced: null,
  };
}

function rosterFromRunEnd(prev: RosterEntry | undefined, ev: EvRunEnd): RosterEntry {
  const base: RosterEntry = prev ?? {
    agent_id: ev.agent_id, run_id: ev.run_id, agent: "", phase: null, status: ev.status,
    issue_key: null, project_id: ev.project_id, project_key: null, provider: null, model: null,
    parent_run_id: null, spawn_depth: 0, started_at: null, ended_at: null, iterations: 0,
    in_tokens: 0, out_tokens: 0, cache_read_tokens: 0, cost_usd: 0, cost_priced: null,
  };
  return {
    ...base, status: ev.status, ended_at: ev.ts, iterations: ev.iterations,
    in_tokens: ev.in_tokens, out_tokens: ev.out_tokens, cache_read_tokens: ev.cache_read_tokens,
    cost_usd: ev.cost_usd, cost_priced: ev.cost_priced,
  };
}

// ── The feed ────────────────────────────────────────────────────────────────────────────────

export function useOfficeFeed(scope: Scope, sid?: Sid, opts?: OfficeFeedOpts): OfficeFeed {
  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const sinceHours = opts?.sinceHours ?? ALL_WINDOW_H;
  /** "All sessions": no room chosen **and** the caller wants this mode. The second part is not
   *  decoration: without a `sid` the project tab is in the same state while its session list is
   *  still on the way, and it should not show the
   *  ganze Projekt laden, um es sofort wieder wegzuwerfen. */
  const all = !sid && !!opts?.allSessions;
  // The identity of the log. It changes on a change of room **and** on a change of window:
  // both are a different log, and the recorder has to be empty in between.
  const key = sid ? sidKey(sid) : (all ? `alle:${scopeKey}:${sinceHours}` : null);

  // ── Refs: everything touched per event but not allowed to trigger a render ────────────────
  const recorderRef = useRef<RecorderApi | null>(null);
  if (recorderRef.current === null) recorderRef.current = new Recorder() as RecorderApi;
  const recorder = recorderRef.current;

  /** Events arriving during the backfill. Emptied when they are replayed. */
  const bufferRef = useRef<Ev[]>([]);
  /** `false` while the snapshot runs: then events are buffered instead of taken in. */
  const liveRef = useRef(false);
  /** The current session, so the message receiver follows without re-subscribing. In the "all
   *  sessions" mode it stays empty, because nothing is separated there. */
  const sidRef = useRef<string | null>(sid ? sidKey(sid) : null);
  sidRef.current = sid ? sidKey(sid) : null;
  /** Mirror for `accept`: the receiver hangs on the socket effect and must not be
   *  re-registered on every change of mode. */
  const allRef = useRef(all);
  allRef.current = all;

  const wsRef = useRef<WebSocket | null>(null);
  const bumpTimerRef = useRef<number | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const closingRef = useRef(false);
  const rosterRef = useRef<Map<string, RosterEntry>>(new Map());

  // ── State: six values, React sees no more of the stream ───────────────────────────────────
  const [revision, setRevision] = useState(0);
  const [roster, setRoster] = useState<Roster>([]);
  const [live, setLive] = useState(false);
  const [wsError, setWsError] = useState<string | undefined>(undefined);
  /** > 0 means "the socket is supplied" and releases the snapshot; every increase is a new,
   *  **full** snapshot (first connection, reconnect, emergency without a socket). */
  const [generation, setGeneration] = useState(0);

  /** Throttled render signal. Calling it several times per tick is explicitly allowed. */
  const scheduleBump = useCallback(() => {
    if (bumpTimerRef.current !== null) return;
    bumpTimerRef.current = window.setTimeout(() => {
      bumpTimerRef.current = null;
      setRevision((r) => r + 1);
    }, REVISION_THROTTLE_MS);
  }, []);

  /** Carry the roster forward from a boundary event. Happens a few times per session, not per
   *  step, which is why it may touch real state here. */
  const patchRoster = useCallback((ev: Ev) => {
    if (ev.kind !== "run_start" && ev.kind !== "run_end") return;
    const map = rosterRef.current;
    const prev = map.get(ev.agent_id);
    if (ev.kind === "run_start") {
      if (prev) return;                       // the snapshot knows it already
      const fresh = rosterFromRunStart(ev);
      // The event carries the project **id**, not the key, which only the snapshot knows. If
      // somebody from the same project already stands in the room, the key is
      // taken from there and not guessed; otherwise it stays empty and the character sits under
      // "(no project)" until the next snapshot. That is visible in the "all sessions" mode,
      // because the tabs group by project there.
      if (fresh.project_id !== null) {
        for (const r of map.values()) {
          if (r.project_id === fresh.project_id && r.project_key) {
            fresh.project_key = r.project_key;
            break;
          }
        }
      }
      map.set(ev.agent_id, fresh);
    } else {
      map.set(ev.agent_id, rosterFromRunEnd(prev, ev));
    }
    setRoster([...map.values()]);
  }, []);

  /** An event from the wire: check whether it belongs here, then buffer or take it in. */
  const accept = useCallback((ev: Ev) => {
    // Contract version: better show nothing than something wrong (see `EVENT_VERSION`).
    if (ev.v !== EVENT_VERSION) return;
    // The server filters by project, not by room, so the client separates the session. In the
    // "all sessions" mode there is nothing to separate: what the socket delivers is already
    // exactly what this user may see (`api/office_ws.py::visible`), and the room shows it
    // together. Throwing it away here anyway was the one line that made the overview
    // impossible until now.
    if (!allRef.current && (!sidRef.current || ev.sid !== sidRef.current)) return;
    if (!liveRef.current) {
      const buf = bufferRef.current;
      buf.push(ev);
      if (buf.length > BUFFER_CAP) buf.splice(0, buf.length - BUFFER_CAP);
      return;
    }
    if (recorderRef.current?.push(ev)) {
      patchRoster(ev);
      scheduleBump();
    }
  }, [patchRoster, scheduleBump]);

  // ── Change of session: empty the recorder, back into the backfill ─────────────────────────
  //
  // Stands before the `useQuery` so that this effect runs first in the same pass: the snapshot
  // must never fall into a recorder that still holds the old session.
  useEffect(() => {
    liveRef.current = false;
    bufferRef.current = [];
    recorderRef.current?.reset();
    rosterRef.current = new Map();
    setRoster([]);
    setLive(false);
    setRevision((r) => r + 1);
  }, [key]);

  // ── The socket ────────────────────────────────────────────────────────────────────────────
  //
  // Hangs on the scope only, not on the session: changing rooms inside the same project does
  // not rebuild the connection (`sidRef` follows).
  useEffect(() => {
    closingRef.current = false;
    attemptRef.current = 0;
    let graceTimer: number | null = null;

    /** Releases the snapshot. Calling it several times is the normal case (every reconnect),
     *  and each time a full new one is fetched. */
    const armSnapshot = () => setGeneration((g) => g + 1);

    const clearPing = () => {
      if (pingTimerRef.current !== null) { window.clearInterval(pingTimerRef.current); pingTimerRef.current = null; }
    };

    const connect = () => {
      if (closingRef.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(officeWsUrl(getToken()));
      } catch {
        // No socket possible (a blocked upgrade, say): the snapshot alone has to do.
        armSnapshot();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setWsError(undefined);
        ws.send(JSON.stringify(subscribeMessage(scope)));
        // Only now fetch the snapshot: before it every row written between reading and
        // subscribing would fall into a hole.
        armSnapshot();
        clearPing();
        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
        }, PING_MS);
      };

      ws.onmessage = (e) => {
        let msg: WsIn;
        try { msg = JSON.parse(e.data as string) as WsIn; } catch { return; }
        if (msg.type === "office_ev") { accept(msg.ev); return; }
        if (msg.type === "hello" && msg.v !== EVENT_VERSION) {
          // The backend speaks a different contract. Do not guess, do not reconnect.
          closingRef.current = true;
          setWsError(`Das Büro spricht Vertragsversion ${msg.v}, diese Ansicht kennt `
            + `${EVENT_VERSION}. Bitte die Seite neu laden.`);
          ws.close();
        }
      };

      ws.onclose = (e) => {
        clearPing();
        liveRef.current = false;
        setLive(false);
        if (closingRef.current) return;
        // 4401/4403 come from the authentication of the socket, and retrying does not help. A
        // 401 on the HTTP path is already redirected hard by `src/api.ts`.
        if (e.code === 4401 || e.code === 4403) {
          setWsError("Keine Berechtigung für den Live-Strom des Büros.");
          return;
        }
        const wait = RECONNECT_MS[Math.min(attemptRef.current, RECONNECT_MS.length - 1)];
        attemptRef.current++;
        retryTimerRef.current = window.setTimeout(connect, wait);
      };
    };

    connect();
    // Emergency anchor: if the socket does not come up, it is fetched once anyway. Without that
    // the view would stay black where a static view was possible.
    graceTimer = window.setTimeout(() => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) armSnapshot();
    }, SOCKET_GRACE_MS);

    return () => {
      closingRef.current = true;
      if (graceTimer !== null) window.clearTimeout(graceTimer);
      if (retryTimerRef.current !== null) { window.clearTimeout(retryTimerRef.current); retryTimerRef.current = null; }
      if (pingTimerRef.current !== null) { window.clearInterval(pingTimerRef.current); pingTimerRef.current = null; }
      if (bumpTimerRef.current !== null) { window.clearTimeout(bumpTimerRef.current); bumpTimerRef.current = null; }
      wsRef.current?.close();
      wsRef.current = null;
      bufferRef.current = [];
      liveRef.current = false;
    };
  }, [scopeKey, accept]);   // `accept` is stable through `useCallback`

  // ── The snapshot ──────────────────────────────────────────────────────────────────────────
  //
  // `staleTime: Infinity`: a snapshot is a moment, not a value that goes stale. It is carried
  // forward through the socket. It is fetched again only when `generation` rises, so on a hole,
  // and every reconnect is a hole.
  const snapshot = useQuery({
    queryKey: ["office", "events", key, generation],
    queryFn: () => fetchSnapshot(sid
      ? (afterSeq) => officeApi.events(sid, { limit: EVENT_PAGE_LIMIT, afterSeq })
      // The scope travels along as `project_id`: it **narrows** server side and never
      // authorises; the allowed set is computed by the backend itself anyway.
      : (afterSeq) => officeApi.allEvents({
        limit: EVENT_PAGE_LIMIT, afterSeq, sinceHours,
        ...(scope.kind === "project" ? { projectId: scope.projectId } : {}),
      })),
    enabled: (!!sid || all) && generation > 0,
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  // Steps 4 and 5: take the snapshot in, replay the buffer, go live.
  // This runs in ONE synchronous block: no message can come in between, and exactly for that
  // reason the transition needs no lock.
  useEffect(() => {
    const data = snapshot.data;
    if (!data) return;
    const rec = recorderRef.current;
    if (!rec) return;

    for (const ev of data.events) rec.push(ev);

    const map = new Map<string, RosterEntry>();
    for (const entry of data.agents) map.set(entry.agent_id, entry);
    rosterRef.current = map;

    const buffered = bufferRef.current;
    bufferRef.current = [];
    for (const ev of buffered) {
      // `push` throws duplicates away, nothing is compared here. That is the whole trick.
      if (rec.push(ev)) patchRoster(ev);
    }

    liveRef.current = true;
    setRoster([...rosterRef.current.values()]);
    setLive(wsRef.current?.readyState === WebSocket.OPEN);
    setRevision((r) => r + 1);
  }, [snapshot.data, patchRoster]);

  // ── Kosten ────────────────────────────────────────────────────────────────────────────────
  //
  // An ordinary query: cost changes at the end of a run, not per step. While something runs it
  // is checked, afterwards no longer.
  //
  // In the "all sessions" mode it does **not** exist: the roll up is built per room, and one
  // call per shown session would be twenty rounds for one total. `computeTotals` then falls
  // back to the roster: tokens are authoritative there anyway, the cost is the billed one per
  // run, and `cost_priced` carries the "≥" as everywhere else. The header then means the total
  // over exactly the sessions standing in the room.
  const sessionRunning = roster.some((r) => r.status === "running");
  const cost = useQuery({
    queryKey: ["office", "cost", key],
    queryFn: () => officeApi.cost(sid!),
    enabled: !!sid && generation > 0,
    staleTime: 30_000,
    refetchInterval: sessionRunning ? 60_000 : false,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  const totals = useMemo<FeedTotals>(() => computeTotals(roster, cost.data), [roster, cost.data]);

  const error = wsError
    ?? (snapshot.error ? `Verlauf nicht ladbar: ${(snapshot.error as Error).message}` : undefined);

  return { recorder, revision, roster, totals, live, error };
}

/** Tokens from the roster, cost from the roll up, and where the roll up is (still) missing,
 *  honestly from the roster, including "incomplete" when even one run is unpriced. */
function computeTotals(roster: Roster, cost: CostRollup | undefined): FeedTotals {
  if (!roster.length && !cost) return EMPTY_TOTALS;
  let inTok = 0, outTok = 0, cacheTok = 0, running = 0, fallbackCost = 0, unpriced = false;
  for (const r of roster) {
    inTok += r.in_tokens || 0;
    outTok += r.out_tokens || 0;
    cacheTok += r.cache_read_tokens || 0;
    fallbackCost += r.cost_usd || 0;
    if (r.status === "running") running++;
    // `null` is an old row, `false` means no catalog entry. Both mean the total is a lower
    // bound, and the display puts a "≥" in front.
    if (r.cost_priced !== true) unpriced = true;
  }
  const billed = cost?.cost_usd_billed ?? fallbackCost;
  return {
    runs: roster.length,
    running,
    in_tokens: cost?.in_tokens ?? inTok,
    out_tokens: cost?.out_tokens ?? outTok,
    cache_read_tokens: cost?.cache_read_tokens ?? cacheTok,
    cost_usd_billed: billed,
    cost_usd_estimated: cost?.cost_usd_estimated ?? billed,
    cost_partial: cost?.cost_partial ?? unpriced,
  };
}
