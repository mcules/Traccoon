"""Deployments: the read API plus **the one manual write path**, the button.

Three read routes on a table that was written 186 rows long and never read, and since the
button a fourth route that writes (`POST /projects/{id}/deployments`, see
`create_deployment`). It is the fifth write site of the table and the only one with a human
behind it, which is why it carries `source="manual"`.

The pattern throughout is that of `api/office.py`: bare dicts instead of Pydantic schemas,
**404 instead of 403**, and the permission comes from the **row** (its `project_id`), not
from the path. A path carrying the project id would leave authorisation to the client.

Why there is a **global** route and not only the project bound one: the 17 maintenance
updates (`self_deploy`) hang off no ticket, and a deployment without a project
(`project_id IS NULL`, none today, one after the first `SET NULL` project deletion) would
otherwise be findable by nobody.

**Four peculiarities of the existing data shape the response** and every one of them was
measured, not guessed:

1. `requested_by` and `chat_id` are filled on **0 of 186** rows; none of the four write
   sites sets them. "Who triggered this" cannot be expressed with today's schema. That is
   what the new `source` column is for, and because nobody fills it yet the API maps
   `"" -> "unknown"` instead of guessing an origin from `self_deploy`. The two legacy
   columns appear in **no** response.
2. All 56 `failed` rows carry the **same** guard text ("Rejected: self-deploy only through
   the explicit maintenance …"). A list without `log_head` would therefore not merely be
   thin, it would be actively misleading: it would show 56 different failures where there
   is one. The full text stays with the detail endpoint (see `deployment_detail`).
3. 69 rows sit on `cancelled`, a status **no code path writes** (derivation in the
   docstring of `models/ops.Deployment`). They are shown but not canonicalised: their
   `phase` is `aborted` and their `ok` is `None`.
4. **71 of 186 rows lack a timestamp.** `wait_ms`/`duration_ms` are three valued because
   of that: a computed 0 would be an invented duration.

`ok` follows the same rule as `services/office.tool_ok`: **never a guessed result**.
Belegter Erfolg `True`, belegter Fehlschlag `False`, alles andere `None`.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.enums import GlobalRole, ProjectRole
from ..models.ops import Deployment
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from .deps import Access, build_access, get_current_user, get_project_access, require_role
# The same definition of "may see" as the office and the project list. There should be
# exactly one, not three that drift apart eventually.
from .office_ws import compute_acl

router = APIRouter(tags=["deployments"])

LIMIT_DEFAULT = 50
LIMIT_MAX = 200

# Considerably longer than the office (a week there): a deployment is **archive**, not a
# session. The question "when did this last go through" is still the same question months
# later, while an agent run falls to retention after 30 days anyway.
SINCE_HOURS_DEFAULT = 24 * 30
SINCE_HOURS_MAX = 24 * 365

LOG_HEAD_CHARS = 240

# The status sets from which `phase`, `ok` and the filter are derived. `status` itself goes
# through the response **raw**: the view should be able to tell `pending-check` from
# `pending` even though both share a phase.
OPEN_STATUS = ("pending", "pending-check", "building")
FAILED_STATUS = ("failed", "rolledback")

STATUS_FILTER = ("all", "running", "ok", "failed", "other")


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
    """ISO-8601 with an explicit zone. A bare `datetime` would be read as local time in the
    browser, and the same row would be hours off depending on the visitor."""
    aware = _aware(value)
    return None if aware is None else aware.astimezone(dt.timezone.utc).isoformat()


def _span_ms(start: dt.datetime | None, end: dt.datetime | None) -> int | None:
    """Milliseconds between two timestamps, or `None` when one of them is missing.

    Falling back to 0 would be convenient and wrong: 71 of the 186 existing rows have no
    `finished_at`, 58 no `started_at`. A 0 there would claim a deploy that took no time
    instead of one whose time nobody wrote down.
    """
    a, b = _aware(start), _aware(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _phase(status: str) -> str:
    """`queued|running|done|aborted`, the rough situation, for colour and sorting.

    Anything unknown lands on `aborted` and not on `done`: a status this file does not know
    is not a finished deploy but one about which nothing is known. `cancelled` falls here
    for exactly that reason, being the only representative occurring today (see the module
    docstring, point 3).
    """
    if status in ("pending", "pending-check"):
        return "queued"
    if status == "building":
        return "running"
    if status == "ok" or status in FAILED_STATUS:
        return "done"
    return "aborted"


def _ok(status: str) -> bool | None:
    """Three valued, following the house rule from `services/office.tool_ok`: **never a
    guessed result**. `True` only on proven success, `False` only on proven failure, `None`
    otherwise. Open (still running) and aborted (nobody knows) are both "unknown" but for
    different reasons; the `phase` separates them.
    """
    if status == "ok":
        return True
    if status in FAILED_STATUS:
        return False
    return None


def _kind(self_deploy: bool, check_only: bool) -> str:
    """`self|check|stack`. The order carries meaning: a self-deploy is never a mere check,
    and `check_only` alone says nothing about whose stack is meant."""
    if self_deploy:
        return "self"
    if check_only:
        return "check"
    return "stack"


def _not_found() -> HTTPException:
    """A single wording for "does not exist" and "is not yours". Two distinguishable answers
    would be a directory of foreign projects."""
    return HTTPException(404, "Deployment not found")


# ── Autorisierung ───────────────────────────────────────────────────────────

async def _visible_deployments(db: AsyncSession, user: User):
    """SQL condition: which deployments may this user see at all?

    The model is `api/office.py::_visible_runs`: admin without a filter, otherwise the
    permitted projects. One difference is deliberate: **`project_id IS NULL` stays reserved
    for admins.** A run has an `owner_id` by which a project-less run can be attributed to
    its human; a deployment has **no** such field (`requested_by` is filled on 0 of 186
    rows). An ownerless deployment therefore belongs to nobody, and `IN (…)` yields NULL
    for NULL anyway, so no match.
    """
    if user.global_role == GlobalRole.admin:
        return true()
    allowed = await compute_acl(db, user)
    return Deployment.project_id.in_(allowed)


async def _authorize_row(db: AsyncSession, user: User, project_id: int | None) -> None:
    """May the user read this one row? Otherwise 404, in **both** cases.

    The project id comes from the loaded row, not from the path; `/deployments/{id}` carries
    none. That "foreign project" and "does not exist" answer indistinguishably is the point:
    otherwise the route would be a counter of foreign deployments.
    """
    if project_id is None:
        # No project means no owner on which visibility could be anchored.
        if user.global_role != GlobalRole.admin:
            raise _not_found()
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise _not_found()
    try:
        access = await build_access(project, user, db)
    except HTTPException:
        raise _not_found() from None
    if not access.has_role(ProjectRole.viewer):
        raise _not_found()


# ── Zeilenform + Abfrage ────────────────────────────────────────────────────

def _status_condition(status: str):
    """The filter behind `?status=`. `all` returns `None` (no AND).

    `running` means "not decided yet" and therefore includes the queue: whoever wants to
    know whether something is under way right now does not care whether the sidecar has
    already picked the row up. `other` is the rest, today exactly the 69 `cancelled`,
    tomorrow every status this file does not know yet.
    """
    if status == "all":
        return None
    if status == "running":
        return Deployment.status.in_(OPEN_STATUS)
    if status == "ok":
        return Deployment.status == "ok"
    if status == "failed":
        return Deployment.status.in_(FAILED_STATUS)
    return Deployment.status.notin_((*OPEN_STATUS, "ok", *FAILED_STATUS))


def _row(rec) -> dict:
    """One row of the list. `status` is in there **raw**, nothing is glossed over;
    `phase`/`ok` are derivations beside it, not in its place."""
    (dep_id, project_id, project_key, issue_id, issue_key, status, source, self_deploy,
     check_only, stack_dir, created_at, started_at, finished_at, log_len, log_head) = rec
    status = status or ""
    return {
        "id": dep_id,
        "project_id": project_id,
        "project_key": project_key or "",
        "issue_id": issue_id,
        "issue_key": issue_key or "",
        "status": status,
        "phase": _phase(status),
        "ok": _ok(status),
        # Empty means "not in the row", not "was nobody". The backfill would be right today
        # (`self_deploy → maintenance`) and would become wrong the moment `merge` or
        # `workflow` fires for the first time: a guessed origin in a history view is worse
        # than an honest empty field.
        "source": source or "unbekannt",
        "kind": _kind(bool(self_deploy), bool(check_only)),
        "stack_dir": stack_dir or "",
        "created_at": _iso(created_at),
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        # Two separate times, because they name two separate problems: `wait_ms` is the
        # queue (the sidecar polls every 3 s), `duration_ms` the actual work. Added
        # together, a 3 s wait would look like a slow build.
        "wait_ms": _span_ms(created_at, started_at),
        "duration_ms": _span_ms(started_at, finished_at),
        "log_bytes": int(log_len or 0),
        "log_head": log_head or "",
    }


def _select_rows():
    """The column list of the list, **without** the full log text.

    `octet_length`/`substr` compute in the database so that fetching 200 rows does not pull
    200 complete build logs over the wire. Both functions exist under Postgres as well as
    SQLite (from 3.43; the backend image ships 3.46).
    """
    return (
        select(
            Deployment.id, Deployment.project_id, Project.key,
            Deployment.issue_id, Issue.key,
            Deployment.status, Deployment.source,
            Deployment.self_deploy, Deployment.check_only, Deployment.stack_dir,
            Deployment.created_at, Deployment.started_at, Deployment.finished_at,
            func.octet_length(Deployment.log),
            func.substr(Deployment.log, 1, LOG_HEAD_CHARS),
        )
        .outerjoin(Project, Project.id == Deployment.project_id)
        .outerjoin(Issue, Issue.id == Deployment.issue_id)
    )


async def _payload(db: AsyncSession, *, where, limit: int, since_hours: int,
                   status: str) -> dict:
    """The shared body of both lists: project card and global page show the same shape
    because they go through the same function.

    `where` carries the **visibility plus every narrowing except the status**. That is
    deliberate: `by_status` is counted against exactly this `where` and thereby answers the
    question "what is lying around in this window at all", while the list is already
    filtered. The other way round the count would be a tautology (`{"ok": 50}` on
    `?status=ok`) and the 69 aborted ones would stay unexplained; mixing them *into* the
    list, on the other hand, would poison it.
    """
    if status not in STATUS_FILTER:
        raise Error(400, "err.status_one_of",
                     "status has to be one of {allowed}", allowed=', '.join(STATUS_FILTER))
    limit = _clamp(limit, 1, LIMIT_MAX)
    since_hours = _clamp(since_hours, 1, SINCE_HOURS_MAX)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours)
    # `created_at` and not `finished_at`: a row without an end (69 of 186) would otherwise
    # fall out of every window and be reachable through no route at all.
    window = and_(where, Deployment.created_at >= cutoff)

    cond = _status_condition(status)
    listed = window if cond is None else and_(window, cond)
    # `limit + 1` reveals the truncation without a second COUNT.
    rows = (await db.execute(
        _select_rows().where(listed).order_by(Deployment.id.desc()).limit(limit + 1)
    )).all()
    truncated = len(rows) > limit
    items = [_row(r) for r in rows[:limit]]

    counts = (await db.execute(
        select(Deployment.status, func.count()).where(window).group_by(Deployment.status)
    )).all()
    by_status = {(s or ""): int(n or 0)
                 for s, n in sorted(counts, key=lambda r: (-int(r[1] or 0), r[0] or ""))}
    return {"items": items, "count": len(items), "truncated": truncated,
            "by_status": by_status}


# ── Routen ──────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/deployments")
async def project_deployments(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    limit: int = LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    issue_id: int | None = None,
):
    """The deployments of a project: card on the dashboard, full list in the settings.

    Foreign project = 404, handled by `get_project_access`. **Viewer is enough**: whoever
    merged wants to know whether it is out there, and is not necessarily a
    `maintainer`.
    """
    where = Deployment.project_id == access.project.id
    if issue_id is not None:
        where = and_(where, Deployment.issue_id == issue_id)
    return await _payload(db, where=where, limit=limit, since_hours=since_hours,
                          status=status)


@router.get("/deployments")
async def global_deployments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    limit: int = LIMIT_DEFAULT,
    since_hours: int = SINCE_HOURS_DEFAULT,
    status: str = "all",
    project_id: int | None = None,
):
    """All deployments this user may see.

    `project_id` **narrows** the already permitted set and never authorises: entering a
    foreign project yields an empty list, no access and no 403 (which would be a proof of
    existence). The filter therefore stands as an additional AND beside the visibility
    condition, not in its place.
    """
    where = await _visible_deployments(db, user)
    if project_id is not None:
        where = and_(where, Deployment.project_id == project_id)
    return await _payload(db, where=where, limit=limit, since_hours=since_hours,
                          status=status)


@router.get("/deployments/{dep_id}")
async def deployment_detail(
    dep_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """One row with the **complete log**, the only endpoint that delivers it.

    The full text belongs exactly here: in the list, 56 identical guard messages would be
    noise, and an `ok` log is around 1 kB today, so with 200 rows the list would be twenty
    times as large for no reason. Whoever wants to see the cause clicks the row; `log_head`
    decides whether that is worth it.
    """
    dep = await db.get(Deployment, dep_id)
    if dep is None:
        raise _not_found()
    await _authorize_row(db, user, dep.project_id)

    project_key = ""
    if dep.project_id:
        project = await db.get(Project, dep.project_id)
        project_key = project.key if project else ""
    issue_key = ""
    if dep.issue_id:
        issue = await db.get(Issue, dep.issue_id)
        issue_key = issue.key if issue else ""

    log = dep.log or ""
    row = _row((
        dep.id, dep.project_id, project_key, dep.issue_id, issue_key, dep.status,
        dep.source, dep.self_deploy, dep.check_only, dep.stack_dir,
        dep.created_at, dep.started_at, dep.finished_at,
        len(log.encode("utf-8")), log[:LOG_HEAD_CHARS],
    ))
    row["log"] = log
    return row


# ── The button ──────────────────────────────────────────────────────────────

class DeployIn(BaseModel):
    """Body of the button, today exactly one field, and that one optional.

    Deliberately **no** `stack_dir`: the target comes from `project.workspace_dir` and must
    never come from the client. A path from the body would be a `docker compose` on an
    arbitrary host directory, triggered over an HTTP route. Just as deliberately no
    `self_deploy` and no `check_only`: the host stack is recreated exclusively through the
    idle-gated maintenance update (see `services/dispatcher.py`), never through a button in
    a project.
    """

    issue_id: int | None = None


@router.post("/projects/{project_id}/deployments")
async def create_deployment(
    data: DeployIn | None = None,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Queue a deployment by hand, the button under Settings → Deployment.

    The reason for the route: deployments used to come into being only automatically
    (`merge` after acceptance with `auto_deploy`, `workflow`, `agent`, `maintenance`). For a
    project with real users `auto_deploy` is deliberately off, because there is **no
    rollback** in the generic path, unlike with the self-deploy. Whoever rolls out there
    wants to pick the moment; that and nothing more is what this route does: it writes a
    `pending` row that the sidecar (`deployer/deploy_watch.py`) picks up on its next poll.

    **`maintainer`, not `viewer`** (and therefore 403 for a member, 404 for a stranger, both
    handled by `require_role`): the read side is deliberately open to every member ("is my
    merge out there?"), triggering is not. It rebuilds and restarts a running stack.

    Four rules, each with an incident or a data race behind it:

    1. **Empty `workspace_dir` → 400.** An empty stack directory aims at the host and
       maintenance project itself. The deployer rejects that anyway ("implicit host deploy
       … is locked"), but the row would come into being regardless, and in the auto-deploy
       path exactly that was a deploy storm once (ABC-19, commented in
       `worker/__main__.py`). A button that is guaranteed to fail is not a button.
    2. **At most one open deploy per project → 409.** Two `docker compose up` in the same
       directory are a data race over containers and networks, not a second deploy. The
       sidecar works off one row at a time anyway; without this lock the second one would
       merely queue and run unnoticed afterwards.
    3. **`issue_id` must belong to the project**, otherwise 404 in the same wording as
       everywhere here. Attaching a foreign ticket would be a row whose `issue_key` points
       the frontend at a project where it has no business.
    4. **`source="manual"`**, the fifth value of the column. Not `agent` and not `merge`:
       the history view should be able to tell the human from the automation, and that is
       the only reason the column exists.

    The response is the **row shape of the list** (`_row`) so that the frontend can sort it
    in without a second fetch. A log does not exist at this point, naturally.
    """
    project = access.project
    stack_dir = (project.workspace_dir or "").strip()
    if not stack_dir:
        raise Error(400, "err.no_stack_directory",
                     "This project has no stack directory (Settings -> Git -> working "
                     "directory). Without a target the deploy would point at the Traccoon stack "
                     "itself, and only the maintenance update builds that, never a project.")

    issue_id = data.issue_id if data else None
    if issue_id is not None:
        issue = await db.get(Issue, issue_id)
        if issue is None or issue.project_id != project.id:
            raise Error(404, "err.ticket_not_found", "Ticket not found")

    # Only against the **open** statuses, not against "last built": a failed deploy from
    # earlier must not block a new attempt.
    running = (await db.execute(
        select(Deployment.id)
        .where(and_(Deployment.project_id == project.id,
                    Deployment.status.in_(OPEN_STATUS)))
        .order_by(Deployment.id.desc()).limit(1)
    )).scalar_one_or_none()
    if running is not None:
        raise Error(409, "err.deployment_already_running",
                     "A deployment is already running for this project (#{id}). Wait until it is "
                     "through, two simultaneous builds in the same stack directory get in each "
                     "other's way.", id=running)

    # `self_deploy`/`check_only`/`worktree` stay on their defaults (False/False/""): what
    # gets built is the project's stack from its own directory, nothing else.
    dep = Deployment(project_id=project.id, issue_id=issue_id, stack_dir=stack_dir,
                     status="pending", source="manual")
    db.add(dep)
    await db.commit()
    await db.refresh(dep)

    issue_key = ""
    if dep.issue_id:
        issue = await db.get(Issue, dep.issue_id)
        issue_key = issue.key if issue else ""
    return _row((
        dep.id, dep.project_id, project.key, dep.issue_id, issue_key, dep.status,
        dep.source, dep.self_deploy, dep.check_only, dep.stack_dir,
        dep.created_at, dep.started_at, dep.finished_at, 0, "",
    ))
