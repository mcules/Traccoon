"""A run nobody stands behind any more must not stay "running" forever.

Run 753 on 2026-08-07: the process died of a deadlock while writing the step row, so at a
place where it could no longer sign off. The row stayed on `running`, the process had long
moved on over the disturbance branch, and the board rule ("whoever works stands on in
progress") held the ticket in the work although it was waiting for a reply.

The measure is the sign of life, not the clock: an agent may take hours.
"""
import datetime as dt

from app.models.agents import Run
from app.services.workflow_engine import dead_runs_close
from test_lifecycle_process import _project_with_ticket


async def _run(db, issue, task_id, *, age_sec: int) -> Run:
    run = Run(issue_id=issue.id, project_id=issue.project_id, task_id=task_id,
              agent="developer", phase="execution", provider="claude_code",
              model="claude-sonnet-5", status="running",
              started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=age_sec))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def test_a_run_without_a_sign_of_life_is_closed(db, monkeypatch):
    _, _, issue, _ = await _project_with_ticket(db)
    run = await _run(db, issue, "wf-1-1-exec-tot", age_sec=3600)

    monkeypatch.setattr("app.core.redis.run_alive", _no)
    assert await dead_runs_close() == 1

    await db.refresh(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "No sign of life" in (run.error or "")


async def test_a_living_run_stays_untouched(db, monkeypatch):
    """The more important half: an agent that has been working for hours is NOT cleared away."""
    _, _, issue, _ = await _project_with_ticket(db)
    run = await _run(db, issue, "wf-1-1-exec-lebt", age_sec=7200)

    monkeypatch.setattr("app.core.redis.run_alive", _yes)
    assert await dead_runs_close() == 0

    await db.refresh(run)
    assert run.status == "running"


async def test_a_young_run_stays_in_the_grace_period(db, monkeypatch):
    """Within the grace period nothing is asked at all: the pulse can lag behind by seconds,
    and a run that has just started is no case for the undertaker."""
    _, _, issue, _ = await _project_with_ticket(db)
    run = await _run(db, issue, "wf-1-1-exec-jung", age_sec=5)

    monkeypatch.setattr("app.core.redis.run_alive", _no)
    assert await dead_runs_close() == 0

    await db.refresh(run)
    assert run.status == "running"


async def _yes(task_id: str) -> bool:
    return True


async def _no(task_id: str) -> bool:
    return False
