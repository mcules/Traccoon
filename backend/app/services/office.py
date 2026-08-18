"""Office: the seam between database and view.

Everything the office ever sees goes through `step_events`. The history API reads
`run_steps` and calls it; the worker writes a row and calls it through `publish_step`.
That makes the *identical shape* of both streams built rather than merely agreed: there is
no second place where an event could come into being, and therefore none where the live
view and the replay could drift apart.

**Order** is `seq`, never `ts`. `seq = run_steps.id * 4 + slot`: the row id is SERIAL,
globally monotonic and exactly the arrival order, and a counter of our own would be a
second truth. Four slots per row so that one row can carry more than one event:

    0  synthesised predecessor (old rows only: the `tool_start` for a `tool_result`)
    1  Hauptereignis
    2  abgeleiteter Begleiter (`usage`, `file_edit`, `agent_spawn`)
    3  reserved, and room for the `run_end` boundary behind the last row

`2e9 * 4 < 2^53`, so the number stays exact in JavaScript.

**Slot 3 has two candidates, and the precedence rule lives here:** the synthesised
`run_end` boundary wins, and a **legacy** deployment moves to slot 3 of the preceding
step row (`deploy_anchor_step_id`). This concerns the old path only: a new deployment
writes a real `run_steps` row through the watcher (`services/deploy_watch.py`) and gets
slot 1 like everything else. Inserting a step afterwards was no option: new SERIAL ids sit
above *every* existing step, so a July deploy would sort to the end of every session.
`seq` **is** the arrival order, and a backfill would lie about it.


**`thinking` is reserved and never emitted.** No provider adapter delivers thinking blocks;
invented thinking would poison the colours of the timeline and claim time nobody measured.
The kind is in the contract so it can be added later without a version jump, not so that
somebody fills it today.

**Replaying old data.** Every row written before the instrumentation has `kind=''` and is
rebuilt from `role` + `content` while reading. Without that the office starts empty.

**Never guess.** `ok` has three values, and where success is not proven it is `None`. A
guessed `True` would be a lie that paints the view green.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import PREFIX
from ..models.agents import RunStep

log = logging.getLogger("office")

# Version of the envelope. Raise it when a field changes its meaning: the frontend then
# refuses to draw rather than paint a wrong reading. *Adding* a kind is additive and stays
# at 1: `deploy` reinterprets no existing field, and a jump to 2 would make every running
# interface refuse to draw, for a branch it can simply ignore.

EVENT_VERSION = 1
# ONE global channel; fan out and authorisation are the job of the websocket bridge. A
# channel per project could not carry projectless runs and would mean 40 subscriptions per user.
CHANNEL = f"{PREFIX}office"
# How recent an event counts as "just now" (joining the live stream after the snapshot).
LIVE_WINDOW_MS = 90_000
EVENT_CAP_DEFAULT = 4_000
EVENT_CAP_MAX = 20_000

# Slots je `run_steps`-Zeile (siehe Modul-Docstring).
SEQ_SLOTS = 4
SLOT_BEFORE = 0
SLOT_MAIN = 1
SLOT_DERIVED = 2
SLOT_TAIL = 3   # `run_end` boundary; borrowed on the legacy path for an existing deployment

# The full contract. `thinking` is deliberately listed and never produced.
KINDS = (
    "session_seen", "run_start", "user_message", "agent_text", "thinking", "usage",
    "tool_start", "tool_result", "file_edit", "agent_spawn", "run_end", "system",
    "deploy",
)

# The four states the server rack can show. `back` (rolledback) is deliberately not merged
# with `fail`: failed AND healed is the only good news in a failure, and merging would lose
# exactly that.
DEPLOY_STATES = ("start", "ok", "fail", "back")
DEPLOY_STATE_BY_STATUS = {
    "building": "start", "ok": "ok", "failed": "fail", "rolledback": "back",
}
# The same width as the list in `api/deployments.py`: both show the same excerpt of the same
# log, and two widths would be two truths about "what stood at the beginning".
DEPLOY_LOG_HEAD_CHARS = 240

# Truncation limits: the room shows previews, not full texts.
ARGS_PREVIEW_CHARS = 400
RESULT_PREVIEW_CHARS = 2000

# What the runtime really returns as an error (worker/runtime.py, mcp_client.py,
# tools_memory.py, codegraph.py). Everything else is unknown, not successful.
ERROR_PREFIXES = ("FEHLER:", "FEHLER ", "TOOL-FEHLER:", "FS-FEHLER:", "CHECK-FEHLER:", "❌", "⛔")

# Which argument gives a tool its label. An explicit table instead of a heuristic, in the
# same key order as `worker/perms.resource_of`, so the room labels the same thing the
# permission dialog showed. Tool server tools (`server__tool`) are missing on purpose:
# their argument names are unbounded, and any rule for them would be a guess.

TOOL_TARGET_KEYS: dict[str, tuple[str, ...]] = {
    "fs_read": ("path",),
    "fs_write": ("path",),
    "fs_edit": ("path",),
    "fs_list": ("path",),
    "read_attachment": ("name",),
    "load_skill": ("key",),
    "screenshot": ("target",),
    "codegraph": ("query",),
    "delegate": ("role",),
    "traccoon_http_call": ("path", "url"),
}

# Tools that leave a file behind: only they get the `file_edit` companion.
EDIT_TOOLS = ("fs_write", "fs_edit")

# Verdict of a run. `blocked`/`running` are neither one nor the other, hence None.
OK_STATUS = ("success", "planned")
FAIL_STATUS = ("failed", "loop_exhausted")

# Separator between arguments and result in the old tool rows
# (`worker/runtime.py`: f"args={…}\n→ {…}").
LEGACY_SPLIT = "\n→ "


# ── Kontext eines Laufs ──────────────────────────────────────────────────────

@dataclass
class RunCtx:
    """Everything an event needs to know about its run, without touching the database.

    The worker keeps one instance for the whole run and counts `seq` up; the read API builds
    it once per run from the `runs` row. No field here is looked up: the websocket bridge
    authorises per event, and a query per event would not be affordable.
    """

    run_id: int
    project_id: int | None = None
    owner_id: int | None = None
    sid: str = ""
    agent: str = ""
    phase: str = ""
    provider: str = ""
    model: str = ""
    parent_run_id: int | None = None
    parent_tool_use_id: str | None = None
    spawn_depth: int = 0
    issue_key: str = ""
    continuation_index: int = 0
    task_id: str = ""
    seq: int = 0   # the running RunStep.seq (the worker counts up here)

    @classmethod
    def from_run(cls, run, *, issue_key: str = "") -> RunCtx:
        return cls(
            run_id=run.id,
            project_id=getattr(run, "project_id", None),
            owner_id=getattr(run, "owner_id", None),
            sid=session_id(run),
            agent=run.agent or "",
            phase=run.phase or "",
            provider=run.provider or "",
            model=run.model or "",
            parent_run_id=run.parent_run_id,
            parent_tool_use_id=getattr(run, "parent_tool_use_id", None),
            spawn_depth=int(getattr(run, "spawn_depth", 0) or 0),
            issue_key=issue_key,
            continuation_index=int(run.continuation_index or 0),
            task_id=run.task_id or "",
        )


def session_id(run) -> str:
    """Address of the room a run belongs to.

    A ticket is a room: the planning run, the execution, continuations and every subagent
    belong together. A single `Run` would be too small (a subagent would get an office of its
    own), a project too large. Runs without a ticket (job, assistant) address through their
    root, and the row knows only one hop upwards, so more deeply nested subruns end up at the
    intermediate run. That is enough for the view, because without a ticket there is only one
    chain anyway.
    """
    if run.issue_id:
        return f"issue:{run.issue_id}"
    return f"run:{run.parent_run_id or run.id}"


def kind_from_role(role: str) -> str:
    """Event kind of an old row, taken from its `role`."""
    r = (role or "").strip().lower()
    if r == "assistant":
        return "agent_text"
    if r == "tool":
        return "tool_result"
    return "system"


def tool_target(tool: str, args: dict | None) -> str | None:
    """What the tool is labelled with, or None when nothing certain is there."""
    for key in TOOL_TARGET_KEYS.get(tool or "", ()):
        value = (args or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_ok(result: str) -> bool | None:
    """False on a proven failure, otherwise None. Never a guessed True.

    The runtime does not report success, it simply returns the result. "No error prefix"
    therefore only means "not recognised as an error", and that is exactly `None`. Only the
    instrumentation knows whether a call went through, and writes `True`.
    """
    text = (result or "").lstrip()
    if not text:
        return None
    return False if text.startswith(ERROR_PREFIXES) else None


# ── Umschlag ────────────────────────────────────────────────────────────────

def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Read naive timestamps as UTC (SQLite delivers them without a zone). Without this a
    comparison of two rows throws a TypeError or is off by hours, depending on the database."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _ts(value: dt.datetime | None) -> str:
    """ISO-8601 in UTC with milliseconds. Naive timestamps count as UTC (SQLite delivers
    them without a zone); otherwise the same row would shift by hours depending on the database."""
    if value is None:
        value = dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


# Public name for `_ts`. Whoever writes a time **into** a finished event (the window clamp of
# `GET /office/events`) has to produce exactly the same text as whoever built the event: two
# spellings of the same moment would be two timelines.
ts_text = _ts


def _event(ctx: RunCtx, *, seq: int, ts: str, kind: str, **fields: Any) -> dict:
    """The shared envelope. `project_id`/`owner_id` hang on EVERY event so the websocket
    bridge can decide who may see it without touching the database."""
    return {
        "v": EVENT_VERSION, "seq": seq, "ts": ts, "sid": ctx.sid,
        "project_id": ctx.project_id, "owner_id": ctx.owner_id,
        "run_id": ctx.run_id, "agent_id": f"run:{ctx.run_id}",
        "kind": kind, **fields,
    }


def _seq(step_id: int, slot: int) -> int:
    return int(step_id) * SEQ_SLOTS + slot


def _parse_args(raw: str) -> dict:
    """Read arguments out of a preview as far as they are readable. No error when they are not,
    the preview is truncated and may break off in the middle of the JSON."""
    text = (raw or "").strip()
    # "args=" is the formatting of the old tool row, not content.
    if text.startswith("args="):
        text = text[len("args="):].strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _args_of(step) -> dict:
    return _parse_args(step.content)


# ── The seam ────────────────────────────────────────────────────────────────

def step_events(step, ctx: RunCtx) -> list[dict]:
    """One `run_steps` row turned into the events it causes in the room.

    Pure: no database, no clock, no randomness. Only that way can the view rewind
    deterministically, and only that way do history and live stream deliver the same thing.
    """
    kind = (getattr(step, "kind", "") or "").strip()
    if not kind:
        return _legacy_events(step, ctx)

    ts = _ts(step.created_at)
    main = _seq(step.id, SLOT_MAIN)
    derived = _seq(step.id, SLOT_DERIVED)
    tool = step.tool_name or ""
    out: list[dict] = []

    if kind == "run_start":
        out.append(_event(ctx, seq=main, ts=ts, kind="run_start",
                          agent=ctx.agent, phase=ctx.phase,
                          provider=ctx.provider, model=ctx.model,
                          parent_run_id=ctx.parent_run_id,
                          parent_tool_use_id=ctx.parent_tool_use_id,
                          spawn_depth=ctx.spawn_depth,
                          continuation_index=ctx.continuation_index,
                          task_id=ctx.task_id, issue_key=ctx.issue_key))

    elif kind == "user_message":
        # The source sits in `target`: a column of its own would be a column for a single
        # word in one of ten event kinds.
        out.append(_event(ctx, seq=main, ts=ts, kind="user_message",
                          source=(step.target or "system"), text=step.content or ""))

    elif kind == "agent_text":
        text = (step.content or "").strip()
        # "(Tool-Call)" is the runtime's placeholder for a turn without text. Unfiltered,
        # every agent would say "(Tool-Call)" into the room every few seconds.
        if text and text != "(Tool-Call)":
            out.append(_event(ctx, seq=main, ts=ts, kind="agent_text", text=step.content))
        out.extend(_usage_event(step, ctx, seq=derived, ts=ts))

    elif kind == "usage":
        out.extend(_usage_event(step, ctx, seq=main, ts=ts))

    elif kind == "tool_start":
        args = _args_of(step)
        out.append(_event(ctx, seq=main, ts=ts, kind="tool_start",
                          tool=tool, target=(step.target or tool_target(tool, args)),
                          tool_use_id=step.tool_use_id,
                          args_preview=(step.content or "")[:ARGS_PREVIEW_CHARS]))
        # The spawn hangs on the tool start, not on the result: `delegate` awaits the subrun
        # inline, so the result row appears only at its END, and the subagent would be sitting
        # at the desk retroactively.
        if tool == "delegate":
            out.append(_event(ctx, seq=derived, ts=ts, kind="agent_spawn",
                              child_role=(step.target or args.get("role") or ""),
                              prompt=str(args.get("task") or ""),
                              tool_use_id=step.tool_use_id, background=False))

    elif kind == "tool_result":
        out.append(_event(ctx, seq=main, ts=ts, kind="tool_result",
                          tool=tool, tool_use_id=step.tool_use_id, ok=step.ok,
                          error=((step.content or "")[:RESULT_PREVIEW_CHARS]
                                 if step.ok is False else ""),
                          duration_ms=step.duration_ms,
                          result_preview=(step.content or "")[:RESULT_PREVIEW_CHARS]))
        # Only on proven success: a failed fs_write wrote nothing, and a `None` does not know.

        if tool in EDIT_TOOLS and step.ok is True and step.target:
            out.append(_event(ctx, seq=derived, ts=ts, kind="file_edit", path=step.target))

    elif kind == "file_edit":
        out.append(_event(ctx, seq=main, ts=ts, kind="file_edit", path=step.target or ""))

    elif kind == "agent_spawn":
        args = _args_of(step)
        out.append(_event(ctx, seq=main, ts=ts, kind="agent_spawn",
                          child_role=(step.target or args.get("role") or ""),
                          prompt=str(args.get("task") or step.content or ""),
                          tool_use_id=step.tool_use_id, background=False))

    elif kind == "deploy":
        # The content is JSON, the same shape as `run_end` and for the same reason: the row
        # carries fields that have no column, and a column of their own in `run_steps` (one of
        # twelve kinds) could not be justified.
        src = _args_of(step)
        out.append(_event(ctx, seq=main, ts=ts, kind="deploy",
                          **deploy_fields(deployment_id=src.get("deployment_id"),
                                          state=src.get("state"),
                                          target=step.target or "",
                                          log_head=src.get("log_head"))))

    elif kind == "run_end":
        # The content of a run_end row is the closing report as JSON, the same fields that
        # `run_boundary_events` pulls from the `runs` row.
        out.append(_event(ctx, seq=main, ts=ts, kind="run_end", **_run_end_fields(_args_of(step))))

    else:
        # `system` and everything unknown: show as a system line instead of swallowing it.
        out.append(_event(ctx, seq=main, ts=ts, kind="system", text=step.content or ""))

    return out


def _usage_event(step, ctx: RunCtx, *, seq: int, ts: str) -> list[dict]:
    """Tokens of one model turn. Comes without text as well: a turn that only called tools
    still cost something. Without tokens no event at all (the timeline would otherwise be full
    of zeros). `cache_write_tokens` is always 0, because no adapter reports the write share."""
    in_tok = int(getattr(step, "in_tokens", 0) or 0)
    out_tok = int(getattr(step, "out_tokens", 0) or 0)
    cache_read = int(getattr(step, "cache_read_tokens", 0) or 0)
    if not (in_tok or out_tok or cache_read):
        return []
    return [_event(ctx, seq=seq, ts=ts, kind="usage",
                   in_tokens=in_tok, out_tokens=out_tok, cache_read_tokens=cache_read,
                   cache_write_tokens=0,
                   provider=(step.provider or ctx.provider), model=(step.model or ctx.model))]


def _legacy_events(step, ctx: RunCtx) -> list[dict]:
    """Rows from the time before the instrumentation (`kind=''`).

    Without this path the office starts empty: the existing runs are the only reason there is
    anything to see on the first day at all. What is missing here is missing honestly: no
    duration, no tokens per turn, `ok` only from the error prefix.
    """
    ts = _ts(step.created_at)
    role = (step.role or "").strip().lower()

    if role == "assistant":
        text = (step.content or "").strip()
        if not text or text == "(Tool-Call)":
            return []
        return [_event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="agent_text",
                       text=step.content)]

    if role == "tool":
        # An old row is start AND result in one field: "args={…}\n→ …". The room needs both
        # halves separately, otherwise a tool never opens. Both carry the same timestamp: the
        # duration was not measured back then, and estimating it would mean inventing time.

        raw = step.content or ""
        args_text, _, result = raw.partition(LEGACY_SPLIT)
        args = _parse_args(args_text)
        # The "args=" prefix is formatting of the old row, not content. The new path has bare
        # JSON there, and both should produce the same preview.
        if args_text.startswith("args="):
            args_text = args_text[len("args="):]
        tool = step.tool_name or ""
        use_id = f"legacy:{step.id}"
        return [
            _event(ctx, seq=_seq(step.id, SLOT_BEFORE), ts=ts, kind="tool_start",
                   tool=tool, target=tool_target(tool, args), tool_use_id=use_id,
                   args_preview=args_text[:ARGS_PREVIEW_CHARS]),
            _event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="tool_result",
                   tool=tool, tool_use_id=use_id, ok=tool_ok(result),
                   error=(result[:RESULT_PREVIEW_CHARS] if tool_ok(result) is False else ""),
                   duration_ms=None, result_preview=result[:RESULT_PREVIEW_CHARS]),
        ]

    return [_event(ctx, seq=_seq(step.id, SLOT_MAIN), ts=ts, kind="system",
                   text=step.content or "")]


def _run_end_fields(src: dict) -> dict:
    """The fields of a `run_end`, from one mapping so that the `runs` row and the JSON content
    of a run_end row go through the SAME place and cannot drift apart."""
    status = str(src.get("status") or "")
    ok: bool | None = True if status in OK_STATUS else False if status in FAIL_STATUS else None
    return {
        "ok": ok, "status": status, "blocker_kind": src.get("blocker_kind"),
        "summary": src.get("summary") or "", "error": src.get("error") or "",
        "iterations": int(src.get("iterations") or 0),
        "in_tokens": int(src.get("in_tokens") or 0),
        "out_tokens": int(src.get("out_tokens") or 0),
        "cache_read_tokens": int(src.get("cache_read_tokens") or 0),
        "cost_usd": float(src.get("cost_usd") or 0.0),
        "cost_priced": src.get("cost_priced"),
    }


def entdoppeln_seq(events: list[dict]) -> int:
    """Resolve duplicate `seq` in an **already sorted** list, the trap of every log that spans
    sessions. Returns how many were moved.

    The boundaries added afterwards sit between the rows: `run_end` at `last*4 + 3`,
    `run_start` at `first*4 - 1`. That is the same number as soon as the next run starts with
    the immediately following row id, and because runs happen one after another this is the
    normal case, not the outlier (13 collisions measured on a real day with 21 runs). Inside
    ONE session it hardly shows, across sessions it hits nearly every transition.


    And it would not be visible but silent: the recorder deduplicates by `seq`
    (`office/recorder.ts`) and would drop the second event, so an agent would never enter or
    never leave. That is why the latecomer moves to the next free number. That is `first*4 + 0`
    and therefore the reserved slot 0 of its own first row: still before its main event, so
    the story stays intact.

    Only upwards and only on a tie: the order of the rows already placed stays untouched.
    sortierten Liste bleibt dadurch unangetastet.

    This lives here and not in the caller, because `seq` belongs to this module: the film
    (`office_film`) and the room (`api/office.GET /office/events`) mix the same sessions and
    must not resolve the collision twice, possibly differently.
    """
    vorher = -1
    verschoben = 0
    for ev in events:
        if ev["seq"] <= vorher:
            ev["seq"] = vorher + 1
            verschoben += 1
        vorher = ev["seq"]
    return verschoben


def run_boundary_events(run, ctx: RunCtx, *, first_step_id: int | None,
                        last_step_id: int | None, cost_priced: bool | None = None) -> list[dict]:
    """`run_start`/`run_end` for runs that have no boundary rows of their own (old runs).

    The boundaries sit between the steps instead of next to them: `run_start` at
    `first_step_id*4 - 1` (slot 0 of the first row stays free for its own predecessor),
    `run_end` at `last_step_id*4 + 3`. That keeps the order one single ascending series of
    numbers, without a special case while sorting.

    Without steps there are no anchors and therefore no boundaries: a run without a single
    row has nothing to show in the room.
    """
    out: list[dict] = []
    if first_step_id:
        out.append(_event(ctx, seq=_seq(first_step_id, 0) - 1, ts=_ts(run.started_at),
                          kind="run_start",
                          agent=ctx.agent, phase=ctx.phase,
                          provider=ctx.provider, model=ctx.model,
                          parent_run_id=ctx.parent_run_id,
                          parent_tool_use_id=ctx.parent_tool_use_id,
                          spawn_depth=ctx.spawn_depth,
                          continuation_index=ctx.continuation_index,
                          task_id=ctx.task_id, issue_key=ctx.issue_key))
    # A running run has not left the room yet.
    if last_step_id and run.status and run.status != "running":
        out.append(_event(ctx, seq=_seq(last_step_id, SEQ_SLOTS - 1),
                          ts=_ts(run.finished_at or run.started_at), kind="run_end",
                          **_run_end_fields({
                              "status": run.status, "blocker_kind": getattr(run, "blocker_kind", None),
                              "summary": run.summary or run.last_text or "", "error": run.error or "",
                              "iterations": run.iterations, "in_tokens": run.input_tokens,
                              "out_tokens": run.output_tokens,
                              # The run does not track cached tokens separately, they only
                              # sit on the CostEntry. An honest 0 here instead of a guess.
                              "cache_read_tokens": 0, "cost_usd": run.cost_usd,
                              "cost_priced": cost_priced,
                          })))
    return out


# ── Deployments ─────────────────────────────────────────────────────────────

def deploy_state(status: str) -> str:
    """`building|ok|failed|rolledback` turned into the state the room shows. Otherwise `""`.

    What falls out empty here has no business in the room: `pending`/`pending-check` is a
    queue, not an event, and `cancelled` is written by no code path (see
    `models/ops.Deployment`). Animating a hand written cleanup would claim an action that
    never took place.
    """
    return DEPLOY_STATE_BY_STATUS.get((status or "").strip(), "")


def deploy_target(dep) -> str:
    """What was worked on: the stack, or the worktree instead. A label for the rack, no more;
    the path is the only thing the row above knows for certain."""
    return (getattr(dep, "stack_dir", "") or getattr(dep, "worktree", "") or "")[:500]


def deploy_fields(*, deployment_id: Any, state: Any, target: str, log_head: Any) -> dict:
    """The fields of a `deploy`, the ONE place where they come into being.

    Both ways go through here: the real row of the watcher (live and follow-up) and the
    existing deployment synthesised while reading. Two places could drift apart, and the view
    would show something different depending on the age of the deployment.
    """
    return {
        "deployment_id": int(deployment_id or 0),
        "state": str(state or ""),
        "target": target or "",
        "log_head": str(log_head or "")[:DEPLOY_LOG_HEAD_CHARS],
    }


def deploy_content(deployment_id: int, state: str, log_head: str = "") -> str:
    """The JSON body of a `deploy` step row (the write side of `deploy_fields`)."""
    return json.dumps({"deployment_id": int(deployment_id), "state": state,
                       "log_head": (log_head or "")[:DEPLOY_LOG_HEAD_CHARS]},
                      ensure_ascii=False)


def deploy_step_id(step) -> int:
    """Which deployment a `deploy` row belongs to (0 when it is none).

    The read path needs that in order NOT to synthesise an existing deployment a second time
    when the watcher has long told it as a real row.
    """
    if (getattr(step, "kind", "") or "") != "deploy":
        return 0
    return int(_args_of(step).get("deployment_id") or 0)


def deploy_anchor_step_id(steps, created_at: dt.datetime | None, *,
                          blocked: set[int] | frozenset[int] = frozenset()) -> int | None:
    """Which step row a **legacy** deployment hangs its borrowed `seq` on.

    The anchor is the last row before `created_at`: that is where the session stood when the
    deploy started, and that is where it belongs in the narration. If its slot 3 is already
    taken (the synthesised `run_end` boundary takes precedence, and two deployments do not
    share a slot), it slips onto the preceding row. If none is found there is no event:
    better a gap than a deploy standing before its own trigger.
    """
    cutoff = _as_utc(created_at)
    frei = [s.id for s in steps
            if cutoff is None or (_as_utc(s.created_at) or cutoff) <= cutoff]
    for step_id in reversed(frei):
        if step_id not in blocked:
            return step_id
    return None


def deployment_events(dep, ctx: RunCtx, *, anchor_step_id: int | None) -> list[dict]:
    """A deployment from the time before the watcher, turned into its event with a borrowed `seq`.

    The same pattern as `run_boundary_events`: nothing is written, and the order comes into
    being while reading, from a foreign row id. Unlike there, there is only ONE event (the
    outcome): no second slot would be free for the opening, and an invented starting time
    between foreign steps would be guesswork.
    """
    state = deploy_state(getattr(dep, "status", "") or "")
    if not state or not anchor_step_id:
        return []
    ts = _ts(getattr(dep, "finished_at", None) or getattr(dep, "started_at", None)
             or getattr(dep, "created_at", None))
    return [_event(ctx, seq=_seq(anchor_step_id, SLOT_TAIL), ts=ts, kind="deploy",
                   **deploy_fields(deployment_id=getattr(dep, "id", 0), state=state,
                                   target=deploy_target(dep),
                                   log_head=getattr(dep, "log", "") or ""))]


def session_seen_event(ctx: RunCtx, *, title: str, project_key: str,
                       started_at: dt.datetime | None, seq: int) -> dict:
    """Header of a room: tells the view that the session exists before the first
    agent comes through the door."""
    return _event(ctx, seq=seq, ts=_ts(started_at), kind="session_seen",
                  title=title, issue_key=ctx.issue_key, project_key=project_key,
                  started_at=_ts(started_at))


# ── Writing and sending ─────────────────────────────────────────────────────

async def add_step(db: AsyncSession, ctx: RunCtx, *, role: str, kind: str, content: str = "",
                   tool: str | None = None, target: str | None = None,
                   tool_use_id: str | None = None, ok: bool | None = None,
                   duration_ms: int | None = None, in_tokens: int = 0, out_tokens: int = 0,
                   cache_read_tokens: int = 0, provider: str = "", model: str = "",
                   commit: bool = True) -> RunStep:
    """Write a step row, the ONE way there.

    The worker (`_add_step`) and `open_room` use the same function, so there can be no row
    that does not carry the event fields. `ctx.seq` is the running counter of the run
    (`RunStep.seq`), not the event number, which comes from `id`. `commit=False` allows
    several rows to be set in ONE transaction.
    """
    ctx.seq += 1
    step = RunStep(
        run_id=ctx.run_id, seq=ctx.seq, role=role, kind=kind, tool_name=tool,
        content=(content or "")[:8000], target=(target or None), tool_use_id=tool_use_id,
        ok=ok, duration_ms=duration_ms, in_tokens=in_tokens, out_tokens=out_tokens,
        cache_read_tokens=cache_read_tokens, provider=provider or None, model=model or None,
    )
    db.add(step)
    if commit:
        await db.commit()
    return step


async def publish_step(ctx: RunCtx, step) -> None:
    """Put the events of a row into the live channel.

    Swallows EVERY error: a Redis outage must not kill an agent run, because the view is a
    spectator, not a participant. Redis is imported inside the body so the test double
    (`conftest.redis_stub`) takes effect; an import at module level would have nailed down
    the real function.
    """
    try:
        from ..core.redis import get_redis
        client = get_redis()
        for event in step_events(step, ctx):
            await client.publish(CHANNEL, json.dumps(event, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — bewusst alles
        log.debug("Büro: Ereignis von Lauf %s nicht gesendet", ctx.run_id, exc_info=True)


async def open_room(db: AsyncSession, ctx: RunCtx, *, agent, mode: str, issue: dict) -> None:
    """Open the room: the agent walks in, and it says WHY.

    Both rows go out in ONE transaction: an agent sitting at a desk without an assignment
    would be a state that must not exist, not even for 20 ms.
    """
    # The name comes from the agent definition when the context does not know it yet: the
    # room labels the desk with it.
    if not ctx.agent:
        ctx.agent = getattr(agent, "name", "") or getattr(agent, "role", "") or ""
    start = await add_step(db, ctx, role="system", kind="run_start", content=mode,
                           provider=ctx.provider, model=ctx.model, commit=False)
    auftrag = "\n\n".join(p for p in (
        str(issue.get("summary") or ""), str(issue.get("description") or "")[:2000]) if p)
    auftrag_step = await add_step(db, ctx, role="user", kind="user_message", target="ticket",
                                  content=auftrag, commit=False)
    await db.commit()
    # Send only after the commit: before it the row has no `id` and therefore no `seq`.
    await publish_step(ctx, start)
    await publish_step(ctx, auftrag_step)


# ── Preise ──────────────────────────────────────────────────────────────────

class PriceTable:
    """Load the model catalog once and price in Python (prices are USD per 1M tokens).

    The distinction that was missing before: a catalog entry of 0.00 means *priced and free*
    (a local model), no entry at all means *unknown*. Both used to produce the same 0.00 in
    the display, and every gap in the catalog looked like a gift.
    """

    def __init__(self, rows) -> None:
        self._by_key = {(r.provider or "", r.model or ""): r for r in rows}

    @classmethod
    async def load(cls, db: AsyncSession) -> PriceTable:
        from ..models.ops import ProviderModel
        rows = (await db.execute(select(ProviderModel))).scalars().all()
        return cls(rows)

    def has(self, provider: str, model: str) -> bool:
        return (provider or "", model or "") in self._by_key

    def price(self, provider: str, model: str, *, in_tokens: int = 0, out_tokens: int = 0,
              cache_read_tokens: int = 0) -> tuple[float, bool]:
        """(cost in USD, priced?). Without a catalog entry `(0.0, False)`: the zero is a gap
        then, and the caller has to mark it as one."""
        row = self._by_key.get((provider or "", model or ""))
        if row is None:
            return 0.0, False
        return ((in_tokens or 0) / 1e6 * (row.price_input or 0.0)
                + (out_tokens or 0) / 1e6 * (row.price_output or 0.0)
                + (cache_read_tokens or 0) / 1e6 * (row.price_cache_read or 0.0)), True
