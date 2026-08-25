// Layer 2, the read API of the office.
//
// A named sub API in the style of `processApi`/`workflowApi` in `src/api.ts`: a flat object with
// three calls, all through the shared `api` helper. That one brings the base path `/api`, the
// `Authorization` header from `getToken()` and, importantly, the hard redirect on 401. So there
// is **no** 401 handling of its own here; it would be a second truth.
//
// The response types live here and **not** in `types.ts`: that is layer 0 and knows no API. What
// comes off the wire is a transport form; what the room builds from it is the contract. Exactly
// one field crosses the border unchanged, `Ev`, and that is deliberate: history and live stream
// come into being in the backend in the same function (`services/office.py` `step_events`), so
// there is only one form in the frontend as well.
//
// ── State of the agreement with the backend ─────────────────────────────────────────────────
// The live socket (`api/office_ws.py`) is built **against real code**:
// `/api/ws?token=`, `{"type":"subscribe","scopes":[…]}`, `{"type":"office_ev","ev":{…}}`.
// The read endpoints (`api/office.py`) did not exist yet when this file was written, so paths
// and field names follow the plan. Everything the feed does not need itself is `?` optional, so
// that a deviation in a side field does not break the build but is simply missing. The places
// that have to match are marked with ⚠.

import { api } from "../../api";
import type { Ev, Roster, RunStatus } from "./types.ts";

/** Version of the event envelope (`services/office.py::EVENT_VERSION`). If the meaning of a
 *  field changes, the backend counts up, and the view refuses to draw instead of painting a
 *  wrong reading. Better a note than a room that lies. */
export const EVENT_VERSION = 1;

// ── Scope and address ───────────────────────────────────────────────────────────────────────

/** Where the session list comes from: a project tab or the global page.
 *  `projectKey` is included because the global page writes `?project=KEY` into the URL and both
 *  header and filter show the key, not the id. */
export type Scope =
  | { kind: "project"; projectId: number; projectKey: string }
  | { kind: "global" };

/** Adresse eines Raums. Genau zwei Formen, siehe `services/office.py::session_id`:
 *  `issue:{id}` is the room of a ticket (planning, execution, continuations, subagents),
 *  `run:{root}` a run without a ticket (job, assistant). */
export interface Sid {
  kind: "issue" | "run";
  ref: number;
}

/** `Sid` becomes `"issue:412"`. The same string stands in every event as `ev.sid`. */
export function sidKey(sid: Sid): string {
  return `${sid.kind}:${sid.ref}`;
}

/** `"issue:412"` becomes a `Sid`, otherwise `null`. Fail closed: unreadable addresses yield no
 *  room instead of silently pointing at a wrong one. */
export function parseSid(raw: string | null | undefined): Sid | null {
  if (!raw) return null;
  const [kind, ref] = raw.split(":");
  if (kind !== "issue" && kind !== "run") return null;
  const n = Number(ref);
  return Number.isSafeInteger(n) && n > 0 ? { kind, ref: n } : null;
}

// ── Antwortformen ───────────────────────────────────────────────────────────────────────────

/** Which rooms the list returns at all (`api/office.py::SESSION_STATUS`). `live` there does
 *  **not** mean "status running in the database" but "running **and** younger than the live
 *  window": after a worker crash `running` would otherwise stand forever and the kiosk would
 *  show a room in which nothing will ever happen again. */
export type SessionStatus = "all" | "live" | "recent";

/** One session in the list: a room, not yet entered. */
export interface SessionSummary {
  /** ⚠ `"issue:412"` / `"run:8871"`. */
  sid: string;
  kind?: "issue" | "run";
  ref?: number;
  title?: string;
  issue_key?: string | null;
  project_id?: number | null;
  project_key?: string | null;
  /** Status of the session: is something still running, or how did it end. */
  status?: RunStatus;
  /** How many runs belong to this room (root plus continuations plus subagents). */
  runs?: number;
  started_at?: string | null;
  ended_at?: string | null;
  /** Newest timestamp of this room (ISO). The backend has always delivered it and sorts the
   *  list by it; it was simply never declared here. The kiosk needs it: a room in which nothing
   *  happened for 90 s is the wrong one for a wall screen. */
  last_event_at?: string | null;
  /** At least one run is `running`. */
  live?: boolean;
  /** The steps fell victim to the retention: the room stays empty, the cost is right anyway
   *  (`CostEntry.run_id` is `SET NULL`). */
  purged?: boolean;
  cost_usd?: number;
  cost_priced?: boolean | null;
}

