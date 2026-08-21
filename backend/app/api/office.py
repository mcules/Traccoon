"""Office: the read API. Session lists, event snapshot, cost.

A **session** is a run tree, not a run and not a project. A single `Run` would be too
small: planning, execution, every continuation and the review agent are one connected
story, and a delegated subagent would sit in an empty office of its own. A project would
be too large to rewind; the project **is** the list of its sessions.
Sessions. Zwei Adressformen:

    issue:{issue_id}   every run with this issue id, the room of one ticket
    run:{root_run_id}  the tree under a run without a ticket (job, assistant)

The path is therefore `/sessions/{kind}/{ref}` and not `/sessions/{sid}`: a literal `:` in
a path segment is allowed but encoded differently by proxies, clients and test tools. Two
segments are cheaper than that discussion.

**Closing the tree costs two queries, not a recursive CTE.** The depth is bounded, so two
`worker/runtime.MAX_DELEGATION_DEPTH` gedeckelt, also reichen zwei begrenzte
`WHERE parent_run_id IN (…)` do it, and they behave identically under SQLite (tests) and
Postgres, while a `WITH RECURSIVE` would pull the two dialects apart.
For `issue:` even that falls away: a delegated subrun inherits the `issue_id` of its parent
(`worker/runtime.py`, `run_agent(issue=…)`), so the index `ix_runs_issue_started` covers the
whole tree in ONE query.

**Old data is not a special case, it is the normal case on day one.** Runs from before the
instrumentation have `kind=''` rows and no `run_start`/`run_end` rows. The read path
therefore always goes through `services.office.step_events` (which knows the old path) and
adds missing boundaries through `run_boundary_events`. The same route profits from later
work without a line of change. **Deployments from before the watcher** join the same way
(`deployment_events`, borrowed `seq`): one extra query for the whole session, not one per
run, and only when the session has tickets.

**And a room that shows ALL sessions.** `GET /office/events` mixes the window of the last
hours across sessions into ONE log. That only works because `seq` comes from a SERIAL
column and is therefore monotonic across runs **and** projects; the traps (clamping the
added boundaries to the window, `seq` collisions at every run transition) are the same ones
`services/office_film.py` already solved for the daily film, which is why deduplication
moved to `services/office.entdoppeln_seq` and is read by both. Permissions come unchanged
from `_visible_runs`; `tages_ereignisse` itself is **no** template for the permission path,
that function knows no ACL at all.

**Unauthorised is 404, never 403.** `build_access` does it that way across the repository
(`deps.py:165-166`); a new surface answering 403 would betray the existence of other
people's tickets. For events and cost the permission is derived from the **session** (the
project or owner of the root run), not from the path: the path carries no project id, and
letting it carry one would hand authorisation to the client.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.agents import CostEntry, Run, RunStep
from ..models.enums import GlobalRole, ProjectRole
from ..models.ops import Deployment
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from ..services.office import (
    EVENT_CAP_DEFAULT, EVENT_CAP_MAX, EVENT_VERSION, LIVE_WINDOW_MS, SEQ_SLOTS,
    PriceTable, RunCtx, deploy_anchor_step_id, deploy_step_id, deployment_events,
    dedupe_seq, run_boundary_events, session_id, session_seen_event, step_events,
    ts_text,
)
from .deps import Access, build_access, get_current_user, get_project_access
# The set of allowed projects comes from THE SAME function as the live socket, and that one
# in turn is `api/projects.py:74-91` (two queries plus `build_access_bulk`, no round per
# project). There should be exactly one definition of "may see", not three that drift.
# irgendwann auseinanderlaufen.
from .office_ws import compute_acl

router = APIRouter(tags=["office"])

SESSION_LIMIT_DEFAULT = 50
SESSION_LIMIT_MAX = 200
SINCE_HOURS_DEFAULT = 24 * 7      # one week: the office is short term memory, not an archive
SINCE_HOURS_MAX = 24 * 365

# How many runs are considered for a session list. The limit applies to sessions, but the
# filtering runs over runs, and one ticket easily reaches a dozen (planning, execution,
# continuations, review, subagents). The factor is generous enough that `limit` sessions
# practically always fill up, and still caps the query hard.
RUN_SCAN_FACTOR = 12
RUN_SCAN_MAX = 3000

# Levels of tree closure. Mirrors `worker/runtime.MAX_DELEGATION_DEPTH = 2`, deliberately as
# a constant of its own instead of importing `worker.runtime`, which would drag half the
# agent runtime (provider adapters, tool client) into a read endpoint.
TREE_LEVELS = 2

SESSION_STATUS = ("all", "live", "recent")

# ── Personalakte: Konstanten ────────────────────────────────────────────────

TOOL_LIMIT_DEFAULT = 8
TOOL_LIMIT_MAX = 50

# **Three bars instead of one success rate.** `office/engine.ts::verdictOf` already treats
# `planned` as ok: a planning run that delivered a plan IS finished, it is just not called
# `success`. A rate of `success/runs` would show the `project_manager` at 0 % (0 success,
# 7 planned) and the `architect` at 6 % (3 success, 36 planned); both contradict the house's
# own code. The three sets are disjoint and are never combined into one number.
DELIVERED_STATUS = ("success", "planned")
WAITING_STATUS = ("blocked",)                        # waiting for a person
ABORTED_STATUS = ("failed", "loop_exhausted")

# The buckets the view shows: <1 min · <5 min · <20 min · <80 min · above that.
DURATION_DISPLAY_EDGES_MS = (60_000, 300_000, 1_200_000, 4_800_000)

# The fine ladder p50 and p90 are read from. It contains every display boundary so that BOTH
# outputs fall out of the same single query (the display buckets are sums of ladder buckets).
# Why buckets at all: `percentile_cont` exists only in Postgres and the tests run on SQLite,
# the same consideration that already made `office.py` do without `WITH RECURSIVE`. The
# ladder is fine at the bottom (516 of 632 runs are there) and coarse at the top (one run
# took 36.5 hours).
DURATION_LADDER_MS = (
    1_000, 2_000, 3_000, 5_000, 7_500, 10_000, 15_000, 20_000, 30_000, 45_000, 60_000,
    90_000, 120_000, 180_000, 240_000, 300_000,
    450_000, 600_000, 900_000, 1_200_000,
    1_800_000, 2_400_000, 3_600_000, 4_800_000,
    7_200_000, 14_400_000, 28_800_000, 86_400_000, 172_800_000,
)


# ── Kleinkram ───────────────────────────────────────────────────────────────

def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Read naive timestamps as UTC. SQLite delivers them without a zone; without this line
    the same row would shift by hours depending on the database."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _iso(value: dt.datetime | None) -> str | None:
    """ISO-8601 with an explicit zone. A bare `datetime` would be read as local time, and the
    timeline would stand hours off depending on the browser."""
    aware = _aware(value)
    return None if aware is None else aware.astimezone(dt.timezone.utc).isoformat()


def _seq(step_id: int, slot: int) -> int:
    """`seq` of a row, the same arithmetic as in `services/office`."""
    return int(step_id) * SEQ_SLOTS + slot


def _sid(kind: str, ref: int) -> str:
    return f"{kind}:{ref}"


def _not_found() -> HTTPException:
    """One single wording for "does not exist" and "is not yours". Two distinguishable
    answers would be a directory of other people's tickets."""
    return HTTPException(404, "Session not found")


# ── Autorisierung ───────────────────────────────────────────────────────────

