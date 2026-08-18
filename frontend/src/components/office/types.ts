// Layer 0, the contract of the office. Types only, not a single runtime value.
//
// Three things stand here: the event stream from the backend (`Ev`), the command vocabulary of
// the room (`Cmd`) and the state the engine builds from it (`ActorState`, `Frame`). Between
// them lies exactly one pure function (`mapEvent`), and that is the seam.
//
// For the rules see PIXEL-CONTRACT.md. For this file rule 5 matters most: no `enum`, no
// `namespace`, nothing that survives stripping the types.

// ── Kleine Vokabeln ──────────────────────────────────────────────────────────

/** Six images instead of forty tool names. The mapping is in `toolAct.ts` (a table, not a heuristic). */
export type ToolAct = "read" | "write" | "run" | "browse" | "delegate" | "other";

/** What stands on the character's monitor. Derived from `ToolAct` (`toolAct.ts::screenFor`),
 *  not from the tool name, otherwise the screen would claim more than the log gives.
 *  `blank` is a dark screen (no running tool). */
export type ScreenKind = "code" | "log" | "page" | "search" | "link" | "wait" | "blank";

/** The base mood of a display (monitor frame, dock tile). Four states, four colours, the same
 *  as in the timeline, so two views of the same run never contradict each other. */
export type MoodKind = "work" | "wait" | "error" | "done";

/** Spiegelt `Run.status` im Backend. */
export type RunStatus =
  | "running" | "success" | "failed" | "blocked" | "planned" | "loop_exhausted";

/** Colours the edge of a speech bubble and nothing else.
 *  `success|planned → ok` · `failed|loop_exhausted → err` · `blocked → blocked` · otherwise `null`. */
export type Verdict = "ok" | "err" | "blocked" | null;

export type Pose = "sit" | "walk" | "stand";

/** Why somebody waits for a person: a question, a permission request, a plan approval. */
export type GateKind = "question" | "permission" | "plan";

export interface Pt { x: number; y: number }

/** The drawing context, as much of it as the pixel contract allows, and **only** that much.
 *  (Regel 2.1: `fillStyle`, `globalAlpha`, `fillRect`).
 *
 *  Deliberately a structural type of its own instead of `CanvasRenderingContext2D`: that
 *  anchors the rules **in the type system**, so a `ctx.beginPath()` in layer 1 is already a
 *  type error and not merely a review finding. A real `CanvasRenderingContext2D` satisfies the
 *  shape (which is why `fillStyle` as `string | object` is wide enough for `CanvasGradient`),
 *  and the stub of the checker satisfies it too: it simply collects the `fillRect` calls. */
export interface Ctx {
  fillStyle: string | object;
  globalAlpha: number;
  fillRect(x: number, y: number, w: number, h: number): void;
}

// ── Ereignis-Strom (Backend → Frontend) ──────────────────────────────────────
//
// snake_case, because the whole repository speaks snake_case and the backend builds the fields
// exactly that way (`services/office.py::step_events`). A camelCase translation layer would be
// thirty lines without a single thought gained, so it does not exist.

/** The envelope every event carries. */
export interface EvBase {
  /** Schema version of the contract. Currently 1. */
  v: number;
  /** `run_steps.id * 4 + slot`, global monoton, **Ankunftsreihenfolge**.
   *  Slots: 0 = synthesised predecessor (old rows), 1 = the main event,
   *  2 = abgeleiteter Begleiter (`usage`/`file_edit`/`agent_spawn`), 3 = frei.
   *  The log is ordered by `seq`, **never** by `ts`. See `MAX_GAP_MS` in `const.ts`. */
  seq: number;
  /** ISO-8601 with milliseconds. Only for distances in time and the timeline, never for order. */
  ts: string;
  /** Session = one run tree: `"issue:412"` (a ticket room) or `"run:8871"` (job, assistant). */
  sid: string;
  /** On every event, so the websocket bridge can authorise without touching the database. */
  project_id: number | null;
  owner_id: number | null;
  run_id: number;
  /** Stable actor id in the room, of the form `"run:8871"`. The seed of the character is `hash32(agent_id)`. */
  agent_id: string;
}

/** Opens the room: title and origin of the session. */
export interface EvSessionSeen extends EvBase {
  kind: "session_seen";
  title: string;
  issue_key: string | null;
  project_key: string | null;
  started_at: string | null;
}

/** A run enters the room. Also the **only** trigger for a spawn:
 *  `parent_run_id`/`parent_tool_use_id` set means a subagent. (`delegate` is no good for that,
 *  because `runtime.py` awaits the subrun inline and writes the row only at its end.) */
