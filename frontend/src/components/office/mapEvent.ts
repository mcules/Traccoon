// Layer 0, the seam: a backend event becomes commands of the room.
//
// `mapEvent` is **pure**: no DOM, no clock, no randomness, no state except the roster passed
// in. Exactly that makes rewinding without snapshots possible: "new engine, replay the log
// from the start" yields the same commands in the same order and therefore the same picture.
// Any exception (a counter, a remembered "already said that") would break it instantly.
//
// The engine does not know `Ev` at all; it only sees `Cmd[]`. This file is the single place
// where both vocabularies meet.

import { toolAct } from "./toolAct.ts";
import type { Cmd, Ev, GateKind, Roster, RosterEntry, RunStatus } from "./types.ts";

// ── Truncation ───────────────────────────────────────────────────────────────
//
// Ground rule: a bubble you cannot read at a glance is no longer a bubble but a text field
// above a 16x24 figure.

/** Gedankenblase. */
const THINK_CHARS = 90;
/** Speech bubble: the first sentence. */
const SAY_CHARS = 120;
/** Tool target (path, URL, role) below the monitor. */
const TARGET_CHARS = 60;

/** From this length on, a model text is no longer a sentence someone says but a paragraph
 *  someone thinks. The limit deliberately sits above `SAY_CHARS`: a status sentence with a
 *  subordinate clause should still be spoken, a report should not. */
const THINK_FROM_CHARS = 180;

/** All whitespace to a single space. Must run **before** the sentence search: otherwise
 *  "…done.\nNext…" would not end at the line break although a sentence obviously ends
 *  there. */
function squash(text: string): string {
  return (text || "").replace(/\s+/g, " ").trim();
}

/** Truncates to `max` characters without cutting a word in half where that can be avoided.
 *  The ellipsis counts along: `clip(s, 90)` never yields more than 90 characters. */
function clip(text: string, max: number): string {
  const t = squash(text);
  if (t.length <= max) return t;
  const hard = t.slice(0, max - 1);
  const space = hard.lastIndexOf(" ");
  return (space > max * 0.6 ? hard.slice(0, space) : hard) + "…";
}

/** The first sentence, at most `max` characters long.
 *
 *  Two conditions, each against a concrete accident:
 *
 *  1. A sentence end is `[.!?]` **followed by whitespace or the end of the text**. Without
 *     that, "latency rose by 1.4x to 320 ms" would become the fragment "latency rose by 1.".
 *  2. A period **after a digit** does not end a sentence. Point 1 alone does not save the
 *     numbered list: in "1. read file" there very much is a space after the period, and the
 *     bubble would then literally say "1.". Bullet markers and version numbers are the same
 *     case. This does not concern `!`/`?`.
 *
 *  The price is a sentence like "There were 12." which does not count as finished and is
 *  hard-truncated to `max` instead. That is the more harmless half of the trade. */
function firstSentence(text: string, max: number): string {
  const t = squash(text);
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (c !== "." && c !== "!" && c !== "?") continue;
    if (c === "." && i > 0 && t[i - 1] >= "0" && t[i - 1] <= "9") continue;
    const next = t[i + 1];
    if (next === undefined || next === " ") return clip(t.slice(0, i + 1), max);
  }
  return clip(t, max);
}

// ── Kleine Nachschlagehilfen ─────────────────────────────────────────────────

/** Actor id of a run. Must match `services/office.py::_event` (`f"run:{run_id}"`), otherwise
 *  the room shows two figures for the same run. */
function actorId(runId: number): string {
  return `run:${runId}`;
}

/** A linear scan instead of a map: `mapEvent` is pure and must not remember anything, and a
 *  roster has dozens of entries, not thousands. Building a map per call would be more
 *  expensive than the scan itself. */
function rosterOf(roster: Roster, id: string): RosterEntry | undefined {
  for (const entry of roster) if (entry.agent_id === id) return entry;
  return undefined;
}