async def _visible_runs(db: AsyncSession, user: User):
    """SQL condition: which runs may this user see at all?

    Admins without a filter. Otherwise: runs of the allowed projects plus the user's **own**
    projectless runs (assistant, job), which have no project room to become visible through.
    """
    if user.global_role == GlobalRole.admin:
        return true()
    allowed = await compute_acl(db, user)
    return or_(
        Run.project_id.in_(allowed),
        and_(Run.project_id.is_(None), Run.owner_id == user.id),
    )


async def _authorize(db: AsyncSession, user: User, *, project_id: int | None,
                     owner_id: int | None) -> None:
    """May the user read this session? Otherwise 404.

    The values come from the root run or the ticket, not from the path. A projectless run
    belongs to its owner (and to an admin); without either it is readable by nobody but an
    admin, because an ownerless run is not a public run.
    """
    if project_id is not None:
        project = await db.get(Project, project_id)
        if project is None:
            raise _not_found()
        try:
            access = await build_access(project, user, db)
        except HTTPException:
            raise _not_found() from None
        if not access.has_role(ProjectRole.viewer):
            raise _not_found()
        return
    if user.global_role == GlobalRole.admin:
        return
    if owner_id is None or owner_id != user.id:
        raise _not_found()


# ── Laufmenge einer Session ─────────────────────────────────────────────────

async def _load_session_runs(db: AsyncSession, kind: str, ref: int) -> tuple[list[Run], Issue | None]:
    """Every run of a session, ascending. Raises 404 when the address points nowhere.

    `issue:` needs no tree closure: subruns inherit the `issue_id`, so one query over
    `ix_runs_issue_started` has the whole tree. `run:` closes in two rounds over
    `parent_run_id`; the tree cannot get deeper (`TREE_LEVELS`).
    """
    if kind == "issue":
        issue = await db.get(Issue, ref)
        runs = (await db.execute(
            select(Run).where(Run.issue_id == ref).order_by(Run.id)
        )).scalars().all()
        # Ticket gone AND no run left: there never was anything, or it is deleted for good.
        # A ticket that still exists without runs is a *tidied* session instead and gets an
        # honest 200 with `purged: true`.
        if issue is None and not runs:
            raise _not_found()
        return list(runs), issue

    root = await db.get(Run, ref)
    # Only real roots are addressable: if a child run had its own `run:` address there would
    # be two rooms for the same tree, and rewinding would show something different per link.
    # etwas anderes.
    if root is None or root.issue_id is not None or root.parent_run_id is not None:
        raise _not_found()
    runs = [root]
    frontier = [root.id]
    for _ in range(TREE_LEVELS):
        kids = (await db.execute(
            select(Run).where(Run.parent_run_id.in_(frontier)).order_by(Run.id)
        )).scalars().all()
        if not kids:
            break
        runs.extend(kids)
        frontier = [k.id for k in kids]
    runs.sort(key=lambda r: r.id)
    return runs, None


def _root_run(runs: list[Run]) -> Run | None:
    """The run the session hangs on: the root, otherwise the oldest."""
    for run in runs:
        if run.parent_run_id is None:
            return run
    return runs[0] if runs else None


# ── Aggregates (ONE query each, never one per run) ──────────────────────────

async def _step_bounds(db: AsyncSession, run_ids: list[int]) -> dict[int, dict]:
    """Per run: first and last step id, the count, and whether boundary rows of its own exist.

    The two `run_start`/`run_end` counters decide whether `run_boundary_events` has to
    synthesise the boundaries. Counting them here costs nothing and saves a second round,
    and above all it does not guess from the (possibly truncated) loaded steps whether a
    `run_start` exists.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(
            RunStep.run_id, func.min(RunStep.id), func.max(RunStep.id), func.count(),
            func.sum(case((RunStep.kind == "run_start", 1), else_=0)),
            func.sum(case((RunStep.kind == "run_end", 1), else_=0)),
        ).where(RunStep.run_id.in_(run_ids)).group_by(RunStep.run_id)
    )).all()
    return {
        run_id: {"first": first, "last": last, "count": int(count or 0),
                 "has_start": bool(starts or 0), "has_end": bool(ends or 0)}
        for run_id, first, last, count, starts, ends in rows
    }


async def _project_keys(db: AsyncSession, project_ids) -> dict[int, str]:
    """`project_id → key` in ONE query. `None` and 0 drop out along the way."""
    ids = {int(p) for p in project_ids if p}
    if not ids:
        return {}
    return dict((await db.execute(
        select(Project.id, Project.key).where(Project.id.in_(ids)))).all())


def _entry_priced(priced: bool | None, provider: str, model: str, prices: PriceTable) -> bool:
    """Is this cost entry priced?

    Where `priced` says so, it holds, even when the catalog entry has been deleted since.
    `api/cost.py:148` states explicitly that a deleted catalog entry must not change old
    runs. Only the three valued NULL (an old row that never knew the distinction) is
    resolved against the catalog while reading.
    """
    if priced is not None:
        return bool(priced)
    return prices.has(provider or "", model or "")


async def _billed_by_run(db: AsyncSession, run_ids: list[int],
                         prices: PriceTable) -> dict[int, dict]:
    """Billed cost per run from `cost_entries`, the authoritative amount.

    Grouped down to (provider, model, priced), because `unpriced_models` should name WHICH
    model had no price; a plain sum per run could no longer say that.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(CostEntry.run_id, CostEntry.provider, CostEntry.model, CostEntry.priced,
               func.sum(CostEntry.input_tokens), func.sum(CostEntry.output_tokens),
               func.sum(CostEntry.cache_read_tokens), func.sum(CostEntry.cost_usd))
        .where(CostEntry.run_id.in_(run_ids))
        .group_by(CostEntry.run_id, CostEntry.provider, CostEntry.model, CostEntry.priced)
    )).all()
    out: dict[int, dict] = {}
    for run_id, provider, model, priced, in_tok, out_tok, cache_read, usd in rows:
        agg = out.setdefault(run_id, {
            "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
            "cost_usd": 0.0, "priced": True, "unpriced_models": [],
        })
        agg["in_tokens"] += int(in_tok or 0)
        agg["out_tokens"] += int(out_tok or 0)
        agg["cache_read_tokens"] += int(cache_read or 0)
        agg["cost_usd"] += float(usd or 0.0)
        if not _entry_priced(priced, provider or "", model or "", prices):
            agg["priced"] = False
            label = f"{provider or ''}/{model or ''}"
            if label not in agg["unpriced_models"]:
                agg["unpriced_models"].append(label)
    return out


async def _step_tokens(db: AsyncSession, run_ids: list[int]) -> dict[tuple[int, str, str], dict]:
    """Tokens per (run, provider, model) **of the step**.

    The step knows who actually answered; `run.model` only knows who was asked. If the run
    switched to the fallback provider halfway through, grouping by `run.model` is simply
    wrong: it would attribute the tokens of one model to the other.
    """
    if not run_ids:
        return {}
    rows = (await db.execute(
        select(RunStep.run_id, RunStep.provider, RunStep.model,
               func.sum(RunStep.in_tokens), func.sum(RunStep.out_tokens),
               func.sum(RunStep.cache_read_tokens))
        .where(RunStep.run_id.in_(run_ids))
        .group_by(RunStep.run_id, RunStep.provider, RunStep.model)
    )).all()
    out: dict[tuple[int, str, str], dict] = {}
    for run_id, provider, model, in_tok, out_tok, cache_read in rows:
        tokens = (int(in_tok or 0), int(out_tok or 0), int(cache_read or 0))
        if not any(tokens):
            continue    # rows without tokens (tools, system text) are not model turns
        key = (run_id, provider or "", model or "")
        agg = out.setdefault(key, {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0})
        agg["in_tokens"] += tokens[0]
        agg["out_tokens"] += tokens[1]
        agg["cache_read_tokens"] += tokens[2]
    return out