export interface EvRunStart extends EvBase {
  kind: "run_start";
  /** Rolle, z. B. `plan_agent`, `exec_agent`, `review_agent`, `assistant`. */
  agent: string;
  phase: "plan" | "execute";
  provider: string | null;
  model: string | null;
  parent_run_id: number | null;
  parent_tool_use_id: string | null;
  /** 0 = Wurzellauf. */
  spawn_depth: number;
  /** Continuation after context compaction; 0 is the first attempt. */
  continuation_index: number;
  task_id: number | null;
  issue_key: string | null;
}

/** Something from outside: a person, the PM, a job trigger. */
export interface EvUserMessage extends EvBase {
  kind: "user_message";
  source: string;
  text: string;
}

/** Real text of the model. Careful: assistant turns in which the model only called tools carry
 *  the literal `"(Tool-Call)"` in the backend. `mapEvent` filters those, otherwise every agent
 *  im Raum dauernd „(Tool-Call)". */
export interface EvAgentText extends EvBase {
  kind: "agent_text";
  text: string;
}

/** Tokens of **one** model turn, not of the run. Layer 0 forms the totals for the inspector. */
export interface EvUsage extends EvBase {
  kind: "usage";
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  /** Who actually answered: on a fallback run not the configured model. */
  provider: string | null;
  model: string | null;
}

export interface EvToolStart extends EvBase {
  kind: "tool_start";
  tool: string;
  /** A short target for the bubble: path, URL, role. From a table, not guessed. */
  target: string | null;
  /** Brackets `tool_start` and `tool_result`. On old rows `"legacy:<run_step_id>"`. */
  tool_use_id: string;
  args_preview: string | null;
}

export interface EvToolResult extends EvBase {
  kind: "tool_result";
  tool: string;
  tool_use_id: string;
  /** **Three valued.** `null` means *unknown* (an old row without a recognisable error
   *  prefix), not success. Whoever writes `ok ?? true` paints green ticks on guessed data. */
  ok: boolean | null;
  error: string | null;
  /** `null` on the old data path: a tool step knows only one moment there. The room then shows
   *  `TOOL_BUSY_MS` as a substitute duration. */
  duration_ms: number | null;
  result_preview: string | null;
}

/** Abgeleiteter Begleiter eines Schreibwerkzeugs (Slot 2). */
export interface EvFileEdit extends EvBase {
  kind: "file_edit";
  path: string;
}

/** A derived companion (slot 2): a subagent was requested. Draws the spawn line; the character
 *  itself only appears with its `run_start`. */
export interface EvAgentSpawn extends EvBase {
  kind: "agent_spawn";
  child_role: string;
  prompt: string | null;
  tool_use_id: string | null;
  background: boolean;
}

/** The run leaves the room. Comes exactly once per `run_start`; when it is missing the
 *  character stands there forever. */
export interface EvRunEnd extends EvBase {
  kind: "run_end";
  ok: boolean;
  status: RunStatus;
  /** What it blocked on when `status === "blocked"`: `question`, `permission`, … */
  blocker_kind: string | null;
  summary: string | null;
  error: string | null;
  iterations: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  /** Three valued: `true` means priced against the catalog (0.00 is priced as well!), `false`
   *  means no `ProviderModel` entry, `null` means an old row. The interface shows a "≥" when
   *  the pricing is incomplete. */
  cost_priced: boolean | null;
}

/** A message of the system (abort, truncation, compaction). */
export interface EvSystem extends EvBase {
  kind: "system";
  text: string;
}

/** The server rack at the back wall becomes the deployment.
 *
 *  **Exactly these four fields**: the set is the contract, not a selection from it.
 *  `services/office.py::deploy_fields` is the one place where they come into being (the
 *  watcher row and a legacy deployment synthesised while reading both go through it), and
 *  `test_office_normalize.py::FIELD_KEYS["deploy"]` nagelt sie im Backend fest.
 *
 *  `state` has **both ends as real events**, unlike a tool row from old data which knows only
 *  one moment. So the room needs no substitute duration here (see `TOOL_BUSY_MS`), it waits
 *  for the counterpart.
 *
 *  `back` (rolled back) deliberately stands next to `fail`: failed **and** healed is the only
 *  good news in a failure, and merging the two would lose exactly that. */
export interface EvDeploy extends EvBase {
  kind: "deploy";
  deployment_id: number;
  state: "start" | "ok" | "fail" | "back";
  /** What was worked on: the stack directory, or the worktree instead. A label, no more. */
  target: string;
  /** An excerpt of the log (240 characters). For the inspector, not for the stage. */
  log_head: string;
}