/** Envelope of the session list. If the backend delivers a bare array, `sessions()` builds the
 *  envelope itself, see there. */
export interface SessionList {
  sessions: SessionSummary[];
  /** How many there would be in total, if the answer was truncated. */
  total?: number;
}

/** One page of the event window. */
export interface EventPage {
  /** ⚠ The events themselves, ascending by `seq`. */
  events: Ev[];
  /** ⚠ Roster straight from `runs`, reliably filled on the first page only. It earns its place
   *  because truncation happens at the **oldest** end: without it the `run_start` events of a
   *  long session would be lost and the room would stay empty. */
  agents?: Roster;
  sid?: string;
  title?: string;
  issue_key?: string | null;
  project_id?: number | null;
  project_key?: string | null;
  /** Smallest and largest `seq` delivered on this page. */
  seq_from?: number | null;
  seq_to?: number | null;
  /** Truncated at the oldest end: there are older events that do not come along. */
  truncated?: boolean;
  purged?: boolean;
  live?: boolean;

  // ── Only on `GET /office/events` (all sessions) ─────────────────────────────────────────
  // This snapshot has no `sid`; the window stands in its place. The fields live here and not in
  // a type of their own, because the frontend sends both snapshots through the **same** path,
  // and two forms would be two paths through `useOfficeFeed`.
  /** `"all"` when the answer mixes several sessions. */
  scope?: string;
  /** The measured window in hours, clamped by the server, which is why it comes back. */
  since_hours?: number;
  window_from?: string | null;
  window_to?: string | null;
  /** How many sessions and runs came together in the window. */
  sessions?: number;
  runs?: number;
}

/** Cost per model. `priced` is the distinction that used to be missing: a catalog entry of 0.00
 *  is *priced and free*, no entry at all is *unknown*. */
export interface CostByModel {
  provider?: string | null;
  model?: string | null;
  in_tokens?: number;
  out_tokens?: number;
  cache_read_tokens?: number;
  cost_usd?: number;
  priced?: boolean;
  calls?: number;
}

/** Cost of a room, on two tracks: billed (from `CostEntry`, authoritative) and estimated (step
 *  tokens against the current catalog, right on a fallback run as well). */
export interface CostRollup {
  cost_usd_billed?: number;
  cost_usd_estimated?: number;
  /** At least one model turn has no catalog entry, so the display puts a "≥" in front. */
  cost_partial?: boolean;
  in_tokens?: number;
  out_tokens?: number;
  cache_read_tokens?: number;
  by_model?: CostByModel[];
  by_agent?: { agent?: string; agent_id?: string; cost_usd?: number; calls?: number }[];
  purged?: boolean;
}

// ── Personalakte: Kennzahlen je Rolle ───────────────────────────────────────────────────────
//
// An aggregate over **roles**, not over runs: the one axis the roster lacks. Everything in it is
// **computed by the server**, and that is not convenience but the whole point: the moment
// `success / runs` stood anywhere here, the old lie would be back (`architect` at 6 % instead of
// 78 %, `project_manager` at 0 % instead of 64 %, because `planned` and `blocked` would count as
// failures there). The frontend may **display** these numbers and put them side by side, not
// derive them.
//
// ⚠ State: `GET /office/agents` came later (`api/office.py`) and did not exist when this file
// was written. Everything except `agent` is therefore `?` optional: a deviation in a side field
// leaves the file with gaps but not broken.

/** Duration distribution of a role. **No average**: one session ran 36.5 hours and would drag
 *  any average into meaninglessness. Median, p90, maximum and a histogram say instead how the
 *  runs are really distributed. */
