"""After an abort the successor knows what has already been done.

A run that ends in an orderly way hands over (`compaction.uebergabe`); an aborted one does
not: at the worker restart on 2026-08-07 runs 753/754 lost their history, and the successors
began at zero and read the same files again although their changes had long stood in the
worktree. The facts about that lie in the database.
"""
import datetime as dt

from app.models.agents import Run, RunStep
from app.worker.runtime import _abbruch_handover
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, *, status, minutes_her=5, last_text="", error="Worker-Neustart") -> Run:
    now = dt.datetime.now(dt.UTC)
    run = Run(issue_id=issue.id, project_id=issue.project_id, task_id="wf-1-1-exec-x",
              agent="developer", phase="execution", provider="claude_code",
              model="claude-sonnet-5", status=status, last_text=last_text, error=error,
              started_at=now - dt.timedelta(minutes=minutes_her + 5),
              finished_at=now - dt.timedelta(minutes=minutes_her))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _step(db, run, *, tool, target, ok=True, rolle="tool", kind="tool_result", seq=1):
    db.add(RunStep(run_id=run.id, seq=seq, role=rolle, kind=kind, tool_name=tool,
                   target=target, ok=ok, content="x"))
    await db.commit()


async def test_changed_files_are_handed_over(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed", last_text="Ich war beim Timeout-Fix.")
    await _step(db, vor, tool="fs_edit", target="backend/app/bot/__main__.py", seq=1)
    await _step(db, vor, tool="fs_write", target="backend/tests/test_voice.py", seq=2)
    await _step(db, vor, tool="fs_read", target="README.md", seq=3)   # reading does not count
    await _step(db, vor, tool=None, target=None, rolle="assistant", kind="usage", seq=4)

    text = await _abbruch_handover(db, issue.id, run_id=vor.id + 999)

    assert "backend/app/bot/__main__.py" in text
    assert "backend/tests/test_voice.py" in text
    assert "README.md" not in text
    assert "Ich war beim Timeout-Fix." in text
    assert "Worker-Neustart" in text


async def test_no_handover_without_write_access(db):
    """Whoever wrote nothing leaves nothing behind, and then silence is more honest than a
    handover that only says "I have read"."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed")
    await _step(db, vor, tool="fs_read", target="README.md")

    assert await _abbruch_handover(db, issue.id, run_id=vor.id + 999) == ""


async def test_cleanly_finished_run_is_not_handed_over(db):
    """`loop_exhausted` has its own, better handover; this one is only the stopgap."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="loop_exhausted")
    await _step(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_handover(db, issue.id, run_id=vor.id + 999) == ""


async def test_old_abort_stays_untouched(db):
    """An abort from yesterday does not describe today's worktree."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed", minutes_her=600)
    await _step(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_handover(db, issue.id, run_id=vor.id + 999) == ""


async def test_own_run_does_not_count(db):
    """The running run must not see itself as its predecessor."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed")
    await _step(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_handover(db, issue.id, run_id=vor.id) == ""
