// The event log `office-check.mjs` checks against. Frozen.
//
// **Not invented but reproduced.** Every line here has exactly the shape
// `backend/app/services/office.py::step_events` produces: the same key set
// `backend/tests/test_office_normalize.py::FIELD_KEYS` records as the contract, the same
// envelope from `_event`, the same `seq` formula `run_steps.id * 4 + slot`. A fixture that
// sends something else checks a view that does not exist.
//
// Deliberately included, because every one of these shapes triggers something else in the room:
//
//   · **three runs**: a root run (8871, which gets the chief's seat), its **delegated
//     sub-run** (8872, with `parent_run_id` set, so a spawn line and a handover at the end)
//     and a second root run (8873, the assistant).
//   · **tools with success AND failure**: `ok: true`, `ok: false` with an error prefix and
//     once `ok: null` (an old row: unknown, **not** success).
//   · **one `gate`**: 8873 ends `blocked`/`ask_human`, the figure raises its hand and
//     precisely does NOT go through the door.
//   · **gaps in time**: one beyond `MAX_GAP_MS` (20 s), so that the clamping in the replay
//     really takes hold, and several small ones.
//   · **a `ts` that runs backwards relative to `seq`** (line 454): under
//     `WORKER_CONCURRENCY > 1` the normal case and the cause of the lower clamping.
//   · **identical timestamps** (`agent_text` plus `usage`, `tool_start` plus `agent_spawn`): they
//     have to take effect together before the clock runs on.
//   · **exactly one `run_end` per run.** If it is missing, the figure stands forever.
//   · **a deployment with both ends** (`deploy` `start` to `fail`): the server rack is the
//     only state in the `Frame` that hangs off no figure, and without these two lines no
//     golden frame would contain a glowing rack. The ops hashes would then never check the new
//     drawing: a check that does not execute the new code is theatre.
//
// Whoever changes something here changes `tools/golden.json` with it, deliberately (`--bless`).

const SID = "issue:412";
const PROJECT_ID = 27;
const OWNER_ID = 3;

/** Zero point of the log. UTC with milliseconds, exactly like `services/office.py::_ts`. */
const T0 = Date.parse("2026-08-05T11:22:33.000Z");

/** ms after `T0` to ISO-8601 with milliseconds. */
function ts(ms) {
  const d = new Date(T0 + ms);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`
    + `T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
    + `.${p(d.getUTCMilliseconds(), 3)}Z`;
}

/** `seq = run_steps.id * 4 + slot`. Slots: 0 predecessor · 1 main · 2 derived · 3 free. */
function seq(stepId, slot) {
  return stepId * 4 + slot;
}

/** The common envelope, field by field `services/office.py::_event`. */
function ev(runId, stepId, slot, atMs, kind, fields) {
  return {
    v: 1, seq: seq(stepId, slot), ts: ts(atMs), sid: SID,
    project_id: PROJECT_ID, owner_id: OWNER_ID,
    run_id: runId, agent_id: `run:${runId}`,
    kind, ...fields,
  };
}

// ── The log ──────────────────────────────────────────────────────────────────