/** The envelope of every event carries `run_id`; the figure comes from it.
 *
 *  This `ensureActor` stands **before** every command that needs a figure. The reason is the
 *  truncation: the event window is cut from the oldest end, so a `run_start` can be missing.
 *  Without this lead-in, the first commands of a long session would be about a figure that
 *  never existed. `ensureActor` is idempotent by contract: sending it several times is
 *  explicitly allowed and here the only stateless solution.
 *
 *  Without a roster entry the master data stays empty: a nameless figure is more honest than
 *  an invented role. */
function ensure(ev: Ev, roster: Roster): Cmd {
  const id = ev.agent_id;
  const row = rosterOf(roster, id);
  const cmd: Cmd = {
    k: "ensureActor", id,
    role: row ? row.agent : "",
    issue: row ? row.issue_key : null,
    phase: row ? row.phase : null,
    model: row ? row.model : null,
  };
  if (row && row.parent_run_id !== null) cmd.parent = actorId(row.parent_run_id);
  return cmd;
}

// ── Werkzeug-Ergebnis ────────────────────────────────────────────────────────

/** What the runtime really returns as an error, the same list as in
 *  `services/office.py::ERROR_PREFIXES`. */
const ERROR_PREFIXES = ["FEHLER:", "FEHLER ", "TOOL-FEHLER:", "FS-FEHLER:", "CHECK-FEHLER:",
                        "❌", "⛔"];

/** The result of a tool, **three valued**.
 *
 *  `null` means *unknown*, not *successful*. In old data nobody measured whether the call
 *  went through; a green tick on that would be a claim about data that does not exist.
 *  Whoever writes `ok ?? true` here paints half the room green.
 *
 *  The fallback to sniffing text deliberately stands **at the very end** and only for the
 *  case `ok === null`: today the backend already does that (`tool_ok`) for both paths, but
 *  an event stream from an older version does not know this detection, and a proven error is
 *  worth more than an "unknown". Nothing is invented in the process: `null` is only
 *  sharpened to `false`, never to `true`. */
function resultOk(ok: boolean | null, error: string | null, preview: string | null): boolean | null {
  if (ok !== null) return ok;
  const text = ((error || "") + (preview || "")).replace(/^\s+/, "");
  for (const p of ERROR_PREFIXES) if (text.startsWith(p)) return false;
  return null;
}

// ── Gates ────────────────────────────────────────────────────────────────────

/** `Run.blocker_kind` mapped to the kind of gate.
 *
 *  The values come from `worker/runtime.py` (`ask_human`, `permission`, `assistant_perm`)
 *  and `worker/__main__.py` (`review`, `question` on a blocked sub-run). Anything unknown
 *  becomes a question: that somebody is waiting is proven, only what for is not. */
const GATE_OF: Record<string, GateKind> = {
  ask_human: "question",
  question: "question",
  review: "question",
  permission: "permission",
  assistant_perm: "permission",
};

const RUN_STATUS: readonly string[] = [
  "running", "success", "failed", "blocked", "planned", "loop_exhausted",
];

/** The backend builds `status` from a free mapping (`_run_end_fields`) and can deliver an
 *  empty string doing so. The type claims `RunStatus`; it is checked anyway, because a
 *  `status` command with `""` would colour dock and bubble border "unknown instead of not at all". */
function isRunStatus(s: string): s is RunStatus {
  return RUN_STATUS.indexOf(s) >= 0;
}

// ── Announcements from outside ───────────────────────────────────────────────

/** Which `user_message` sources are really spoken in the room.
 *
 *  Deliberately an allow list, not a deny list: `source` is a free field (`step.target`,
 *  fallback `"system"`), and turning every system, hook or follow-up message into speech
 *  would fill the room with bubbles nobody wrote. What is missing here is silent, and a
 *  silent line in the text stream is the smaller damage. */
const SAY_SOURCES: readonly string[] = ["ticket", "user", "human", "chat", "mail", "pm"];

// ── Deployments ──────────────────────────────────────────────────────────────

/** The four states of the server rack, word for word `services/office.py::DEPLOY_STATES`. */
const DEPLOY_STATES: readonly string[] = ["start", "ok", "fail", "back"];

/** Like `isRunStatus`, and for the same reason: `deploy_fields` builds `state` with
 *  `str(state or "")` from a JSON body. A damaged row delivers `""`, and a rack command with
 *  an empty state would make the rack glow in a colour nobody
 *  ever assigned. */