export interface AgentDuration {
  p50_ms?: number | null;
  p90_ms?: number | null;
  max_ms?: number | null;
  /** Fixed buckets, ascending. `lt_ms` is the **upper** bound; when it is missing this is the
   *  open bucket at the top ("above"). Counted server side as a `CASE WHEN`, because the tests
   *  run on SQLite and there is no `percentile_cont` there. */
  buckets?: { lt_ms?: number | null; n?: number }[];
}

/** One tool in the ranking of a role. `failed` is a number of its own and not derived from
 *  `n - ok`: "unknown" (old data without a measured result) is neither one nor the other. */
export interface AgentTool {
  tool: string;
  n?: number;
  ok?: number;
  failed?: number;
}

/** The file of one role. */
export interface AgentRecord {
  /** ⚠ The role name (`runs.agent`), `developer` for instance. The identity of the row. */
  agent: string;
  runs?: number;
  running?: number;
  /** Raw status counts, undistilled, so that it stays visible where the three bars come from. */
  by_status?: Record<string, number>;
  /** Abgeliefert (`success` + `planned`). */
  delivered?: number;
  /** Waiting for a person (`blocked`). */
  waiting?: number;
  /** Abgebrochen (`failed` + `loop_exhausted`). */
  aborted?: number;
  cost_usd?: number;
  /** At least one entry is unpriced, so the display puts a "≥" in front. */
  cost_partial?: boolean;
  in_tokens?: number;
  out_tokens?: number;
  cache_read_tokens?: number;
  /** **Rounds** of the agent loop (6.9 on average here), not the same as steps. */
  iterations_avg?: number;
  iterations_max?: number;
  /** **Schritte** im Ereignisstrom (Ø 21,5 im Bestand). */
  steps_avg?: number;
  steps_max?: number;
  duration?: AgentDuration;
  tools?: AgentTool[];
  last_run_at?: string | null;
}

/** Envelope of the file. `since_hours` comes **back**, because the server clamps
 *  (`SINCE_HOURS_MAX`): the heading should name the window that was measured, not the one that
 *  was asked for. */
export interface AgentRecordList {
  agents: AgentRecord[];
  since_hours?: number;
}

// ── The calls ───────────────────────────────────────────────────────────────────────────────

/** Upper bound of one event page. The backend caps by itself (`EVENT_CAP_MAX = 20 000`); 4 000
 *  is the value from the plan and keeps a page small enough that the burst while replaying does
 *  not become a source of judder. */
