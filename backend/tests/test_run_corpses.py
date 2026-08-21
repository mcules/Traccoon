"""Stale entries in the run list.

A worker restart leaves the aborted run behind as `running`; the reliable queue fetches the
assignment back and starts it anew. The clean-up at the worker start only takes hold after
`STALE_GRACE_SEC` though, and thereby left standing exactly the corpses the restart itself
produced (run 714 on 2026-08-07 was eight seconds old and stayed "running" forever).
Whoever starts the same assignment anew therefore clears its predecessor away themselves,
more precisely than any time limit.
"""
from app.models.agents import Run
from app.worker.runtime import _start_run
from sqlalchemy import select
from test_lifecycle_process import _project_with_ticket


async def _run(db, issue, task_id, **kw):
    return await _start_run(db, issue.id, "developer", "execute", "claude_code",
                            "claude-sonnet-5", kw.pop("parent_run_id", None), 0, task_id,
                            project_id=issue.project_id, **kw)


async def test_a_new_run_closes_the_corpse_of_the_same_task(db):
    _, _, issue, _ = await _project_with_ticket(db)
    corpse = await _run(db, issue, "wf-1-1-exec-abc")
    assert corpse.status == "running"

    new = await _run(db, issue, "wf-1-1-exec-abc")

    await db.refresh(corpse)
    assert corpse.status == "failed"
    assert corpse.finished_at is not None
    assert "neu gestartet" in (corpse.error or "")
    assert new.status == "running"          # the fresh run stays untouched


async def test_a_foreign_task_stays_untouched(db):
    _, _, issue, _ = await _project_with_ticket(db)
    different = await _run(db, issue, "wf-1-1-exec-xyz")
    await _run(db, issue, "wf-1-1-exec-abc")
    await db.refresh(different)
    assert different.status == "running"


async def test_a_delegated_sub_run_does_not_clear_its_parent_run(db):
    """The expensive mistake: a sub-run carries THE SAME task_id as its parent (the link key
    for the office) and starts while the parent run is still running."""
    _, _, issue, _ = await _project_with_ticket(db)
    parent = await _run(db, issue, "wf-1-1-exec-abc")

    await _run(db, issue, "wf-1-1-exec-abc", parent_run_id=parent.id, spawn_depth=1)

    await db.refresh(parent)
    assert parent.status == "running", "the parent run was cleared away by its own child"
    runs = (await db.execute(select(Run).where(Run.task_id == "wf-1-1-exec-abc"))).scalars().all()
    assert len(runs) == 2