export const EVENTS = [
  // Header of the room. `seq: 0`: it stands before everything else (`api/office.py` pushes it
  // in front with `events.insert(0, …)`).
  ev(8871, 0, 0, 0, "session_seen", {
    title: "Anmeldung schlägt bei Umlauten fehl", issue_key: "TRA-412",
    project_key: "TRA", started_at: ts(0),
  }),

  // ── Wurzellauf 8871 ────────────────────────────────────────────────────────
  ev(8871, 100, 1, 0, "run_start", {
    agent: "exec_agent", phase: "execute", provider: "claude_code", model: "sonnet",
    parent_run_id: null, parent_tool_use_id: null, spawn_depth: 0,
    continuation_index: 0, task_id: null, issue_key: "TRA-412",
  }),
  ev(8871, 101, 1, 1200, "user_message", {
    source: "ticket",
    text: "Beim Login mit Umlauten im Passwort kommt ein 500er. Bitte fixen und testen.",
  }),
  ev(8871, 102, 1, 3400, "agent_text", {
    text: "Ich schaue mir zuerst den Login-Handler an.",
  }),
  ev(8871, 102, 2, 3400, "usage", {
    in_tokens: 4210, out_tokens: 96, cache_read_tokens: 3800, cache_write_tokens: 0,
    provider: "claude_code", model: "sonnet",
  }),

  ev(8871, 103, 1, 4100, "tool_start", {
    tool: "fs_read", target: "backend/app/api/auth.py", tool_use_id: "tu-1",
    args_preview: '{"path": "backend/app/api/auth.py"}',
  }),
  ev(8871, 104, 1, 4350, "tool_result", {
    tool: "fs_read", tool_use_id: "tu-1", ok: true, error: "", duration_ms: 243,
    result_preview: "from fastapi import APIRouter\n…",
  }),

  // Failure with a proven error prefix (`ERROR_PREFIXES`).
  ev(8871, 105, 1, 5000, "tool_start", {
    tool: "codegraph", target: "verify_password", tool_use_id: "tu-2",
    args_preview: '{"query": "verify_password"}',
  }),
  ev(8871, 106, 1, 5900, "tool_result", {
    tool: "codegraph", tool_use_id: "tu-2", ok: false,
    error: "FEHLER: kein Index für diesen Worktree",
    duration_ms: 902, result_preview: "FEHLER: kein Index für diesen Worktree",
  }),

  // Delegation. The spawn hangs off the **start** (`step_events`), not off the result: the
  // sub-run is awaited inline.
  ev(8871, 107, 1, 7000, "tool_start", {
    tool: "delegate", target: "review_agent", tool_use_id: "tu-3",
    args_preview: '{"role": "review_agent", "task": "Patch gegenlesen"}',
  }),
  ev(8871, 107, 2, 7000, "agent_spawn", {
    child_role: "review_agent", prompt: "Patch gegenlesen", tool_use_id: "tu-3",
    background: false,
  }),

  // ── Unterlauf 8872 ─────────────────────────────────────────────────────────
  ev(8872, 108, 1, 7200, "run_start", {
    agent: "review_agent", phase: "execute", provider: "codex", model: "gpt-5-codex",
    parent_run_id: 8871, parent_tool_use_id: "tu-3", spawn_depth: 1,
    continuation_index: 0, task_id: null, issue_key: "TRA-412",
  }),
  // Above `THINK_FROM_CHARS` (180), so it becomes a thinking bubble, not a speech bubble.
  ev(8872, 109, 1, 9000, "agent_text", {
    text: "Der Handler dekodiert das Passwort mit latin-1, bevor er es an bcrypt reicht; bei "
      + "einem Umlaut im Klartext wirft das eine UnicodeDecodeError. Ich prüfe, ob dieselbe "
      + "Stelle auch beim Registrieren steht, sonst schlagen Anlegen und Anmelden verschieden fehl.",
  }),
  ev(8872, 110, 1, 10500, "tool_start", {
    tool: "fs_write", target: "backend/app/api/auth.py", tool_use_id: "tu-4",
    args_preview: '{"path": "backend/app/api/auth.py", "content": "…"}',
  }),
  ev(8872, 111, 1, 10900, "tool_result", {
    tool: "fs_write", tool_use_id: "tu-4", ok: true, error: "", duration_ms: 61,
    result_preview: "geschrieben: backend/app/api/auth.py",
  }),
  // Derived companion (slot 2), only on a proven success of an `EDIT_TOOLS`.
  ev(8872, 111, 2, 10900, "file_edit", { path: "backend/app/api/auth.py" }),
  ev(8872, 112, 1, 12000, "run_end", {
    ok: true, status: "success", blocker_kind: null,
    summary: "Kodierung auf utf-8 umgestellt, Testfall ergänzt.", error: "",
    iterations: 4, in_tokens: 18400, out_tokens: 1120, cache_read_tokens: 16000,
    cost_usd: 0.0412, cost_priced: true,
  }),

  // `ts` runs BACKWARDS relative to `seq`: the usage companion of the sub-run arrives later
  // but carries the earlier time. Without the lower clamping that would turn the engine back.
  ev(8872, 113, 2, 11800, "usage", {
    in_tokens: 900, out_tokens: 210, cache_read_tokens: 800, cache_write_tokens: 0,
    provider: "codex", model: "gpt-5-codex",
  }),
  ev(8871, 113, 1, 12200, "tool_result", {
    tool: "delegate", tool_use_id: "tu-3", ok: true, error: "", duration_ms: 5180,
    result_preview: "review_agent: Kodierung auf utf-8 umgestellt, Testfall ergänzt.",
  }),

  // ── The server rack goes on ────────────────────────────────────────────────
  // A real row of the watcher (`services/deploy_watch.py`), slot 1, the fields exactly
  // `services/office.py::deploy_fields`. `log_head` is empty at the start: there is no log yet.
  // The triggering figure is the root run: it has just got the review back.
  ev(8871, 114, 1, 13500, "deploy", {
    deployment_id: 341, state: "start",
    target: "/opt/docker/stacks/traccoon", log_head: "",
  }),

  // ── Gap: 31.5 s without an event (beyond MAX_GAP_MS = 20 s) ────────────────

  // ── Zweiter Wurzellauf 8873 (Assistent) ────────────────────────────────────
  ev(8873, 115, 1, 45000, "run_start", {
    agent: "assistant", phase: "execute", provider: "claude_code", model: "sonnet",
    parent_run_id: null, parent_tool_use_id: null, spawn_depth: 0,
    continuation_index: 0, task_id: null, issue_key: "TRA-412",
  }),
  ev(8873, 116, 1, 45500, "user_message", {
    source: "chat", text: "Wie viele Tickets hängen noch an diesem Fehler?",
  }),
  ev(8873, 117, 1, 46200, "tool_start", {
    tool: "traccoon_list_issues", target: null, tool_use_id: "tu-5",
    args_preview: '{"project": "TRA"}',
  }),
  // `ok: null`: an old row without a recognisable prefix. Unknown, NOT success.
  ev(8873, 118, 1, 46900, "tool_result", {
    tool: "traccoon_list_issues", tool_use_id: "tu-5", ok: null, error: "",
    duration_ms: null, result_preview: "TRA-412, TRA-418",
  }),
  ev(8873, 119, 1, 48000, "run_end", {
    ok: null, status: "blocked", blocker_kind: "ask_human",
    summary: "Soll TRA-418 mit erledigt werden?", error: "",
    iterations: 2, in_tokens: 3100, out_tokens: 240, cache_read_tokens: 2900,
    cost_usd: 0.0071, cost_priced: false,
  }),

  // System message: deliberately triggers no command (`mapEvent` returns `[]`).
  ev(8871, 120, 1, 48500, "system", {
    text: "Kontext kompaktiert (42 Nachrichten → 12).",
  }),

  // ── Conclusion of the root run ─────────────────────────────────────────────
  ev(8871, 121, 1, 52000, "agent_text", { text: "Fix ist drin, Prüfung läuft grün." }),

  // The counterpart to the `start` above, with **the same** `deployment_id`. Exactly for that
  // the rack state needs no expiry: both ends are real events. `log_head` carries the guard
  // text that all 56 failed deployments carry in reality (`api/deployments.py`); the backend
  // already sends it truncated (240 characters).
  ev(8871, 122, 1, 52500, "deploy", {
    deployment_id: 341, state: "fail",
    target: "/opt/docker/stacks/traccoon",
    log_head: "Abgelehnt: Self-Deploy nur über das explizite Wartungs-Kommando.",
  }),

  ev(8871, 123, 1, 54000, "run_end", {
    ok: true, status: "success", blocker_kind: null,
    summary: "Login akzeptiert jetzt Umlaute; Regressionstest ergänzt.", error: "",
    iterations: 9, in_tokens: 52000, out_tokens: 3400, cache_read_tokens: 47000,
    cost_usd: 0.1938, cost_priced: true,
  }),
];