def _token_groups(run: Run, by_key: dict[tuple[int, str, str], dict]) -> list[tuple[str, str, dict]]:
    """The model turns of a run, with a fallback to the `runs` row.

    A run from before the instrumentation has no tokens on its steps, but it does have its
    totals on the run. Without this fallback the estimate would be 0 everywhere on day one
    and the whole cost view useless. The fallback naturally knows only ONE model: a switch
    to the fallback provider stays invisible in old data, because nobody recorded it then.
    """
    groups = [(provider, model, agg) for (rid, provider, model), agg in by_key.items()
              if rid == run.id]
    if groups:
        return groups
    if not (run.input_tokens or run.output_tokens):
        return []
    return [(run.provider or "", run.model or "",
             {"in_tokens": int(run.input_tokens or 0), "out_tokens": int(run.output_tokens or 0),
              "cache_read_tokens": 0})]


# ── Sessionliste ────────────────────────────────────────────────────────────

async def _resolve_sids(db: AsyncSession, runs: list[Run]) -> tuple[dict[int, str], list[Run]]:
    """run_id → `sid`, and the parent runs loaded for it.

    A child run without a ticket has to end up at the ROOT run, otherwise a delegated
    subagent would get an office of its own in the list, and that office would not even be
    reachable through `/sessions/run/{id}` (only roots are addressable). `session_id()`
    jumps one step upwards, here the chain is walked to its end: missing parents are loaded
    per round in ONE query over the primary key, at most `TREE_LEVELS` rounds.

    The parents loaded this way join the session as members (otherwise `runs`, `started_at`
    and the cost would be wrong), even when they lie outside the time window or are archived
    differently. The tree is the unit, not the window.
    """
    by_id = {r.id: r for r in runs}
    extra: list[Run] = []
    for _ in range(TREE_LEVELS):
        missing = {r.parent_run_id for r in by_id.values()
                   if r.issue_id is None and r.parent_run_id and r.parent_run_id not in by_id}
        if not missing:
            break
        parents = (await db.execute(select(Run).where(Run.id.in_(missing)))).scalars().all()
        if not parents:
            break
        extra.extend(parents)
        by_id.update({p.id: p for p in parents})
    sids: dict[int, str] = {}
    for run in by_id.values():
        node = run
        # Bounded, so that a cyclic parent_run_id (from damaged data) cannot hang.
        for _ in range(TREE_LEVELS + 1):
            parent = by_id.get(node.parent_run_id) if node.parent_run_id else None
            if parent is None:
                break
            node = parent
        # `session_id` stays the one truth about the address form, including its own
        # fallback when the parent run does not exist any more.
        sids[run.id] = session_id(node)
    return sids, extra


async def _sessions_payload(db: AsyncSession, *, where, limit: int, since_hours: int,
                            status: str, archived: bool) -> dict:
    """The shared body of both session lists: the project tab and the global page show the
    same shape, because they go through the same function."""
    limit = _clamp(limit, 1, SESSION_LIMIT_MAX)
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    if status not in SESSION_STATUS:
        raise Error(400, "err.status_one_of",
                     "status has to be one of {allowed}", allowed=', '.join(SESSION_STATUS))
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=since_hours)
    scan = min(limit * RUN_SCAN_FACTOR, RUN_SCAN_MAX)

    rows = (await db.execute(
        select(Run, Issue.key, Issue.summary, Issue.project_id)
        .outerjoin(Issue, Issue.id == Run.issue_id)
        .where(where, Run.started_at >= cutoff, Run.archived.is_(archived))
        .order_by(Run.id.desc()).limit(scan)
    )).all()
    runs = [r for r, _k, _s, _p in rows]
    issue_meta = {r.id: (key, summary, issue_pid) for r, key, summary, issue_pid in rows}
    if not runs:
        return {"live_window_ms": LIVE_WINDOW_MS, "sessions": []}

    sids, extra = await _resolve_sids(db, runs)
    runs.extend(extra)
    run_ids = [r.id for r in runs]

    # Project keys in ONE query. The run carries its project itself now; on old rows without
    # a `project_id` it still sits on the ticket.
    project_ids = {r.project_id for r in runs if r.project_id} | {
        pid for _k, _s, pid in issue_meta.values() if pid}
    project_keys: dict[int, str] = {}
    if project_ids:
        project_keys = dict((await db.execute(
            select(Project.id, Project.key).where(Project.id.in_(project_ids)))).all())

    bounds = await _step_bounds(db, run_ids)
    prices = await PriceTable.load(db)
    billed = await _billed_by_run(db, run_ids, prices)

    grouped: dict[str, list[Run]] = {}
    for run in runs:
        grouped.setdefault(sids.get(run.id) or session_id(run), []).append(run)

    sessions = []
    for sid, members in grouped.items():
        members.sort(key=lambda r: r.id)
        kind, _, ref = sid.partition(":")
        root = _root_run(members)
        key, summary, issue_pid = issue_meta.get(root.id, ("", "", None))
        if not key:
            # The root run may have been loaded from outside the window, in which case the
            # ticket label sits on one of the members.
            for member in members:
                key, summary, issue_pid = issue_meta.get(member.id, ("", "", None))
                if key:
                    break
        project_id = root.project_id or issue_pid
        events = sum(bounds.get(r.id, {}).get("count", 0) for r in members)
        starts = [_aware(r.started_at) for r in members if r.started_at]
        ends = [_aware(r.finished_at) for r in members if r.finished_at]
        last_event = max([*starts, *ends], default=None)
        running = any((r.status or "") == "running" for r in members)
        # The database status alone lies after a worker crash: `status='running'` then stays
        # forever. `api/runs.py:72` had to read the Redis hash for that reason. Here the 90 s
        # window does the job: it costs no round and claims nothing.
        live = running and last_event is not None and (
            (now - last_event).total_seconds() * 1000 <= LIVE_WINDOW_MS)
        if status == "live" and not live:
            continue
        if status == "recent" and live:
            continue
        cost = [billed[r.id] for r in members if r.id in billed]
        sessions.append({
            "sid": sid, "kind": kind, "ref": int(ref) if ref.isdigit() else ref,
            "title": (summary or "").strip() or (root.agent or f"Lauf {root.id}"),
            "issue_key": key or "", "project_id": project_id,
            "project_key": project_keys.get(project_id or 0, ""),
            "started_at": _iso(min(starts) if starts else None),
            "last_event_at": _iso(last_event),
            "live": live,
            "runs": len(members),
            "agents": len({r.agent or "" for r in members}),
            "events": events,
            "archived": all(r.archived for r in members),
            # Not a single step left: either removed by the retention or never written. For
            # the room both mean the same, there is nothing to replay.
            "purged": events == 0,
            "status": "running" if running else (max(members, key=lambda r: r.id).status or ""),
            # Tokens from the run (they are always there), cached tokens only from the cost
            # entries, because the run does not track them separately.
            "in_tokens": sum(int(r.input_tokens or 0) for r in members),
            "out_tokens": sum(int(r.output_tokens or 0) for r in members),
            "cache_read_tokens": sum(c["cache_read_tokens"] for c in cost),
            "cost_usd": round(sum(c["cost_usd"] for c in cost), 6),
            "cost_partial": any(not c["priced"] for c in cost),
        })
    sessions.sort(key=lambda s: (s["last_event_at"] or "", s["sid"]), reverse=True)
    return {"live_window_ms": LIVE_WINDOW_MS, "sessions": sessions[:limit]}