export const EVENT_PAGE_LIMIT = 4000;

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const officeApi = {
  /** Rooms this user may see.
   *
   *  Two paths, one result: the project tab asks under the project (access is checked by
   *  `get_project_access`, strangers get 404), the global page asks across everything.
   *  `projectId` only **narrows** there, it never authorises. */
  sessions: (scope: Scope,
             opts?: { limit?: number; projectId?: number; sinceHours?: number;
                      status?: SessionStatus }): Promise<SessionList> => {
    const path = scope.kind === "project"
      ? `/projects/${scope.projectId}/office/sessions${qs({ limit: opts?.limit, since_hours: opts?.sinceHours, status: opts?.status })}`
      : `/office/sessions${qs({ limit: opts?.limit, since_hours: opts?.sinceHours, project_id: opts?.projectId, status: opts?.status })}`;
    // Whether the envelope is `{sessions: […]}` or a bare array is decided by the backend. Both
    // become the same result here, which is cheaper than asking and
    // survives the agreement in both directions.
    return api.get<SessionList | SessionSummary[]>(path)
      .then((r) => (Array.isArray(r) ? { sessions: r } : r));
  },

  /** One page of events.
   *
   *  `afterSeq` is **exclusively** for paging and for filling a gap right after a reconnect,
   *  **never** a poller. Why is explained at length in `useOfficeFeed.ts` and in the module
   *  docstring of `api/office_ws.py`: `seq` comes from a `SERIAL` column that is assigned
   *  **before** the commit. */
  events: (sid: Sid, opts?: { limit?: number; afterSeq?: number }): Promise<EventPage> =>
    api.get<EventPage>(
      `/office/sessions/${sid.kind}/${sid.ref}/events`
      + qs({ limit: opts?.limit ?? EVENT_PAGE_LIMIT, after_seq: opts?.afterSeq })),

  /** One page of events across **all** sessions of a time window.
   *
   *  The room of the global page. That is possible because `seq` comes from `run_steps.id`, a
   *  SERIAL column that is monotonic across runs and projects; events of different sessions
   *  therefore form ONE sequence, and the seat (`hash32(run_id) % 12`) is independent of the
   *  session anyway.
   *
   *  The response has the shape of `events()`, only without a `sid` and with the window
   *  instead. Permissions are those of `sessions()`; `projectId` narrows, never authorises. */
  allEvents: (opts?: { sinceHours?: number; limit?: number; afterSeq?: number;
                       projectId?: number }): Promise<EventPage> =>
    api.get<EventPage>("/office/events" + qs({
      since_hours: opts?.sinceHours, limit: opts?.limit ?? EVENT_PAGE_LIMIT,
      after_seq: opts?.afterSeq, project_id: opts?.projectId,
    })),

  /** Cost of the room: a call of its own, because it changes at a completely different rate
   *  than the events and would otherwise keep invalidating the snapshot. */
  cost: (sid: Sid): Promise<CostRollup> =>
    api.get<CostRollup>(`/office/sessions/${sid.kind}/${sid.ref}/cost`),

  /** The personnel file: key figures per **role**, across runs and sessions.
   *
   *  Two paths as with `sessions()`: under the project (access checked by
   *  `get_project_access`) or globally. The time window is **part of the statement** here and
   *  therefore a mandatory field of the display, not a silent default: `run_retention_days`
   *  deletes older runs, and "ever" would simply be a lie.
   *
   *  `agent` narrows to one role, `toolLimit` caps the tool ranking per role. */
  agents: (scope: Scope,
           opts?: { sinceHours?: number; agent?: string; toolLimit?: number }): Promise<AgentRecordList> => {
    const q = qs({ since_hours: opts?.sinceHours, agent: opts?.agent, tool_limit: opts?.toolLimit });
    const path = scope.kind === "project"
      ? `/projects/${scope.projectId}/office/agents${q}`
      : `/office/agents${q}`;
    // As with `sessions()`: whether the envelope is `{agents: […]}` or a bare array is decided
    // by the backend. Both land on the same shape here.
    return api.get<AgentRecordList | AgentRecord[]>(path)
      .then((r) => (Array.isArray(r) ? { agents: r } : r));
  },
};

// ── The live socket ─────────────────────────────────────────────────────────────────────────
//
// Built against real code (`backend/app/api/office_ws.py`): ONE user socket serves the project
// tab **and** the global page; filtering happens server side. The client can only **narrow**
// through `subscribe`, never widen: a subscription to a foreign project yields
// silence, not an error.

/** What the server sends over the socket. */
export type WsIn =
  | { type: "hello"; v: number; user_id: number; is_admin: boolean; projects: number[]; acl_ttl_s: number }
  | { type: "subscribed"; scope: number[] | null }
  | { type: "office_ev"; ev: Ev }
  | { type: "pong" };

/** What the client sends. */
export type WsOut =
  | { type: "subscribe"; scopes: ({ kind: "project"; id: number } | { kind: "global" })[] }
  | { type: "ping" };

/** Address of the user socket. One socket per browser session is enough: the separation by
 *  project is done by the server, not by the number of connections. */
export function officeWsUrl(token: string | null): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/api/ws?token=${encodeURIComponent(token ?? "")}`;
}

/** A `Scope` turned into the `subscribe` message. An object, so that the narrowing comes into
 *  being in exactly one place and the caller does not assemble it by hand. */
export function subscribeMessage(scope: Scope): WsOut {
  return scope.kind === "project"
    ? { type: "subscribe", scopes: [{ kind: "project", id: scope.projectId }] }
    : { type: "subscribe", scopes: [{ kind: "global" }] };
}
