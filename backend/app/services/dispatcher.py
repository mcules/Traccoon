"""Board mirror and operations tick.

What used to stand here, the complete two phase flow planning → plan_review → approved →
execution → to_test → done including continuation, splitting and gate check, is now a
designable process (slot `ticket_lifecycle`, see `services/workflow_seed.py`). What remains
are the two things that should not be a process:

* `sync_board_status`: mirrors the agent status onto the board column,
* the operations tick: maintenance update (no agent running means self-deploy) and clean-up
  after a restart.

The gate check before every agent run (time window, runner limit, runaway brake) sits in
`services/agent_gate.py`; advancing waiting runs is done by the engine tick.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select

from ..core.redis import get_flag, lauf_lebt, peek_result, set_flag
from ..db import SessionLocal
from ..models.agents import Run, RunStep
from ..models.enums import HoldReason, StatusCategory, TicketAgentStatus
from ..models.ticket import Issue, WorkflowStatus

log = logging.getLogger("dispatcher")

TICK_SECONDS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# Agent status to board column (by name). Error, question, approval gate and to-test become
# "waiting", active work "in progress", finished "done"; open/None leaves the column untouched.
_AGENT_STATUS_TO_BOARD = {
    TicketAgentStatus.failed: "Warten",
    TicketAgentStatus.hold: "Warten",
    TicketAgentStatus.plan_review: "Warten",
    # Test environment flow (ABC-18): a column of its own between "in progress" and "done".
    # If it is missing in the project, the fallback to "waiting" applies (see sync_board_status).
    TicketAgentStatus.to_test: "Testen",
    TicketAgentStatus.testing: "Testen",
    TicketAgentStatus.planning: "In Arbeit",
    TicketAgentStatus.approved: "In Arbeit",
    TicketAgentStatus.in_progress: "In Arbeit",
    TicketAgentStatus.done: "Fertig",
}


async def sync_board_status(db, issue: Issue) -> None:
    """Couple the board column to the agent status (moves the ticket into the matching column
    when it exists in the project). That way errors, questions and tickets to be tested land
    in "waiting" instead of staying in "to do". A ticket moved manually into a "done" column
    is never pulled back (human acceptance takes precedence)."""
    target = _AGENT_STATUS_TO_BOARD.get(issue.agent_status)
    if not target:
        return
    stats = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == issue.project_id))).scalars().all()
    cur = next((s for s in stats if s.id == issue.status_id), None)
    if cur and cur.category == StatusCategory.done and target != "Fertig":
        return  # already accepted (manually): do not pull back to "waiting"
    st = next((s for s in stats if s.name == target), None)
    if st is None and target == "Testen":
        # Existing project without a "testing" column: create it once (before "done");
        # otherwise fall back to "waiting", because the transition must never fail.
        st = await _ensure_testing_status(db, issue.project_id, stats)
    if st and issue.status_id != st.id:
        issue.status_id = st.id


async def _ensure_testing_status(db, project_id: int, stats: list[WorkflowStatus]):
    """Creates the "testing" column for an existing project (idempotent) and attaches it to the board."""
    from ..models.ticket import Board, BoardColumn
    done = next((s for s in stats if s.category == StatusCategory.done), None)
    order = (done.order if done else max([s.order for s in stats], default=0) + 1)
    st = WorkflowStatus(project_id=project_id, name="Testen",
                        category=StatusCategory.in_progress, order=order)
    db.add(st)
    if done is not None:
        done.order = order + 1
    await db.flush()
    board = (await db.execute(select(Board).where(Board.project_id == project_id))).scalars().first()
    if board is not None:
        db.add(BoardColumn(board_id=board.id, status_id=st.id, order=order))
    return st


# ── Pulse of the worker ──────────────────────────────────────────────────────
# The worker writes `runner:heartbeat` (ex=10) every 5 s. If that stops while assignments lie
# in the queue, the worker is stuck, which is exactly what happened on 2026-07-30 for over an
# hour without being noticed anywhere: the assistant simply stayed silent, and that cannot be
# told apart from "has nothing to say". Better to report once too often.
WORKER_STILL_SEC = 180
_puls_gemeldet = False


async def _check_worker_puls() -> None:
    global _puls_gemeldet
    from ..core.redis import PREFIX, QUEUE, get_redis
    from ..models.notification import Notification
    from ..models.user import User
    r = get_redis()
    puls = await r.get(f"{PREFIX}runner:heartbeat")
    wartend = await r.llen(QUEUE)
    steht = puls is None and wartend > 0
    if steht and not _puls_gemeldet:
        log.error("Worker without a pulse, %s assignment(s) waiting", wartend)
        async with SessionLocal() as db:
            # To the operator: without a worker neither the assistant nor an agent runs.
            admin = (await db.execute(select(User).where(User.telegram_chat_id.isnot(None))
                                      .order_by(User.id))).scalars().first()
            if admin:
                from .i18n import tr
                db.add(Notification(
                    user_id=admin.id, kind="worker_down",
                    title=await tr(db, "server.notify.worker_down", admin.locale),
                    body=await tr(db, "server.notify.worker_down_body", admin.locale,
                                  wartend=wartend),
                    chat_id=admin.telegram_chat_id))
                await db.commit()
        _puls_gemeldet = True
    elif not steht and _puls_gemeldet:
        log.info("The worker is back")
        _puls_gemeldet = False


# ── Betriebs-Tick ────────────────────────────────────────────────────────────

async def _tick() -> None:
    """Maintenance update: as soon as the last agent is finished, self-deploy the maintenance
    project over the deployer sidecar. During the update `agent_gate` starts no new runs
    anyway."""
    await _check_worker_puls()
    if not await get_flag("update_pending"):
        return
    from ..models.ops import Deployment
    from .appsettings import get_setting
    async with SessionLocal() as db:
        running = (await db.execute(
            select(func.count()).select_from(Issue).where(Issue.agent_working.is_(True)))).scalar() or 0
        if running:
            return
        mp = await get_setting(db, "maintenance_project_id", "")
        if mp.isdigit():
            db.add(Deployment(project_id=int(mp), stack_dir="", self_deploy=True,
                              status="pending", source="maintenance"))
            await db.commit()
            log.info("Maintenance update: the last agent is done, the self deploy is queued (project %s)", mp)
    await set_flag("update_pending", False)
    await set_flag("update_in_progress", True)


async def run_dispatcher() -> None:
    log.info("operations tick started (tick=%ss)", TICK_SECONDS)
    await asyncio.sleep(5)
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            log.exception("betriebs-tick failed")
        await asyncio.sleep(TICK_SECONDS)


# Is a worker run still going when its last run_step is younger than this? The worker is a
# container of its own and survives a backend reload (uvicorn --reload), and then its run
# must NOT be shot down as "interrupted" but is reattached.
REATTACH_FRESH_SECONDS = 300


async def recover_on_start() -> None:
    """Clean up after a backend restart.

    Reattaching running agents is done by the engine (`recover_workflow_agents`, which knows
    the waiting step). What remains here: acknowledge maintenance flags and reset
    `agent_working` on tickets whose run is demonstrably dead, because otherwise they would
    block all further runs over the runner limit.
    """
    just_updated = await get_flag("update_in_progress") or await get_flag("update_pending")
    if just_updated:
        await set_flag("update_in_progress", False)
        await set_flag("update_pending", False)
        log.info("The maintenance update is finished, operation continues.")
    async with SessionLocal() as db:
        if just_updated:
            from .appsettings import set_setting
            await set_setting(db, "last_update_completed_at", _now().isoformat())
        stuck = (await db.execute(
            select(Issue).where(Issue.agent_working.is_(True)))).scalars().all()
        for issue in stuck:
            run = (await db.execute(select(Run).where(Run.issue_id == issue.id)
                                    .order_by(Run.id.desc()))).scalars().first()
            alive = False
            if run and run.task_id:
                # The first and best information: the pulse of the worker for this exact
                # assignment. Without it a run counted as dead as soon as it sat on a
                # single answer for more than five minutes, while the engine kept hanging.
                if await lauf_lebt(run.task_id):
                    alive = True
                if not alive and run.finished_at is None:
                    last_step = (await db.execute(select(func.max(RunStep.created_at))
                                                  .where(RunStep.run_id == run.id))).scalar()
                    ref = last_step or run.started_at
                    if ref and (_now() - ref).total_seconds() < REATTACH_FRESH_SECONDS:
                        alive = True
                if not alive and await peek_result(run.task_id):
                    alive = True  # the worker was finished, the result still lies in Redis
            if alive:
                log.info("recover: run %s is alive, the engine reattaches it", run.task_id)
                continue
            issue.agent_working = False
            if issue.agent_status == TicketAgentStatus.in_progress:
                from .artifacts import set_ticket_status
                await set_ticket_status(db, issue, TicketAgentStatus.hold,
                                        reason=HoldReason.interrupted)
        await db.commit()