@router.get("/projects/{project_id}/office/sessions")
async def project_sessions(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    limit: int = SESSION_LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    archived: bool = False,
):
    """The sessions of one project, the content of the project tab "🏢 office".

    A foreign project is 404, which `get_project_access` takes care of.
    """
    pid = access.project.id
    # Preferably over `Run.project_id` (it survives the deletion of a ticket and also carries
    # the project bound job runs without a ticket). Old rows without a `project_id` are still
    # counted through the ticket, exactly as `api/cost.py:28-31` does.
    where = or_(Run.project_id == pid,
                and_(Run.project_id.is_(None), Issue.project_id == pid))
    return await _sessions_payload(db, where=where, limit=limit, since_hours=since_hours,
                                   status=status, archived=archived)


@router.get("/office/sessions")
async def global_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = SESSION_LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    project_id: int | None = None,
):
    """Every session this user may see, the full page `/buero`.

    `project_id` **narrows** the already allowed set and never authorises: entering a foreign
    project yields an empty list, not access. That is why the filter stands as an additional
    AND next to the visibility condition, not in its place.
    """
    where = await _visible_runs(db, user)
    if project_id is not None:
        where = and_(where, Run.project_id == project_id)
    return await _sessions_payload(db, where=where, limit=limit, since_hours=since_hours,
                                   status=status, archived=False)


# ── Ereignisse ──────────────────────────────────────────────────────────────

def _agent_row(run: Run, billed: dict | None, *,
               issue_key: str = "", project_key: str = "") -> dict:
    """One row of the `agents[]` roster, straight from `runs` instead of from the events.

    The roster earns its place exactly when truncation happens: it cuts from the OLDEST end
    (the room should show the present), so the `run_start` events would go first, and the
    office would stay empty although every agent is there.

    **`issue_key`/`project_key` belong here since a room shows several sessions.** The
    session tabs in the header group the roster by them (`TopBar.sitzungsSchluessel`: by
    ticket inside a project, by project globally) and dim what does not belong. Without these
    two fields **every** character would fall into "(no project)", the tab row would stay
    single and therefore invisible, which is exactly how it used to look. Empty text means
    "not known"; the caller fills in what it has loaded anyway, instead of creating a round
    per run here.
    """
    tokens = billed or {}
    return {
        "run_id": run.id, "agent_id": f"run:{run.id}", "agent": run.agent or "",
        "issue_key": issue_key or "", "project_id": run.project_id,
        "project_key": project_key or "",
        "phase": run.phase or "", "provider": run.provider or "", "model": run.model or "",
        "parent_run_id": run.parent_run_id, "parent_tool_use_id": run.parent_tool_use_id,
        "spawn_depth": int(run.spawn_depth or 0), "status": run.status or "",
        "blocker_kind": run.blocker_kind,
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "continuation_index": int(run.continuation_index or 0),
        "in_tokens": tokens.get("in_tokens", int(run.input_tokens or 0)),
        "out_tokens": tokens.get("out_tokens", int(run.output_tokens or 0)),
        "cache_read_tokens": tokens.get("cache_read_tokens", 0),
        "cost_usd": round(tokens.get("cost_usd", float(run.cost_usd or 0.0)), 6),
        # Three valued: without cost entries nothing is billed, and that is not "unpriced"
        # but "not known yet". A `False` here would have reported every running run as a gap
        # in the price catalog.
        "cost_priced": None if billed is None else billed["priced"],
    }


async def _legacy_deploy_events(db: AsyncSession, *, issue_ids: set[int], steps: list[RunStep],
                                ctxs: dict[int, RunCtx], bounds: dict[int, dict]) -> list[dict]:
    """Legacy deployments with a borrowed `seq`, in **one** query instead of one per run.

    Since the watcher (`services/deploy_watch.py`) every deployment writes its own row.
    Before that it did not: 130 of the 186 legacy rows have none at all, and the 56 that do
    are precisely the rejected ones. Without this path they would be missing from the replay
    exactly where something happened.

    Only applies when the session has tickets at all; `ix_deployments_issue` covers the
    query. Where the watcher already told the story nothing is borrowed, otherwise the same
    Deploy zweimal im Raum.
    """
    if not issue_ids or not steps:
        return []
    deps = (await db.execute(
        select(Deployment).where(Deployment.issue_id.in_(issue_ids))
        .order_by(Deployment.id))).scalars().all()
    if not deps:
        return []
    step_by_id = {s.id: s for s in steps}
    # Slot 3 is taken wherever a boundary is synthesised: `run_end` sits at `last*4+3`,
    # `run_start` at `first*4-1`, and that is slot 3 of the row before it.
    taken: set[int] = set()
    for b in bounds.values():
        if b["last"] and not b["has_end"]:
            taken.add(b["last"])
        if b["first"] and not b["has_start"]:
            taken.add(b["first"] - 1)
    tells = {deploy_step_id(s) for s in steps} - {0}

    out: list[dict] = []
    for dep in deps:
        if dep.id in tells:
            continue
        anchor = deploy_anchor_step_id(steps, dep.created_at, blocked=taken)
        ctx = ctxs.get(step_by_id[anchor].run_id) if anchor else None
        if ctx is None:
            continue
        events = deployment_events(dep, ctx, anchor_step_id=anchor)
        if events:
            # Two deployments do not share a slot, otherwise the recorder would lose one of
            # them (it deduplicates by `seq` alone).
            taken.add(anchor)
            out.extend(events)
    return out


