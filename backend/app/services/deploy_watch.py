"""The stage watcher for deployments: status changes become step rows.

The `deployments` table is written by the `deployer` sidecar, which knows nothing of
Traccoon's event stream. For a deploy to become visible in the office it needs a **real
`run_steps` row**: `seq = id * 4 + slot` is the arrival order, and whoever wants into the
stream has to go through a row. That way everything else follows automatically:
monotonicity, slot arithmetic, snapshot, reconnect protocol. A `seq` space of its own for
deployments was out (`Recorder.push` deduplicates **exclusively** over `seq`, so colliding
numbers would mean immediate data loss), a second Redis channel likewise (it would double
the WS bridge and the snapshot pagination including a truncation rule of its own), and a
synthetic `Run` per deployment would have polluted `runs`, cost views and roster with
token-less ghost runs.

**A loop of its own with a 3 s beat**, not the operations tick: that one runs every 30 s and
is therefore longer than an average deploy (12.5 s). The `building` state would regularly be
over before we look, and the stage would only get the verdict. Where that happens anyway (a
deploy running through completely between two beats), the opening is caught up:
`states_for` then delivers `start` and the verdict together. A verdict without an opening
would be a rack lighting up without anybody ever having walked over.

**Idempotency over a column, not over process memory.** `announced_status` says what has
already been told; what is read is `WHERE status <> announced_status`. Restart proof,
duplicate free, and "what has already been told" is a fact in the database instead of an
assumption in RAM.

**Which run the row is hung off**: a deployment has no actor of its own (see above), it
*belongs* to the run that triggered it:

    agent tool         `worktree <> ''`   the run whose `deploy` call is waiting right now:
                                          over `issue_id` the most recent `running` run,
                                          otherwise the most recent one at all
    merge/workflow     `issue_id` set     the most recent run of this ticket
    maintenance update `self_deploy`,     **no anchor, so no stage event**
                       no `issue_id`

The third case deliberately gets nothing, and that is not a gap: a self-deploy recreates
the backend container that supplies the stage. The WebSocket falls in the middle of the
animation, and the process that would draw it dies of it. Animating a process that kills
the animator is a category error. These rows live in the list (`api/deployments.py`), not
in the room, and a test nails the decision down so that nobody "repairs" it.

**Only the most recent window.** The existing rows have `announced_status = ''` and would
otherwise all be "new": the first beat after the rollout would tell of 186 deployments from
three months as if they had just happened. A deploy older than `ANNOUNCE_WINDOW_HOURS` is
no longer news; the history is shown by the read path
(`services/office.deployment_events`) with a borrowed `seq` in its proper place.
A row that has already been told about (`announced_status <> ''`) stays in view
regardless: a started story is told to the end, even after a long outage.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models.agents import Run, RunStep
from ..models.ops import Deployment
from .office import (
    DEPLOY_LOG_HEAD_CHARS, RunCtx, add_step, deploy_content, deploy_state, deploy_target,
    publish_step,
)

log = logging.getLogger("deploy_watch")

# Shorter than an average deploy (12.5 s), otherwise the room never sees the opening.
TICK_SECONDS = 3
# From when on a status change is no longer news (see the module docstring).
ANNOUNCE_WINDOW_HOURS = 6

# Statuses after which nothing more comes.
TERMINAL_STATUS = ("ok", "failed", "rolledback")
# Status that has already told the opening.
STARTED_STATUS = ("building",)

# The value of the `ok` column on the step per state. Three valued as everywhere in the
# house: at the opening nobody knows anything, and a guessed True would paint the view green.
_OK_BY_STATE: dict[str, bool | None] = {"start": None, "ok": True, "fail": False, "back": False}


def states_for(announced: str, status: str) -> list[str]:
    """Which states this transition tells, the whole idempotency logic, pure.

    Empty when the status has no business in the room (`pending`, `cancelled`). Otherwise
    the new state, preceded by the opening if that has never been told because the deploy
    ran through completely between two beats.
    """
    state = deploy_state(status)
    if not state:
        return []
    if state == "start" or announced in STARTED_STATUS or announced in TERMINAL_STATUS:
        return [state]
    return ["start", state]


async def _anchor_run(db: AsyncSession, dep: Deployment) -> Run | None:
    """The run whose story the deployment hangs off (or None, see the docstring).

    Both anchor cases go over `issue_id`: the deploy call of the agent knows its ticket, and
    `ix_runs_issue_started` covers the query. Without a ticket there is no run the process
    could belong to, and then the room stays silent.
    """
    if not dep.issue_id:
        return None
    if (dep.worktree or "").strip():
        # Agent tool: `_do_deploy` waits inline for the result, so the triggering run is
        # still `running`, and exactly that one should walk to the rack. The fallback to the
        # most recent run takes hold when it has died at the timeout meanwhile.
        running = (await db.execute(
            select(Run).where(Run.issue_id == dep.issue_id, Run.status == "running")
            .order_by(Run.id.desc()).limit(1))).scalars().first()
        if running is not None:
            return running
    return (await db.execute(
        select(Run).where(Run.issue_id == dep.issue_id)
        .order_by(Run.id.desc()).limit(1))).scalars().first()


async def announce(db: AsyncSession, dep: Deployment) -> list[RunStep]:
    """Tell a status change: write step rows, acknowledge, send.

    Acknowledging happens ALWAYS, even when there is no anchor (maintenance update) or the
    status has nothing to show. Otherwise the same row would lie on the table again on every
    beat and the watcher would run in circles forever.
    """
    before = dep.announced_status or ""
    states = states_for(before, dep.status or "")
    steps: list[RunStep] = []
    ctx: RunCtx | None = None

    if states:
        run = await _anchor_run(db, dep)
        if run is not None:
            ctx = RunCtx.from_run(run)
            # `RunStep.seq` is the running counter OF THE RUN; the watcher writes into a
            # foreign run and has to tie in there instead of starting at 1.
            ctx.seq = int((await db.execute(
                select(func.max(RunStep.seq)).where(RunStep.run_id == run.id))).scalar() or 0)
            for state in states:
                steps.append(await add_step(
                    db, ctx, role="system", kind="deploy", target=deploy_target(dep),
                    ok=_OK_BY_STATE.get(state),
                    # At the opening the log is still empty respectively belongs to an
                    # earlier attempt; sending it along would anticipate a result.
                    content=deploy_content(dep.id, state,
                                           "" if state == "start"
                                           else (dep.log or "")[:DEPLOY_LOG_HEAD_CHARS]),
                    commit=False))

    # Free connection: the trigger name has been in `BUILTIN_EVENTS` all along and has never
    # fired. Here is the place where everything is together. Before the commit, so that the
    # acknowledgement and the started flows lie in ONE transaction: a crash in between would
    # otherwise lose the event while the acknowledgement stood.
    if dep.status in TERMINAL_STATUS and before not in TERMINAL_STATUS:
        from .events import emit
        await emit(db, "deployment.finished", project_id=dep.project_id,
                   issue_id=dep.issue_id, source_ref=f"deployment:{dep.id}",
                   payload={"deployment": {
                       "id": dep.id, "status": dep.status, "ok": dep.status == "ok",
                       "source": dep.source or "", "stack_dir": dep.stack_dir or "",
                       "self_deploy": bool(dep.self_deploy),
                       "check_only": bool(dep.check_only)}})

    dep.announced_status = dep.status or ""
    await db.commit()
    if steps:
        log.info("Deployment %s: %s -> %s in the room of run %s",
                 dep.id, before or "—", dep.status, ctx.run_id if ctx else "—")
    # Send only after the commit: before it the row has no `id` and therefore no `seq`.
    for step in steps:
        await publish_step(ctx, step)
    return steps


async def tick(db: AsyncSession) -> int:
    """One pass. Returns the number of told rows (for test and log)."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=ANNOUNCE_WINDOW_HOURS)
    open_ones = (await db.execute(
        select(Deployment).where(
            Deployment.status != Deployment.announced_status,
            # Young rows, or those whose story has already begun. The second term closes the
            # gap when a deploy got its opening and its outcome was only settled after a long
            # backend outage: a started story is told to the end, no matter how old it is by
            # then.
            or_(Deployment.created_at >= cutoff, Deployment.announced_status != ""),
        ).order_by(Deployment.id))).scalars().all()
    tells = 0
    for dep in open_ones:
        tells += len(await announce(db, dep))
    return tells


async def run_deploy_watch() -> None:
    log.info("deploy-watch started (tick=%ss)", TICK_SECONDS)
    await asyncio.sleep(5)
    while True:
        try:
            async with SessionLocal() as db:
                await tick(db)
        except Exception:  # noqa: BLE001 - the stage is a spectator, not a participant
            log.exception("deploy-watch failed")
        await asyncio.sleep(TICK_SECONDS)