/** RESERVED: **never** sent by the backend.
 *
 *  No provider adapter here delivers thinking blocks; Anthropic thinking is not even
 *  requested in the worker and would not arrive as a step of its own. The branch stays in the
 *  union so that a later adapter can fill it without breaking the contract.
 *
 *  Please do not "fix" this by passing `agent_text` through as `thinking`: the timeline has a
 *  colour of its own for thinking, and it would be a lie. */
export interface EvThinking extends EvBase {
  kind: "thinking";
  text: string;
}

export type Ev =
  | EvSessionSeen | EvRunStart | EvUserMessage | EvAgentText | EvUsage
  | EvToolStart | EvToolResult | EvFileEdit | EvAgentSpawn | EvRunEnd
  | EvSystem | EvThinking | EvDeploy;

export type EvKind = Ev["kind"];

// ── Commands, the entire vocabulary of the room ──────────────────────────────
//
// Twelve of them, there are no more. Everything the room can do is a sequence of these.
// `mapEvent(ev, ctx) -> Cmd[]` is pure; the engine does not know `Ev` at all.
//
// Deliberately **absent**:
//   · `confront`, a command that lets two agents contradict each other. There is no such model
//     of dispute here: runs do not contradict one another, they fail or succeed. A command
//     without a data source would be decoration.
//   · `prompt`, showing the instruction text of a spawn as a gesture of its own. Here it sits
//     in `agent_spawn.prompt` and belongs in the inspector, not on the stage.
//
// Eigene Zutat:
//   · `gate` / `resume`, the human in the loop form. `ask_human`, permission requests and plan
//     approvals stop a run until a person answers. That is the most common reason a room
//     stands still, and exactly why one has to see it (`GATE_PULSE_MS`).
//     stands still, and exactly why one has to see it (`GATE_PULSE_MS`).

export type Cmd =
  /** Creates the character or updates its master data. Idempotent, may happen several times. */
  | { k: "ensureActor"; id: string; role: string; issue: string | null;
      phase: string | null; model: string | null; parent?: string }
  /** Thought bubble (dotted). Currently only produced from derived states, not from `thinking`. */
  | { k: "think"; id: string; text: string }
  /** Speech bubble with a typewriter effect (`TYPE_CPS`, `BUBBLE_MS`). */
  | { k: "say"; id: string; text: string }
  /** A tool begins: pose, monitor image and busy state. */
  | { k: "tool"; id: string; act: ToolAct; tool: string; target?: string }
  /** A tool ends. `ok: null` means unknown, so a neutral mark and no green tick. */
  | { k: "toolEnd"; id: string; ok: boolean | null }
  /** A file was written: counts `edits` up and puts a note on the desk. */
  | { k: "edit"; id: string; path: string }
  /** Spawn line from `parent` to `id`. The character `id` only appears with its `ensureActor`. */
  | { k: "spawn"; id: string; parent: string; role: string }
  /** `id` walks to `to` and hands over a result. The core of the choreography. */
  | { k: "deliver"; id: string; to: string; text?: string }
  /** Waits for a person. Stops the character until `resume` arrives. */
  | { k: "gate"; id: string; kind: GateKind; text: string }
  /** The person answered: the gate opens. */
  | { k: "resume"; id: string }
  /** A change of state without a change of place (colours the dock and the bubble edge). */
  | { k: "status"; id: string; status: RunStatus }
  /** Done: a closing bubble, then out through the door (`DONE_LINGER_MS`). */
  | { k: "done"; id: string; ok: boolean; text?: string }
  /** The server rack lights up. `by` is the triggering character, which walks to the rack and
   *  back; when it is missing the rack lights without a gesture (a deployment without a run is
   *  conceivable). `label` is the target (stack or worktree) for layer 2; the stage shows only colour. */
  | { k: "deploy"; state: "start" | "ok" | "fail" | "back"; by?: string; label: string };

export type CmdKind = Cmd["k"];

// ── Zustand ──────────────────────────────────────────────────────────────────

/** One entry per agent. All times are `engine.t` in ms (simulation time, not the wall clock).
 *  Coordinates are **scene** coordinates (`SCENE` 1600×900); rendering multiplies by
 *  `POS_SCALE` and only rounds there, see PIXEL-CONTRACT.md rules 1 and 2.3. */
export interface ActorState {
  /** `"run:8871"` — identisch zu `Ev.agent_id`. */
  id: string;
  role: string;
  issue: string | null;
  phase: string | null;
  model: string | null;

