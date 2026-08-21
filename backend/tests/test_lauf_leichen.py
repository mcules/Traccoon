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
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, task_id, **kw):
    return await _start_run(db, issue.id, "developer", "execute", "claude_code",
                            "claude-sonnet-5", kw.pop("parent_run_id", None), 0, task_id,
                            project_id=issue.project_id, **kw)


async def test_neuer_lauf_schliesst_die_leiche_desselben_auftrags(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    leiche = await _lauf(db, issue, "wf-1-1-exec-abc")
    assert leiche.status == "running"

    new = await _lauf(db, issue, "wf-1-1-exec-abc")

    await db.refresh(leiche)
    assert leiche.status == "failed"
    assert leiche.finished_at is not None
    assert "neu gestartet" in (leiche.error or "")
    assert new.status == "running"          # the fresh run stays untouched


async def test_fremder_task_bleibt_unberuehrt(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    anderer = await _lauf(db, issue, "wf-1-1-exec-xyz")
    await _lauf(db, issue, "wf-1-1-exec-abc")
    await db.refresh(anderer)
    assert anderer.status == "running"


async def test_delegierter_unterlauf_raeumt_seinen_elternlauf_nicht_ab(db):
    """The expensive mistake: a sub-run carries THE SAME task_id as its parent (the link key
    for the office) and starts while the parent run is still running."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    eltern = await _lauf(db, issue, "wf-1-1-exec-abc")

    await _lauf(db, issue, "wf-1-1-exec-abc", parent_run_id=eltern.id, spawn_depth=1)

    await db.refresh(eltern)
    assert eltern.status == "running", "the parent run was cleared away by its own child"
    runs = (await db.execute(select(Run).where(Run.task_id == "wf-1-1-exec-abc"))).scalars().all()
    assert len(runs) == 2
