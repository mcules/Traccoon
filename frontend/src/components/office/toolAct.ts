// Layer 0: tool name to picture.
//
// `ToolAct = read | write | run | browse | delegate | other`: **six pictures instead of forty names**.
// The reason: making an agent walk to the window because a tool name happens to contain
// "web" is a lie the viewer cannot check. They see a figure browsing and have no way of
// finding out that in truth a file was read. Better no picture than a wrong one.
//
// ── The compromise, named openly ─────────────────────────────────────────────
//
// The clean principle would be "a table, no heuristic". That would only hold with a closed
// set of tools. Traccoon's set is open: every MCP server a user registers brings arbitrarily
// named tools with it (`obsidian__obsidian_get_note`, `homeassistant__call_service`, …). An
// exhaustive table for those cannot exist.
//
// The compromise preserves the intention:
//   1. `TOOL_ACT` is **exhaustive for every tool Traccoon owns itself**: the native ones from
//      `worker/runtime.py`, the memory tools from `worker/tools_memory.py` and all twenty
//      `traccoon_*` from `worker/tools_traccoon.py`. Nothing drifts for those, they are
//      enumerated.
//   2. `NATIVE_TOOLS` is a **separate, maintained list** of the same names. Deliberately not
//      derived from `Object.keys(TOOL_ACT)`: a forgotten tool would otherwise be missing in
//      both and the checker would notice nothing. It holds the backend target list against
//      `NATIVE_TOOLS` **and** `NATIVE_TOOLS` against the table; only that makes the build break on a gap.
//   3. The heuristic takes hold **only** on MCP names and **only** for `read`/`write`/`run`.
//      `browse` and `delegate` are not reachable from it: whether a foreign tool goes onto the
//      network or passes work on does not stand in its name. Exactly that was the lie above.

import type { MoodKind, ScreenKind, ToolAct } from "./types.ts";

// ── 1. The table: authoritative, never drifts ────────────────────────────────

/** Every Traccoon-owned tool with its picture. The order follows the source file in the
 *  worker, so that a new tool is added where one looks for it. */
export const TOOL_ACT: Record<string, ToolAct> = {
  // ── worker/runtime.py — native Werkzeuge ───────────────────────────────────
  // `submit_plan` writes no file but delivers a document; `write` is the most honest of the
  // six pictures.
  submit_plan: "write",
  // `ask_human`/`continue_later` are gestures towards the flow, not an activity. What the
  // viewer should see comes from the `gate` command (raising a hand), not from a tool pose.
  ask_human: "other",
  continue_later: "other",
  fs_read: "read",
  fs_list: "read",
  fs_write: "write",
  fs_edit: "write",
  check: "run",
  deploy: "run",
  // Renders the project page and looks at it: that is the one native case of "browse".
  screenshot: "browse",
  read_attachment: "read",
  open_tasks: "read",
  codegraph: "read",
  delegate: "delegate",
  load_skill: "read",

  // ── worker/tools_memory.py: the memory tools ───────────────────────────────
  remember_this: "write",
  forget: "write",
  memory_search: "read",

  // ── worker/tools_traccoon.py: the twenty control tools ─────────────────────
  traccoon_list_projects: "read",
  traccoon_list_issues: "read",
  traccoon_get_issue: "read",
  traccoon_create_issue: "write",
  traccoon_comment: "write",
  traccoon_assign_agent: "write",
  // Triggers a foreign run. **Not** `delegate`: the picture for that is the spawn line to a
  // figure in the same room, and that never comes into being here, because the planned run
  // belongs to another ticket and therefore to another session.
  traccoon_start_planning: "run",
  traccoon_approve_plan: "write",
  traccoon_issue_costs: "read",
  // Message to the outside, to a human: neither reading nor writing nor running.
  traccoon_notify_human: "other",
  traccoon_list_destinations: "read",
  traccoon_list_jobs: "read",
  traccoon_get_job: "read",
  traccoon_job_templates: "read",
  traccoon_create_job: "write",
  traccoon_update_job: "write",
  traccoon_run_job: "run",
  traccoon_list_workflows: "read",
  traccoon_start_workflow: "run",
  // The only way of an agent onto the network (destinations, `allow_agents`); here "browse" is proven.
  traccoon_http_call: "browse",
};

/** The target list the checker tests against: every name Traccoon itself offers as a tool.
 *  The sources are `worker/runtime.py` (`*_TOOL` constants and `_delegate_tool`),
 *  `worker/tools_memory.py::MEMORY_TOOLS` and `worker/tools_traccoon.py::TRACCOON_TOOLS`.
 *
 *  Kept twice and not derived from `TOOL_ACT`, see the head, point 2. */
export const NATIVE_TOOLS: readonly string[] = [
  // runtime.py (15)
  "submit_plan", "ask_human", "continue_later",
  "fs_read", "fs_list", "fs_write", "fs_edit",
  "check", "deploy", "screenshot", "read_attachment", "open_tasks", "codegraph",
  "delegate", "load_skill",
  // tools_memory.py (3)
  "erinnere_dich", "vergiss", "gedaechtnis_suchen",
  // tools_traccoon.py (20)
  "traccoon_list_projects", "traccoon_list_issues", "traccoon_get_issue",
  "traccoon_create_issue", "traccoon_comment", "traccoon_assign_agent",
  "traccoon_start_planning", "traccoon_approve_plan", "traccoon_issue_costs",
  "traccoon_notify_human", "traccoon_list_destinations",
  "traccoon_list_jobs", "traccoon_get_job", "traccoon_job_templates",
  "traccoon_create_job", "traccoon_update_job", "traccoon_run_job",
  "traccoon_list_workflows", "traccoon_start_workflow", "traccoon_http_call",
];