  /** Integer scene coordinate. `y` is the **foot point** (the sorting key of the scene). */
  x: number;
  y: number;
  /** Subpixel accumulator: collects the fractions of the movement so that even tiny `dt` make
   *  progress. It is **never** rendered and never written back rounded. */
  sub: Pt;

  pose: Pose;
  /** Facing left. */
  flip: boolean;
  /** Away: not visible in the room (`deskIndex === -2`). */
  away: boolean;

  /** The running bubble text and the start time for the typewriter effect and `BUBBLE_MS`. */
  say?: string;
  sayAt?: number;
  /** Colours the bubble edge only. */
  verdict: Verdict;
  think?: string;
  /** Start time of the thought bubble (`engine.t`). Without it a thought bubble would have no
   *  expiry and would stand forever, the same role `sayAt` plays for the speech bubble. */
  thinkAt?: number;

  status: RunStatus;
  /** Sitzplatz: `0..11` = Pod (`hash32(runId) % 12`, deterministisch), `-1` = Chefplatz,
   *  `-2` means away. */
  deskIndex: number;

  /** Laufendes Werkzeug. */
  act?: ToolAct;
  tool?: string;
  target?: string;
  /** `engine.t` until which the tool looks busy. `0` means free. */
  busy: number;
  /** Stands at the gate and waits for a person. */
  waiting: boolean;
  gate?: GateKind;

  /** Result of the tool that finished last (three valued, see `EvToolResult.ok`). */
  lastOk?: boolean | null;
  /** Counters for the dock and the inspector. */
  fails: number;
  resolved: number;

  /** The path written last and the total count. */
  edit?: string;
  edits: number;

  /** `engine.t` of the completion: from there `DONE_LINGER_MS` runs until the door. */
  done?: number;
  doneOk?: boolean;

  /** Elternagent (Spawn). */
  parent?: string;
  /** Sichtbare Verbindungslinie bis `until` (`LINK_MS`). */
  link?: { to: string; until: number };
  /** "has listened": the head turns until `heard` (`HEARD_MS`). */
  heard?: number;
  /** Has left the room through the door; stays in the dock, disappears from the stage. */
  retired?: boolean;

  /** `hash32(id)`. The source of **every** variation of this character: look, gait, seat.
   *  Siehe PIXEL-CONTRACT.md Regel 3.2. */
  seed: number;
}

/** A short lived effect above the scene. `t0`/`until` are `engine.t`; the progress is
 *  `(t - t0) / (until - t0)`, never a counter of its own (rule 3.4). */
export interface Fx {
  kind: FxKind;
  /** Scene coordinates; `y` is the foot point here as well. */
  x: number;
  y: number;
  /** The target, only on `"link"`. */
  to?: Pt;
  t0: number;
  until: number;
  /** On `"emote"` exactly one character from the art table. */
  text?: string;
  seed: number;
}

/** `emote` = a pop above the head (instead of separate cheer or frustration poses) · `spark` = a tool spark ·
 *  `link` = a spawn or handover line · `drop` = a note falls onto the desk. */
export type FxKind = "emote" | "spark" | "link" | "drop";

/** What the server rack shows. `idle` is the initial state and is set by **no** event: it means
 *  "no deployment has happened since this room was opened". */
export type RackState = "idle" | "start" | "ok" | "fail" | "back";

/** The server rack, the only state in the `Frame` that hangs on no character.
 *
 *  `since` is the `engine.t` of the change; every animation phase is `(t - since)` and **never**
 *  a counter that is incremented (PIXEL-CONTRACT.md 3.4). There is deliberately no `until`: the
 *  state does not expire, it is replaced. See `engine.ts::apply({k:"deploy"})`. */
export interface Rack {
  state: RackState;
  since: number;
  /** Target of the deployment (stack or worktree). The stage does not show it, layer 2 may. */
  label: string;
}

/** The only thing layer 1 gets to see. `actors` is **sorted by `y`** (painter's algorithm) and
 *  a copy: the engine never hands out its own collection. */
export interface Frame {
  /** Simulation time in ms since the `t0` of the session. */
  t: number;
  actors: ActorState[];
  fx: Fx[];
  /** The server rack. It stands next to `actors`, because it is bound to no actor. */
  rack: Rack;
}

/** One row of the log, in **arrival order** (`seq`), never sorted by `ts`.
 *  `ts` here is already the parsed wall clock in ms. */
export interface LogEntry {
  ts: number;
  seq: number;
  cmds: Cmd[];
}