/** The roster as the read API delivers it beside `events[]` (`api/office.py::_agent_row`).
 *  `mapEvent` needs it for the role, the parent run and the handover at the end of a run. */
export const ROSTER = [
  {
    agent_id: "run:8871", run_id: 8871, agent: "exec_agent", phase: "execute",
    status: "success", issue_key: "TRA-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "claude_code", model: "sonnet", parent_run_id: null, spawn_depth: 0,
    started_at: ts(0), ended_at: ts(54000), iterations: 9,
    in_tokens: 52000, out_tokens: 3400, cache_read_tokens: 47000,
    cost_usd: 0.1938, cost_priced: true,
  },
  {
    agent_id: "run:8872", run_id: 8872, agent: "review_agent", phase: "execute",
    status: "success", issue_key: "TRA-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "codex", model: "gpt-5-codex", parent_run_id: 8871, spawn_depth: 1,
    started_at: ts(7200), ended_at: ts(12000), iterations: 4,
    in_tokens: 18400, out_tokens: 1120, cache_read_tokens: 16000,
    cost_usd: 0.0412, cost_priced: true,
  },
  {
    agent_id: "run:8873", run_id: 8873, agent: "assistant", phase: "execute",
    status: "blocked", issue_key: "TRA-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "claude_code", model: "sonnet", parent_run_id: null, spawn_depth: 0,
    started_at: ts(45000), ended_at: ts(48000), iterations: 2,
    in_tokens: 3100, out_tokens: 240, cache_read_tokens: 2900,
    cost_usd: 0.0071, cost_priced: false,
  },
];

/** Beginning and end of the log in epoch ms: the bounds checking happens between. */
export const T_FROM = T0;
export const T_TO = T0 + 54000;

/** The eight moments of the golden picture, as an offset to `T_FROM`.
 *
 *  Chosen, not spread: an even grid does not hit the interesting moments. Every point here
 *  stands for a state only it shows:
 *
 *    0      arrival: the figure still stands at the door, nothing has run
 *    1200   in the middle of walking in: the only point where an interpolation is checked
 *    7200   the sub-run enters the room: spawn line, spark at the tool
 *    12000  `run_end` of the sub-run: verdict, emote, the note falls, the handover is planned
 *    15500  arrival at the handover, in the `SETTLE_MS` window in which stepping still
 *           happens; at the same time the root run stands at the rack, which has shown `start` for 2 s (a rising bar)
 *    25200  through the door: `retired`, the seat free again; the rack keeps glowing because
 *           the deployment is still running, exactly the state without an expiry
 *    48000  after the 31.5 s gap: simulation time stands at 36.5 s, because `MAX_GAP_MS`
 *           clamps; plus the raised hand of the gate
 *    54000  end of the log: both root runs finished respectively waiting, the rack stands on
 *           `fail` (three red rows) and the "✗" above it has not yet faded
 *
 *  A point that does NOT tip over with a changed constant checks nothing; whoever deletes
 *  something here should know beforehand which line in the code then stays unchecked. */
export const GOLDEN_OFFSETS = [0, 1200, 7200, 12000, 15500, 25200, 48000, 54000];