function isDeployState(s: string): s is "start" | "ok" | "fail" | "back" {
  return DEPLOY_STATES.indexOf(s) >= 0;
}

/** The compile-time guard for `mapEvent`.
 *
 *  The parameter is `never`: as long as every kind of the `Ev` union above has its own
 *  `case`, `ev` is `never` in the `default` branch and the call compiles. If a kind is added
 *  without anyone adding a branch here, **`tsc`** breaks, which is exactly what the
 *  exhaustive `switch` without a `default` used to provide.
 *
 *  At runtime it returns `[]`, and that is the actual point: a backend may run ahead of the
 *  interface. Without this branch `mapEvent` returns `undefined` for an unknown kind,
 *  `recorder.push` puts that into the log as `cmds` unchecked, and the room dies on the next
 *  `advance` with `for (const c of undefined)`. An unknown event should be silent, not
 *  lethal. */
function unknown(_ev: never): Cmd[] {
  return [];
}

// ── The translation ──────────────────────────────────────────────────────────

/** One event to its commands. An empty array means: nothing happens in the room for this. */
export function mapEvent(ev: Ev, roster: Roster): Cmd[] {
  switch (ev.kind) {
    // Only the header of the session (title, origin). It belongs in the bar above the stage,
    // not on the floor: layer 2 reads it straight from the event.
    case "session_seen":
      return [];

    case "run_start": {
      const cmds: Cmd[] = [{
        k: "ensureActor", id: ev.agent_id, role: ev.agent, issue: ev.issue_key,
        phase: ev.phase, model: ev.model,
        ...(ev.parent_run_id !== null ? { parent: actorId(ev.parent_run_id) } : {}),
      }];
      // **The** spawn moment. `delegate` is no good for it: `worker/runtime.py` awaits the
      // sub-run inline, so the `delegate` row only comes into being at its END, and the
      // sub-agent would get its line retroactively, long after it has finished.
      if (ev.parent_run_id !== null) {
        cmds.push({ k: "spawn", id: ev.agent_id, parent: actorId(ev.parent_run_id),
                    role: ev.agent });
      }
      return cmds;
    }

    case "user_message": {
      const text = squash(ev.text);
      if (!text) return [];
      if (SAY_SOURCES.indexOf((ev.source || "").toLowerCase()) < 0) return [];
      // The agent says its assignment out loud: the room has no narrator, and that somebody
      // knows what they are working on is the information the viewer is looking for.
      return [ensure(ev, roster), { k: "say", id: ev.agent_id, text: firstSentence(text, SAY_CHARS) }];
    }

    case "agent_text": {
      const text = squash(ev.text);
      if (!text) return [];
      // `"(Tool-Call)"` is the literal placeholder of the runtime for a turn in which the
      // model only called tools (`resp.text or "(Tool-Call)"`). Unfiltered, every agent in the
      // office would say "(Tool-Call)" without pause.
      //
      // Today the backend already separates the two cases: for a pure tool turn the worker
      // writes `kind="usage"` instead of `kind="agent_text"`, and `step_events` filters the
      // placeholder in both paths on top. The check here is the fallback for old data and for
      // streams from an older backend version; it costs one comparison.
      if (text === "(Tool-Call)") return [];
      const head = ensure(ev, roster);
      return text.length >= THINK_FROM_CHARS
        ? [head, { k: "think", id: ev.agent_id, text: clip(text, THINK_CHARS) }]
        : [head, { k: "say", id: ev.agent_id, text: firstSentence(text, SAY_CHARS) }];
    }

    // Reserved: no provider adapter in Traccoon delivers thinking blocks, the backend never
    // emits the kind. The branch stands here filled in anyway so that a later adapter does not
    // have to touch this file first, and because a silent `default` case is the place where a
    // new event disappears unnoticed.
    case "thinking": {
      const text = squash(ev.text);
      if (!text) return [];
      return [ensure(ev, roster), { k: "think", id: ev.agent_id, text: clip(text, THINK_CHARS) }];
    }

    // Tokens of a model turn. `ActorState` keeps no token count and `status` carries only the
    // `RunStatus`: there simply is no command that could take the number. It is not lost
    // regardless: timeline, dock and inspector read it in layer 2 straight from the event
    // stream. Inventing a `status` command here that transports nothing would be one line of
    // noise per model turn.
    case "usage":
      return [];

    case "tool_start": {
      const target = clip(ev.target || "", TARGET_CHARS);
      return [ensure(ev, roster), {
        k: "tool", id: ev.agent_id, act: toolAct(ev.tool), tool: ev.tool,
        ...(target ? { target } : {}),
      }];
    }

    case "tool_result":
      return [ensure(ev, roster), {
        k: "toolEnd", id: ev.agent_id,
        ok: resultOk(ev.ok, ev.error, ev.result_preview),
      }];

    case "file_edit": {
      const path = squash(ev.path);
      if (!path) return [];
      return [ensure(ev, roster), { k: "edit", id: ev.agent_id, path }];
    }

    // Deliberately without a command. The spawn line comes from the `run_start` of the CHILD
    // (see above); drawing another one here would give two lines for one event, one of them to
    // a figure that does not even exist at that moment. The instruction text of the spawn
    // (`prompt`) belongs in the inspector, not on the stage.
    case "agent_spawn":
      return [];

    case "run_end": {
      const id = ev.agent_id;
      const cmds: Cmd[] = [ensure(ev, roster)];
      if (isRunStatus(ev.status)) cmds.push({ k: "status", id, status: ev.status });

      // Handover to the parent run, the core of the choreography: the figure walks over and
      // passes its result on. Only with a roster, because the `run_end` itself does not know
      // its parent.
      //
      // On `blocked` **not**: a blocked sub-run has nothing to deliver. The figure would walk
      // over, hand over nothing and then raise its hand, so the handover would be a gesture
      // over an empty result.
      const row = rosterOf(roster, id);
      if (row && row.parent_run_id !== null && ev.status !== "blocked") {
        const text = firstSentence(ev.summary || ev.error || "", SAY_CHARS);
        cmds.push({ k: "deliver", id, to: actorId(row.parent_run_id),
                    ...(text ? { text } : {}) });
      }

      // Raise a hand instead of disappearing. A run waiting for a human is the most common
      // reason for a silent room, and a silent room without a visible reason reads like a
      // crash. `planned` belongs here although it carries no `blocker_kind`: a submitted plan
      // waits for an approval just the same.
      const gate: GateKind | undefined =
        ev.status === "planned" ? "plan"
          : (ev.blocker_kind ? (GATE_OF[ev.blocker_kind] || "question") : undefined);
      if (gate) {
        cmds.push({ k: "gate", id, kind: gate,
                    text: firstSentence(ev.summary || ev.error || "", SAY_CHARS) });
      }

      // `done` sends the figure through the door after `DONE_LINGER_MS`. On `blocked` that
      // must not happen: there it stands at the gate waiting for an answer, and that is
      // exactly what should be visible. `planned` on the other hand really is finished (the
      // run delivered), the figure only holds the plan up as it leaves.
      if (ev.status !== "blocked") {
        const ok = ev.status === "success" || ev.status === "planned";
        const text = firstSentence(ev.summary || ev.error || "", SAY_CHARS);
        cmds.push({ k: "done", id, ok, ...(text ? { text } : {}) });
      }
      return cmds;
    }

    // Abort, truncation, compaction: without a command in v1. These messages concern the
    // process, not the room; they belong in the text stream and in the timeline. Letting a
    // figure speak them would put words into the agent's mouth that the system
    // said.
    case "system":
      return [];

    // The server rack. Exactly one command per state change: the gesture (walking there,
    // walking back) and the verdict (`emote`) are built from it by the engine, not by this file.
    //
    // `ensure` stands before it as everywhere: the row can come from a window whose
    // `run_start` was cut off at the front, and an existing deployment even hangs its borrowed
    // `seq` on a **foreign** step row.
    case "deploy": {
      if (!isDeployState(ev.state)) return [];
      return [ensure(ev, roster), {
        k: "deploy", state: ev.state, by: ev.agent_id,
        label: clip(ev.target || "", TARGET_CHARS),
      }];
    }

    default:
      return unknown(ev);
  }
}