// ── Look (layer 1 reads it, layer 0 builds it from the seed) ─────────────────

/** The 19 parts of a character, resolved from `seed`: head 3 · hair 5 · torso 3 · arms 4 · legs 4.
 *  The colour fields are **palette keys**, not CSS colours: the palette is resolved once per
 *  theme change (`pixel/palette.ts`). */
export interface Look {
  head: number;   // 0..2
  hair: number;   // 0..4
  torso: number;  // 0..2
  arms: number;   // 0..3
  legs: number;   // 0..3
  skin: string;
  hairCol: string;
  shirtCol: string;
  pantsCol: string;
}

/** The gait, likewise from the seed. With it twelve people walk recognisably differently
 *  without anything being rolled anywhere. */
export interface Gait {
  /** Factor on `SPEED_PX_PER_S`, `1 ± PACE_SPREAD`. */
  speed: number;
  /** Up and down amplitude in **buffer pixels** (0..1). */
  bob: number;
  /** Start phase of the four frame walk cycle, 0..1. */
  phase: number;

  // Added later. The reason: with `speed/bob/phase` alone twelve people walk in the same rhythm
  // and differ only in speed, which from two metres away looks like one single animation. The
  // four fields below are the silhouette features one really sees. All come from salts of their
  // own (rule 3.2), produced by `pixel/palette.ts::gaitOf`, the only maker of a `Gait`.


  /** Leg swing of the walk cycle in **buffer pixels** (2..4). */
  stride: number;
  /** Lean of the upper body in the walking direction, 0..1 (0 is bolt upright). */
  lean: number;
  /** Armausschlag in **Pufferpixeln** (1..2). */
  swing: number;
  /** Start phase of the arm cycle, 0..1, offset against `phase`, because arms and legs swing
   *  in opposite directions; without the offset the gait looks like marching. */
  armPhase: number;
}

// ── Raum (statische Geometrie, Szenen-Koordinaten) ──────────────────────────

/** A workplace. `sit` is the foot point of the seated character, not the centre of the chair. */
export interface Seat {
  /** Centre of the desktop. */
  desk: Pt;
  /** Foot point of the character when it sits here. */
  sit: Pt;
  /** Faces left at this seat. */
  flip: boolean;
}

/** Built once deterministically (`room.ts`) and only read afterwards. */
export interface Room {
  /** `seats[0..11]` are the twelve pod seats (`deskIndex` 0..11), `seats[12]` is the boss seat
   *  (`deskIndex === -1`). Exactly `MAX_SEATS` entries; `deskIndex === -2` has no seat. */
  seats: Seat[];
  /** The threshold: characters enter and leave the room here. */
  door: Pt;
  /** Centre of the round table. */
  table: Pt;
  /** Standing places around the table (huddle), in a fixed order. */
  huddle: Pt[];
  /** Kaffeeecke (`IDLE_COFFEE_MS`). */
  coffee: Pt;
  /** Gathering point outside the picture for `deskIndex === -2`. */
  away: Pt;
  /** Standing place **in front of** the server rack: no seat, no actor, only a target point.
   *  Exactly for that the deploy gesture needs no sixth `TripKind`: it is a `deliver` without a
   *  target actor and thereby inherits door detection, foot dust, spread of pace and
   *  dt-Split-Invarianz gratis. */
  rack: Pt;
}

// ── Roster & Zeitleiste ──────────────────────────────────────────────────────

/** A run as the read API delivers it next to `events[]`, straight from `runs`.
 *  The roster earns its place because the event window is truncated at the **oldest** end:
 *  without it the `run_start` events of a long session would be lost and the room
 *  bliebe leer. */
export interface RosterEntry {
  agent_id: string;
  run_id: number;
  agent: string;
  phase: string | null;
  status: RunStatus;
  issue_key: string | null;
  project_id: number | null;
  project_key: string | null;
  provider: string | null;
  model: string | null;
  parent_run_id: number | null;
  spawn_depth: number;
  started_at: string | null;
  ended_at: string | null;
  iterations: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  cost_priced: boolean | null;
}

export type Roster = RosterEntry[];

/** One bar of the timeline, `TIMELINE_BUCKET_MS` wide. `t` is the start of the window in
 *  `engine.t` time. The four counters are the four colours, and there are no more colours. */
export interface Bucket {
  t: number;
  says: number;
  tools: number;
  thinks: number;
  errors: number;
}

/** Day or evening office. Comes from `<html data-theme>`, **not** from the real time of day,
 *  which would break determinism. */
export type Grade = "day" | "night";
