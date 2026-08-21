"""A run nobody stands behind any more must not stay "running" forever.

Run 753 on 2026-08-07: the process died of a deadlock while writing the step row, so at a
place where it could no longer sign off. The row stayed on `running`, the process had long
moved on over the disturbance branch, and the board rule ("whoever works stands on in
progress") held the ticket in the work although it was waiting for a reply.

The measure is the sign of life, not the clock: an agent may take hours.
"""
import datetime as dt

from app.models.agents import Run
from app.services.workflow_engine import tote_runs_schliessen
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, task_id, *, alter_sek: int) -> Run:
    run = Run(issue_id=issue.id, project_id=issue.project_id, task_id=task_id,
              agent="developer", phase="execution", provider="claude_code",
              model="claude-sonnet-5", status="running",
              started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=alter_sek))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def test_lauf_ohne_lebenszeichen_wird_geschlossen(db, monkeypatch):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-tot", alter_sek=3600)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _nein)
    assert await tote_runs_schliessen() == 1

    await db.refresh(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "Kein Lebenszeichen" in (run.error or "")


async def test_lebender_lauf_bleibt_unangetastet(db, monkeypatch):
    """The more important half: an agent that has been working for hours is NOT cleared away."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-lebt", alter_sek=7200)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _ja)
    assert await tote_runs_schliessen() == 0

    await db.refresh(run)
    assert run.status == "running"


async def test_junger_lauf_bleibt_in_der_gnadenfrist(db, monkeypatch):
    """Within the grace period nothing is asked at all: the pulse can lag behind by seconds,
    and a run that has just started is no case for the undertaker."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-jung", alter_sek=5)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _nein)
    assert await tote_runs_schliessen() == 0

    await db.refresh(run)
    assert run.status == "running"


async def _ja(task_id: str) -> bool:
    return True


async def _nein(task_id: str) -> bool:
    return False