@router.get("/office/sessions/{kind}/{ref}/events")
async def session_events(
    kind: str, ref: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = EVENT_CAP_DEFAULT,
    after_seq: int = 0,
):
    """The snapshot of a room: roster plus events, strictly ascending by `seq`.

    `seq` is the arrival order (`run_steps.id`), **never** `ts`: under
    `WORKER_CONCURRENCY > 1` the timestamp can run backwards against the order, and sorted by
    `ts` the room would replay events that never happened that way.

    On truncation the OLDEST falls away; `seq_from` tells the client where its log begins.
    """
    if kind not in ("issue", "run"):
        raise _not_found()
    cap = _clamp(limit, 1, EVENT_CAP_MAX)
    runs, issue = await _load_session_runs(db, kind, ref)
    sid = _sid(kind, ref)

    root = _root_run(runs)
    await _authorize(db, user,
                     project_id=(root.project_id if root else None) or (issue.project_id if issue else None),
                     owner_id=root.owner_id if root else None)
    if not runs:
        # Tidied up: the ticket is still there, its runs fell victim to the retention. A 404
        # would be a lie here, the session existed, and the interface should be allowed to
        # say so ("this room was cleared") instead of "not found".
        return {"sid": sid, "v": EVENT_VERSION, "seq_from": 0, "seq_to": 0, "count": 0,
                "truncated": False, "purged": True, "agents": [], "events": []}

    run_ids = [r.id for r in runs]
    bounds = await _step_bounds(db, run_ids)
    total_steps = sum(b["count"] for b in bounds.values())

    # A step carries `seq = id * SEQ_SLOTS + slot`, so the lower bound is
    # `after_seq // SEQ_SLOTS`. Python filters more finely afterwards on the finished event:
    # one row can carry up to four `seq`, which the SQL bound cannot resolve.
    after_id = max(0, after_seq // SEQ_SLOTS)
    # Fetch descending and turn it around afterwards: truncation happens at the oldest end.
    # Ascending with LIMIT would give the BEGINNING of the log, which is exactly what nobody
    # wants to see while something is running. `cap + 1` reveals the truncation without a
    # second COUNT.
    step_rows = (await db.execute(
        select(RunStep).where(RunStep.run_id.in_(run_ids), RunStep.id >= after_id)
        .order_by(RunStep.id.desc()).limit(cap + 1)
    )).scalars().all()
    truncated = len(step_rows) > cap
    steps = sorted(step_rows[:cap] if truncated else list(step_rows), key=lambda s: s.id)

    issue_keys: dict[int, str] = {}
    issue_ids = {r.issue_id for r in runs if r.issue_id}
    if issue_ids:
        issue_keys = dict((await db.execute(
            select(Issue.id, Issue.key).where(Issue.id.in_(issue_ids)))).all())
    project_keys = await _project_keys(
        db, {r.project_id for r in runs} | {issue.project_id if issue else None})

    ctxs = {r.id: RunCtx.from_run(r, issue_key=issue_keys.get(r.issue_id or 0, "")) for r in runs}
    events: list[dict] = []
    for step in steps:
        ctx = ctxs.get(step.run_id)
        if ctx is not None:
            events.extend(step_events(step, ctx))
    # Boundaries only for runs that have none of their own (old runs). Newer runs write them
    # themselves; synthesising those here as well would show every agent twice.
    for run in runs:
        b = bounds.get(run.id)
        if b is None:
            continue
        events.extend(run_boundary_events(
            run, ctxs[run.id],
            first_step_id=None if b["has_start"] else b["first"],
            last_step_id=None if b["has_end"] else b["last"],
        ))
    # Deployments from the time before the watcher: borrowed `seq`, no backfill.
    events.extend(await _legacy_deploy_events(
        db, issue_ids=issue_ids, steps=steps, ctxs=ctxs, bounds=bounds))

    events = [e for e in events if e["seq"] > after_seq]
    if truncated and steps:
        # Whatever lies below the truncation flies out even when it is a synthesised boundary
        # or a borrowed deploy event, otherwise a `run_start` would sit under `seq_from` and
        # the client would think its log complete. Whoever is missing there is in the roster.
        floor = _seq(steps[0].id, 0) - 1
        events = [e for e in events if e["seq"] >= floor]
    events.sort(key=lambda e: e["seq"])

    if events and after_seq <= 0:
        # The header of the room: title, ticket, project. It stands at the very front, right
        # under the first real event. Only on a full fetch: when catching up with `after_seq`
        # the client has had it for a long time and would otherwise get it a second time with
        # a new `seq` (the recorder deduplicates by `seq`).
        project_key = project_keys.get(root.project_id or (issue.project_id if issue else 0) or 0, "")
        title = (issue.summary if issue else "") or root.agent or f"Lauf {root.id}"
        events.insert(0, session_seen_event(
            ctxs[root.id], title=title, project_key=project_key,
            started_at=root.started_at, seq=events[0]["seq"] - 1))

    billed = await _billed_by_run(db, run_ids, await PriceTable.load(db))
    return {
        "sid": sid, "v": EVENT_VERSION,
        "seq_from": events[0]["seq"] if events else 0,
        "seq_to": events[-1]["seq"] if events else 0,
        "count": len(events), "truncated": truncated,
        "purged": total_steps == 0,
        "agents": [_agent_row(r, billed.get(r.id),
                              issue_key=issue_keys.get(r.issue_id or 0, ""),
                              project_key=project_keys.get(r.project_id or 0, ""))
                   for r in runs],
        "events": events,
    }


# ── Events of ALL sessions (one room for the global page) ───────────────────
#
# Why this holds together at all: `seq = run_steps.id * SEQ_SLOTS + slot`, and
# `run_steps.id` is SERIAL, globally monotonic across **all** runs and projects. Events of
# different sessions therefore form ONE ascending series, and `Recorder.push` deduplicates
# by exactly that number. The seat holds too: `seatOf` computes `hash32(run_id) % 12`, which
# is independent of the session.
#
# What does NOT happen here, and why:
#
# · **No `session_seen`.** The header is one title per room; fourteen titles for one room
#   would be fourteen contradictions. The film leaves it out for the same reason
#   (`services/office_film.py`, trap 3), and `mapEvent` produces nothing from it anyway.
# · **No legacy deployments** (`_legacy_deploy_events`). Those borrow the `seq` of a foreign
#   step row that is closest in time, and across sessions that would with high probability
#   be the row of a *different* run, so the deploy would stand in the wrong room under a
#   foreign `sid`. Deployments since the watcher have a real row and come through
#   `step_events` like everything else.

# Vorgabefenster. Gemessen am Bestand (05.08.2026): 1 h → 1 Lauf, 6 h → 6, **12 h → 14**,
# 24 h → 23, 72 h → 69. `office/const.ts` allows `MAX_ACTORS = 24` characters at once and
# evicts above that (first `retired`, then `done`, then the oldest), so at 24 h the room
# would permanently sit at the edge and lose a character with every new run, and at 72 h it
# would flicker. Twelve hours leave plenty of room and still cover a whole working day
# backwards. The interface names the window explicitly ("the last 12 hours"), because a
# silent excerpt would be a claim about the day.
EVENTS_SINCE_HOURS_DEFAULT = 12


@router.get("/office/events")
async def all_events(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    since_hours: int = EVENTS_SINCE_HOURS_DEFAULT,
    limit: int = EVENT_CAP_DEFAULT,
    after_seq: int = 0,
    project_id: int | None = None,
):
    """A snapshot across **all** sessions of a time window, the global page.

    The answer has the shape of `session_events` so the frontend takes the same path; instead
    of the `sid` it carries the window (`since_hours`, `window_from`, `window_to`) and says
    how many sessions and runs came together in it.

    **Permissions come from `_visible_runs`**, the same function as `/office/sessions` and
    therefore ultimately from `compute_acl`. There is no second definition of "may see".
    `project_id` **narrows** the already allowed set and never authorises: it stands as an
    additional AND next to it, not in its place. A foreign project therefore yields an empty
    answer, not access, and not a 403 either (which would betray the existence).
    Existenz).

    Truncation happens at the **oldest** end as it does there, because the room should show
    the present. The roster (`agents[]`) stays complete anyway: it comes from `runs`, not from
    the events, and without it exactly those characters would be missing whose `run_start`
    Opfer fiel.
    """
    cap = _clamp(limit, 1, EVENT_CAP_MAX)
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=since_hours)

    where = await _visible_runs(db, user)
    if project_id is not None:
        where = and_(where, Run.project_id == project_id)

    def empty(agents=(), events=(), truncated=False) -> dict:
        return {
            "v": EVENT_VERSION, "scope": "all",
            "since_hours": since_hours,
            "window_from": _iso(cutoff), "window_to": _iso(now),
            "sessions": 0, "runs": len(agents),
            "seq_from": 0, "seq_to": 0, "count": 0,
            "truncated": truncated, "purged": False,
            "agents": list(agents), "events": list(events),
        }

    # Fetch descending and turn it around afterwards (as in `session_events`): ascending with
    # LIMIT would give the BEGINNING of the window, which is exactly what nobody wants to see
    # while something is running. `cap + 1` reveals the truncation without a second COUNT.
    after_id = max(0, after_seq // SEQ_SLOTS)
    step_rows = (await db.execute(
        select(RunStep).join(Run, Run.id == RunStep.run_id)
        .where(where, RunStep.created_at >= cutoff, RunStep.id >= after_id)
        .order_by(RunStep.id.desc()).limit(cap + 1)
    )).scalars().all()
    truncated = len(step_rows) > cap
    steps = sorted(step_rows[:cap] if truncated else list(step_rows), key=lambda s: s.id)
    if not steps:
        return empty(truncated=truncated)

    run_ids = sorted({s.run_id for s in steps})
    runs = sorted(
        (await db.execute(select(Run).where(Run.id.in_(run_ids)))).scalars().all(),
        key=lambda r: r.id)

    issue_ids = {r.issue_id for r in runs if r.issue_id}
    issue_keys: dict[int, str] = {}
    if issue_ids:
        issue_keys = dict((await db.execute(
            select(Issue.id, Issue.key).where(Issue.id.in_(issue_ids)))).all())
    project_keys = await _project_keys(db, {r.project_id for r in runs})

    ctxs = {r.id: RunCtx.from_run(r, issue_key=issue_keys.get(r.issue_id or 0, ""))
            for r in runs}

    # Boundaries of the **window** per run, deliberately from the LOADED rows and not from
    # `_step_bounds` over the whole run: whether a `run_start` is there has to refer to the
    # window. A run whose start row lies yesterday would otherwise have no appearance today,
    # and its agent would never sit at a desk. As a side effect all synthesised boundaries lie
    # between `steps[0]` and `steps[-1]`, which is why no extra truncation floor is needed here
    # (unlike in `session_events`).
    window: dict[int, dict] = {}
    for s in steps:
        b = window.setdefault(s.run_id, {"first": s.id, "last": s.id,
                                          "has_start": False, "has_end": False})
        b["last"] = s.id
        kind = (getattr(s, "kind", "") or "").strip()
        if kind == "run_start":
            b["has_start"] = True
        elif kind == "run_end":
            b["has_end"] = True

    events: list[dict] = []
    for s in steps:
        ctx = ctxs.get(s.run_id)
        if ctx is not None:
            events.extend(step_events(s, ctx))

    windowstart = ts_text(cutoff)
    for run in runs:
        b = window.get(run.id)
        if b is None:
            continue
        limits = run_boundary_events(
            run, ctxs[run.id],
            first_step_id=None if b["has_start"] else b["first"],
            last_step_id=None if b["has_end"] else b["last"],
        )
        start = _aware(run.started_at)
        end = _aware(run.finished_at) or start
        for ev in limits:
            if ev["kind"] == "run_start" and start is not None and start < cutoff:
                # Trap 1: the boundary added afterwards carries `run.started_at`, so for a run
                # that began before the window that is a timestamp from yesterday. Unclamped,
                # the timeline would stretch the whole room back to yesterday, and that would
                # look like a bug in the engine.
                ev["ts"] = windowstart
            elif ev["kind"] == "run_end" and end is not None and end > now:
                # Trap 2: an end beyond the edge of the window does not belong inside. Filtering
                # only here is deliberate: before this it is not settled whether a `run_end`
                # boundary appears at all (a running run gets none). With a trailing window
                # (edge = now) this is the exception; the rule stands anyway, because otherwise
                # the window is only correct as long as nobody puts `window_to` in the past.
                continue
            events.append(ev)

    events = [e for e in events if e["seq"] > after_seq]
    # `seq` is the arrival order, never `ts`. On a tie the END goes before the beginning:
    # first somebody leaves the room, then the next one comes in.
    events.sort(key=lambda e: (e["seq"], 0 if e["kind"] == "run_end" else 1))
    # And then resolve the collision: `run_end` at `last*4+3` and `run_start` at `first*4-1`
    # are the same number as soon as two runs with neighbouring row ids follow each other,
    # which across sessions is the normal case. Without this `Recorder.push` would silently
    # drop the second event, and an agent would never enter.
    dedupe_seq(events)

    billed = await _billed_by_run(db, run_ids, await PriceTable.load(db))
    answer = empty(
        agents=[_agent_row(r, billed.get(r.id),
                           issue_key=issue_keys.get(r.issue_id or 0, ""),
                           project_key=project_keys.get(r.project_id or 0, ""))
                for r in runs],
        events=events, truncated=truncated)
    answer.update({
        "sessions": len({ctx.sid for ctx in ctxs.values()}),
        "seq_from": events[0]["seq"] if events else 0,
        "seq_to": events[-1]["seq"] if events else 0,
        "count": len(events),
    })
    return answer


# ── Kosten ──────────────────────────────────────────────────────────────────

@router.get("/office/sessions/{kind}/{ref}/cost")
async def session_cost(
    kind: str, ref: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Two numbers on purpose: billed and estimated.

    `cost_usd_billed` is the sum of the `cost_entries`, what was actually charged at the time
    of the run. `cost_usd_estimated` prices the **step** tokens against TODAY's catalog,
    grouped by the model of the step, which stays right even when the run switched to the
    fallback provider halfway through. Both stand side by side and neither overwrites the
    other: one says what it cost, the other what it would cost today.

    `unpriced` makes a distinction that was not possible before: a catalog entry with price
    0.00 is *priced and free* (the local model), no entry at all is *unknown*. Both used to
    produce the same 0.00, and every gap in the catalog looked like a gift.
    """
    if kind not in ("issue", "run"):
        raise _not_found()
    runs, issue = await _load_session_runs(db, kind, ref)
    root = _root_run(runs)
    await _authorize(db, user,
                     project_id=(root.project_id if root else None) or (issue.project_id if issue else None),
                     owner_id=root.owner_id if root else None)

    prices = await PriceTable.load(db)
    run_ids = [r.id for r in runs]
    billed = await _billed_by_run(db, run_ids, prices)
    steps = await _step_tokens(db, run_ids)

    total = {"in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
             "cost_usd_billed": 0.0, "cost_usd_estimated": 0.0}
    by_agent: dict[str, dict] = {}
    by_model: dict[tuple[str, str], dict] = {}

    for run in runs:
        agent = run.agent or ""
        row = by_agent.setdefault(agent, {
            "agent": agent, "run_ids": [], "runs": 0,
            "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
            "cost_usd_billed": 0.0, "cost_usd_estimated": 0.0,
            "unpriced": False, "unpriced_models": [],
        })
        row["run_ids"].append(run.id)
        row["runs"] += 1

        entry = billed.get(run.id)
        if entry is not None:
            row["cost_usd_billed"] += entry["cost_usd"]
            total["cost_usd_billed"] += entry["cost_usd"]
            if not entry["priced"]:
                # The price gap sits on the BILLING side: this amount came about without a
                # catalog entry and is therefore not solid. That is why the interface shows a
                # "≥" here instead of a sum that pretends to be exact.
                row["unpriced"] = True
                for label in entry["unpriced_models"]:
                    if label not in row["unpriced_models"]:
                        row["unpriced_models"].append(label)

        for provider, model, tokens in _token_groups(run, steps):
            cost, priced = prices.price(
                provider, model, in_tokens=tokens["in_tokens"], out_tokens=tokens["out_tokens"],
                cache_read_tokens=tokens["cache_read_tokens"])
            for field in ("in_tokens", "out_tokens", "cache_read_tokens"):
                row[field] += tokens[field]
                total[field] += tokens[field]
            row["cost_usd_estimated"] += cost
            total["cost_usd_estimated"] += cost
            model_row = by_model.setdefault((provider, model), {
                "provider": provider, "model": model, "in_tokens": 0, "out_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0.0, "unpriced": not priced,
            })
            for field in ("in_tokens", "out_tokens", "cache_read_tokens"):
                model_row[field] += tokens[field]
            model_row["cost_usd"] += cost

    for row in (*by_agent.values(), total):
        for field in ("cost_usd_billed", "cost_usd_estimated"):
            row[field] = round(row[field], 6)
    for model_row in by_model.values():
        model_row["cost_usd"] = round(model_row["cost_usd"], 6)

    return {
        # Exactly the statement "at least": some billed entry had no price behind it. A run
        # still going without cost entries is NOT partial, it simply has not billed yet, and
        # that is not an error.
        "cost_partial": any(row["unpriced"] for row in by_agent.values()),
        "total": total,
        "by_agent": sorted(by_agent.values(), key=lambda r: r["agent"]),
        "by_model": sorted(by_model.values(), key=lambda r: (r["provider"], r["model"])),
    }


# ── Personalakte (Kennzahlen je Rolle) ──────────────────────────────────────

def _duration_ms_expr(db: AsyncSession):
    """`finished_at - started_at` in milliseconds, dialect dependent because there is no
    common expression for it.

    Postgres can do `extract(epoch from a - b)`, SQLite knows neither intervals nor
    `extract`; there `julianday()` counts in days (floating point, about 0.1 ms accurate). A
    comparison `finished_at < started_at + INTERVAL` is out: SQLAlchemy cannot map date
    arithmetic onto SQLite (`TypeError: fromisoformat`). So exactly one branch, in exactly one
    place, and the rest of the evaluation is dialect free.
    """
    if db.get_bind().dialect.name == "sqlite":
        return (func.julianday(Run.finished_at) - func.julianday(Run.started_at)) * 86_400_000.0
    return func.extract("epoch", Run.finished_at - Run.started_at) * 1000.0


def _bucket_expr(duration_ms):
    """Ladder index of a run as a `CASE WHEN` chain (0 … len(LADDER))."""
    return case(*[(duration_ms < edge, i) for i, edge in enumerate(DURATION_LADDER_MS)],
                else_=len(DURATION_LADDER_MS))


def _percentile_ms(counts: list[int], q: float, max_ms: int | None) -> int | None:
    """A percentile from bucket counts, as the **upper bound** of the bucket it falls into.

    Bucket counts do not allow more to be read out. Interpolating inside the bucket would
    invent accuracy the numbers do not have; the upper bound on the other hand is a true
    statement ("the median is below X"). `max_ms` clamps it further: with a single 30 s run
    the median is not "below 45 s" but exactly 30 s.
    The overflow bucket (beyond the ladder) has no upper bound, hence `None`; how long it
    really was stands in `max_ms`.
    """
    total = sum(counts)
    if total <= 0:
        return None
    rank = max(1, math.ceil(q * total))          # the next rank, not interpolation
    seen = 0
    for i, n in enumerate(counts):
        seen += n
        if seen >= rank:
            if i >= len(DURATION_LADDER_MS):
                return None
            edge = DURATION_LADDER_MS[i]
            return min(edge, max_ms) if max_ms is not None else edge
    return None


def _display_buckets(counts: list[int]) -> list[dict]:
    """The five display buckets as sums of ladder buckets. `lt_ms: None` means "above"."""
    out: list[dict] = []
    below = 0
    for edge in DURATION_DISPLAY_EDGES_MS:
        to = sum(n for i, n in enumerate(counts)
                  if i < len(DURATION_LADDER_MS) and DURATION_LADDER_MS[i] <= edge)
        out.append({"lt_ms": edge, "n": to - below})
        below = to
    out.append({"lt_ms": None, "n": sum(counts) - below})
    return out


def _agent_slot(agents: dict[str, dict], name: str) -> dict:
    """The row of a role, created as soon as it shows up anywhere for the first time."""
    return agents.setdefault(name, {
        "agent": name, "runs": 0, "running": 0, "by_status": {},
        "delivered": 0, "waiting": 0, "aborted": 0,
        "cost_usd": 0.0, "cost_partial": False,
        "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
        "iterations_avg": 0.0, "iterations_max": 0,
        "steps_avg": 0.0, "steps_max": 0,
        "duration": {"p50_ms": None, "p90_ms": None, "max_ms": None,
                     "buckets": _display_buckets([])},
        "tools": [], "last_run_at": None,
        "_iter_sum": 0, "_step_sum": 0, "_step_runs": 0,
        "_buckets": [0] * (len(DURATION_LADDER_MS) + 1),
    })


async def _agents_payload(
    db: AsyncSession, *,
    scope_runs: Callable, scope_costs: Callable,
    since_hours: int, agent: str | None, tool_limit: int,
) -> dict:
    """The shared body of both personnel files: five grouped queries, none per role.

    The authorisation sits in `scope_runs`/`scope_costs`: both receive a `select` and hang
    the JOIN and WHERE of their view onto it. That makes the global and the project file
    **the same** computation, and there is no second place where "may see" is defined.

    **Cost is grouped by `cost_entries.agent`, not by `runs.agent`.** The two columns are
    identical today but not coupled by a foreign key, and that is exactly what the column is
    for: `cost_entries.run_id` is `SET NULL`, so a cost entry survives the deletion of its run
    by the retention. Computed over `runs.agent` the bill would vanish with the run, and the
    file would claim nothing had been spent. A role can therefore stand in the list with
    `runs: 0` and cost > 0; that is not a glitch but the fact.
    """
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    tool_limit = _clamp(tool_limit, 1, TOOL_LIMIT_MAX)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours)

    def _runs(stmt):
        stmt = scope_runs(stmt).where(Run.started_at >= cutoff)
        return stmt.where(Run.agent == agent) if agent else stmt

    def _costs(stmt):
        stmt = scope_costs(stmt).where(CostEntry.created_at >= cutoff)
        return stmt.where(CostEntry.agent == agent) if agent else stmt

    agents: dict[str, dict] = {}

    # (1) Runs per (role, status). Grouping by status instead of fixed counters also records
    # a status this code does not know yet: `by_status` is the raw truth, the three bars are
    # the reading of it.
    for name, status, n, it_sum, it_max, last in (await db.execute(
        _runs(select(Run.agent, Run.status, func.count(),
                     func.sum(Run.iterations), func.max(Run.iterations),
                     func.max(Run.started_at)))
        .group_by(Run.agent, Run.status)
    )).all():
        row = _agent_slot(agents, name or "")
        status = status or ""
        row["runs"] += int(n or 0)
        row["by_status"][status] = row["by_status"].get(status, 0) + int(n or 0)
        row["_iter_sum"] += int(it_sum or 0)
        row["iterations_max"] = max(row["iterations_max"], int(it_max or 0))
        if status == "running":
            row["running"] += int(n or 0)
        if status in DELIVERED_STATUS:
            row["delivered"] += int(n or 0)
        elif status in WAITING_STATUS:
            row["waiting"] += int(n or 0)
        elif status in ABORTED_STATUS:
            row["aborted"] += int(n or 0)
        iso = _iso(last)
        if iso and (row["last_run_at"] is None or iso > row["last_run_at"]):
            row["last_run_at"] = iso

    # (2) Duration buckets. Finished runs only: a running run has no duration yet, and
    # reporting the time elapsed so far as a duration would be a number that changes on the
    # next fetch without anything having happened.
    duration = _duration_ms_expr(db)
    bucket = _bucket_expr(duration)
    for name, idx, n, max_ms in (await db.execute(
        _runs(select(Run.agent, bucket, func.count(), func.max(duration)))
        .where(Run.finished_at.is_not(None))
        .group_by(Run.agent, bucket)
    )).all():
        row = _agent_slot(agents, name or "")
        row["_buckets"][int(idx)] += int(n or 0)
        measured = int(round(float(max_ms or 0.0)))
        d = row["duration"]
        d["max_ms"] = measured if d["max_ms"] is None else max(d["max_ms"], measured)

    # (3) Steps per run. `iterations` (rounds of the agent) and steps (rows in `run_steps`)
    # are two different things: the inspector already labels `iterations` as rounds, and one
    # shared field would have made both numbers unreadable. The denominator of the average is
    # the runs WITH steps: a run whose steps the retention has already deleted did not have
    # "0 steps".
    per_run = _runs(
        select(Run.agent.label("agent"), RunStep.run_id.label("rid"), func.count().label("n"))
        .select_from(RunStep).join(Run, Run.id == RunStep.run_id)
    ).group_by(Run.agent, RunStep.run_id).subquery()
    for name, s_sum, s_max, s_runs in (await db.execute(
        select(per_run.c.agent, func.sum(per_run.c.n), func.max(per_run.c.n), func.count())
        .group_by(per_run.c.agent)
    )).all():
        row = _agent_slot(agents, name or "")
        row["_step_sum"] += int(s_sum or 0)
        row["_step_runs"] += int(s_runs or 0)
        row["steps_max"] = max(row["steps_max"], int(s_max or 0))

    # (4) Cost and tokens from the cost entries. Tokens come from the same source as the
    # amount so that both tell the same story ("what was billed"): `runs.input_tokens` does
    # not know cached tokens at all.
    for name, usd, ein, aus, cache, offen in (await db.execute(
        _costs(select(CostEntry.agent, func.sum(CostEntry.cost_usd),
                      func.sum(CostEntry.input_tokens), func.sum(CostEntry.output_tokens),
                      func.sum(CostEntry.cache_read_tokens),
                      func.sum(case((CostEntry.priced.is_(True), 0), else_=1))))
        .group_by(CostEntry.agent)
    )).all():
        row = _agent_slot(agents, name or "")
        row["cost_usd"] += float(usd or 0.0)
        row["in_tokens"] += int(ein or 0)
        row["out_tokens"] += int(aus or 0)
        row["cache_read_tokens"] += int(cache or 0)
        # `priced` is three valued; only proven priced counts as complete here. NULL (an old
        # row, today ALL 411 entries) means "never recorded whether a catalog entry existed".
        # `_entry_priced` resolves NULL for a SINGLE run against today's catalog; across
        # months and several providers the same back calculation would be a claim. The file
        # therefore simply says: lower bound.
        if int(offen or 0) > 0:
            row["cost_partial"] = True

    # (5) Tool table: `run_steps ⋈ runs` over `runs.agent`, because the step itself does not
    # know which role triggered it. `ix_runs_agent_started` covers that.
    # `ok` is three valued, so `ok + failed ≤ n` holds and **not** `ok + failed = n`: the rest
    # are rows where nobody looked (in this instance that is the majority, `fs_read` has 1531
    # calls and 0 proven verdicts). Computing a rate `ok/n` from that would paint half the
    # table red for no reason, because the difference is "unknown", not "failed".
    tools: dict[str, list[dict]] = {}
    for name, tool, n, ok, bad in (await db.execute(
        _runs(select(Run.agent, RunStep.tool_name, func.count(),
                     func.sum(case((RunStep.ok.is_(True), 1), else_=0)),
                     func.sum(case((RunStep.ok.is_(False), 1), else_=0)))
              .select_from(RunStep).join(Run, Run.id == RunStep.run_id))
        .where(RunStep.tool_name.is_not(None), RunStep.tool_name != "")
        .group_by(Run.agent, RunStep.tool_name)
    )).all():
        _agent_slot(agents, name or "")
        tools.setdefault(name or "", []).append(
            {"tool": tool, "n": int(n or 0), "ok": int(ok or 0), "failed": int(bad or 0)})

    for name, row in agents.items():
        row["iterations_avg"] = round(row["_iter_sum"] / row["runs"], 1) if row["runs"] else 0.0
        row["steps_avg"] = (round(row["_step_sum"] / row["_step_runs"], 1)
                            if row["_step_runs"] else 0.0)
        d = row["duration"]
        d["buckets"] = _display_buckets(row["_buckets"])
        d["p50_ms"] = _percentile_ms(row["_buckets"], 0.5, d["max_ms"])
        d["p90_ms"] = _percentile_ms(row["_buckets"], 0.9, d["max_ms"])
        row["cost_usd"] = round(row["cost_usd"], 6)
        row["tools"] = sorted(tools.get(name, []),
                              key=lambda t: (-t["n"], t["tool"]))[:tool_limit]
        for hilf in ("_iter_sum", "_step_sum", "_step_runs", "_buckets"):
            row.pop(hilf)

    return {
        # The window belongs in the answer: `run_retention_days` deletes older runs, so the
        # view must not say "favourite tools" but only "of the last N".
        "since_hours": since_hours,
        "tool_limit": tool_limit,
        "agents": sorted(agents.values(),
                         key=lambda r: (-r["runs"], -r["cost_usd"], r["agent"])),
    }


@router.get("/projects/{project_id}/office/agents")
async def project_agents(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    since_hours: int = SINCE_HOURS_DEFAULT,
    agent: str | None = None,
    tool_limit: int = TOOL_LIMIT_DEFAULT,
):
    """The personnel file of one project, the fourth dock tab in the project office.

    A foreign project is 404, which `get_project_access` takes care of (`build_access` raises
    404, never 403). A viewer is enough: the file is a reading surface over runs they can
    already see, and `/costs/global` (which carries `require_admin`) is explicitly NOT the
    template, otherwise every viewer's office would hold an empty tab.
    """
    pid = access.project.id

    def scope_runs(stmt):
        # Like `project_sessions`: preferably over `Run.project_id` (it survives the deletion
        # of a ticket), old rows without a `project_id` still through the ticket.
        return stmt.outerjoin(Issue, Issue.id == Run.issue_id).where(
            or_(Run.project_id == pid,
                and_(Run.project_id.is_(None), Issue.project_id == pid)))

    def scope_costs(stmt):
        return stmt.select_from(CostEntry).outerjoin(
            Issue, Issue.id == CostEntry.issue_id).where(
            or_(CostEntry.project_id == pid,
                and_(CostEntry.project_id.is_(None), Issue.project_id == pid)))

    return await _agents_payload(db, scope_runs=scope_runs, scope_costs=scope_costs,
                                 since_hours=since_hours, agent=agent, tool_limit=tool_limit)


@router.get("/office/agents")
async def global_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    since_hours: int = SINCE_HOURS_DEFAULT,
    agent: str | None = None,
    tool_limit: int = TOOL_LIMIT_DEFAULT,
):
    """The personnel file across all visible projects, the tab on `/buero`.

    Visibility comes from `_visible_runs`, so from the same definition as the session list and
    the live socket. A non member gets no 403 but an empty list: there is no path whose
    existence could be betrayed here.
    """
    visible = await _visible_runs(db, user)

    def scope_runs(stmt):
        return stmt.where(visible)

    if user.global_role == GlobalRole.admin:
        cost_cond = true()
    else:
        # Cost entries carry no `owner_id`. Project bound ones cover the project set;
        # projectless ones (assistant, job) hang on the run, hence the outer join. A
        # projectless entry whose run is already deleted stays invisible to non admins:
        # better a gap than somebody else's bill.
        allowed = await compute_acl(db, user)
        cost_cond = or_(
            CostEntry.project_id.in_(allowed),
            and_(CostEntry.project_id.is_(None), Run.owner_id == user.id),
        )

    def scope_costs(stmt):
        return stmt.select_from(CostEntry).outerjoin(
            Run, Run.id == CostEntry.run_id).where(cost_cond)

    return await _agents_payload(db, scope_runs=scope_runs, scope_costs=scope_costs,
                                 since_hours=since_hours, agent=agent, tool_limit=tool_limit)