// ── 2./3. MCP ────────────────────────────────────────────────────────────────

/** Separator between server name and tool name with MCP tools.
 *  Set in `worker/mcp_client.py::MultiMcpSession.list_tools` (`f"{name}__{t.name}"`);
 *  MCPJungle delivers its gateway tools in the same shape. */
const MCP_SEP = "__";

/** The one heuristic. It sees exclusively MCP names and knows only three of the six pictures.
 *
 *  It is applied to the **word marks** of the bare name, not to the beginning of the name: a
 *  strict `startsWith` fired almost never, because MCP servers repeat their own name as the
 *  first mark (`obsidian_get_note`, `calendar_list_events`). The first mark that stands in
 *  this table wins; if none is found it stays `other`, and `other` is not a stopgap but the
 *  right answer to "I do not know". */
const MCP_VERB: Record<string, ToolAct> = {
  get: "read", list: "read", read: "read", search: "read", find: "read", query: "read",
  fetch: "read", describe: "read", show: "read", view: "read", download: "read",
  resolve: "read", overview: "read", history: "read", summary: "read",
  create: "write", add: "write", update: "write", set: "write", write: "write",
  save: "write", delete: "write", remove: "write", patch: "write", post: "write",
  put: "write", upload: "write", rename: "write", move: "write", mark: "write",
  bulk: "write", manage: "write", submit: "write", send: "write", edit: "write",
  append: "write", replace: "write", insert: "write",
  run: "run", start: "run", stop: "run", exec: "run", execute: "run", trigger: "run",
  call: "run", restart: "run", reload: "run", build: "run", deploy: "run", sync: "run",
  render: "run", fire: "run", install: "run",
};

/** `hasOwnProperty` instead of `in`: a tool called `constructor` or `toString` would
 *  otherwise run over the prototype chain and get a picture that stands nowhere in the table. */
function tableHas<T>(table: Record<string, T>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(table, key);
}

/** Tool name to picture. Resolution order:
 *
 *  1. **exact table**, authoritative, never drifts;
 *  2. shorten MCP names `server__tool` to the bare name and ask the table again
 *     (a project MCP may well be called `traccoon__fs_read`);
 *  3. exactly one prefix heuristic, and that applies **only** to MCP.
 *
 *  An unknown name **without** `__` is not an MCP tool, so it is either a typo or a new
 *  native tool that belongs in the table; that one gets `other` and the checker reports the
 *  gap. Guessing it heuristically would mean hiding the gap. */
export function toolAct(name: string): ToolAct {
  const tool = (name || "").trim();
  if (!tool) return "other";
  if (tableHas(TOOL_ACT, tool)) return TOOL_ACT[tool];

  const cut = tool.lastIndexOf(MCP_SEP);
  if (cut < 0) return "other";           // no MCP → no heuristic
  const bare = tool.slice(cut + MCP_SEP.length);
  if (tableHas(TOOL_ACT, bare)) return TOOL_ACT[bare];

  for (const mark of bare.toLowerCase().split("_")) {
    if (mark && tableHas(MCP_VERB, mark)) return MCP_VERB[mark];
  }
  return "other";
}

// ── Monitor and mood ─────────────────────────────────────────────────────────

/** Picture per activity. The base table: it has an entry for each of the six `ToolAct` so
 *  that no case ever falls through. */
const SCREEN_BY_ACT: Record<ToolAct, ScreenKind> = {
  read: "code",
  write: "code",
  run: "log",
  browse: "page",
  delegate: "link",
  other: "blank",
};

/** The few tools where the picture of the activity would be visibly wrong.
 *  Keep it short: every line here is an exception to "six pictures instead of forty names"
 *  and has to be worth it. */
const SCREEN_BY_TOOL: Record<string, ScreenKind> = {
  // Searching looks different from reading: a hit list instead of source code.
  codegraph: "search",
  memory_search: "search",
  fs_list: "search",
  // Looking at something rendered, not at source code.
  screenshot: "page",
  read_attachment: "page",
};

/** What stands on the monitor.
 *
 *  `waiting` wins before everything else: a figure waiting for a human is exactly what the
 *  viewer should see, even when a tool is still open in the background (the permission
 *  dialog is what holds the call up right now). */
export function screenFor(act: ToolAct | undefined, tool: string | undefined,
                          waiting: boolean): ScreenKind {
  if (waiting) return "wait";
  const t = (tool || "").trim();
  if (t && tableHas(SCREEN_BY_TOOL, t)) return SCREEN_BY_TOOL[t];
  if (!act) return "blank";
  return SCREEN_BY_ACT[act];
}

/** Mood of the display.
 *
 *  The order is a statement: **done** beats everything (a finished run is finished, even when
 *  something went wrong on the way), then **waiting** (the only state in which the human has
 *  to do something, and it must not disappear behind an old error), then **error**,
 *  otherwise **work**.
 *
 *  `fails`/`waiting` are counters, not switches: the same function labels the tile of a
 *  single agent (0/1) and the summary of a whole room (n). */
export function moodFor(done: boolean, fails: number, waiting: number): MoodKind {
  if (done) return "done";
  if (waiting > 0) return "wait";
  if (fails > 0) return "error";
  return "work";
}
