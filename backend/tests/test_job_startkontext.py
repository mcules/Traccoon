"""A job should be able to give its flow something along.

Until now a flow job always started with an empty context. For the second watcher (the same
check, another metric series) one would therefore have had to build a second flow although
only one word changes. The parameter set of the job now fills the start context, by the same
rule as with prompt jobs.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.ops import Job, JobRun
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.scheduler import _start_workflow_job
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _ablauf(db, anna) -> WorkflowDefinition:
    d = WorkflowDefinition(project_id=None, key="waechter", name="Wächter", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {}}},
                            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "s", "target": "e"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _lauf(db, anna, args):
    d = await _ablauf(db, anna)
    job = Job(name="Wächter", type="cron", schedule="0 * * * *", kind="workflow",
              workflow_definition_id=d.id, user_id=anna.id, args=args)
    db.add(job)
    await db.flush()
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.flush()
    await _start_workflow_job(db, job, jr)
    await db.commit()
    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().first()
    return jr, inst


async def test_parametersatz_wird_startkontext(db):
    anna = await make_user(db, "anna")
    jr, inst = await _lauf(db, anna, {"reihe": "akku.shelter", "still_stunden": 26})
    assert jr.status == "ok"
    assert inst.context["reihe"] == "akku.shelter"
    assert inst.context["still_stunden"] == 26


async def test_script_argumente_bleiben_draussen(db):
    """A list is a script argument, not a context: regression protection."""
    anna = await make_user(db, "anna")
    _jr, inst = await _lauf(db, anna, ["-x", "42"])
    assert inst.context == {}


async def test_ohne_parameter_wie_bisher(db):
    anna = await make_user(db, "anna")
    _jr, inst = await _lauf(db, anna, [])
    assert inst.context == {}
