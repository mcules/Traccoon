"""Who may set a process off: job, webhook, agent.

The occasion: `kind` branched only in the scheduler. "Run now" (API and agent tool) silently
gave workflow and http jobs to the assistant as a prompt job: no workflow, no error, only an
agent run on an empty prompt. And an agent could not start a process at all, because the tool
was missing although job and webhook could do it.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.ops import Job, JobRun
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.worker.tools_traccoon import TRACCOON_GATED_TOOLS, call_traccoon_tool
from sqlalchemy import select

from conftest import make_user


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna", admin=True)


async def _prozess(db, key="preis-abgleich", subject=WorkflowSubjectKind.standalone,
                   project_id=None, published=True) -> WorkflowDefinition:
    graph = {
        "nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
                  {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                   "data": {"config": {"outcome": "completed"}}}],
        "edges": [{"id": "e1", "source": "s", "target": "e"}],
    }
    d = WorkflowDefinition(project_id=project_id, key=key, name=key, subject_kind=subject)
    db.add(d)
    await db.flush()
    if published:
        v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                            status=WorkflowVersionStatus.published)
        db.add(v)
        await db.flush()
        d.current_version_id = v.id
    await db.commit()
    return d


async def _instanzen(db) -> list[WorkflowInstance]:
    return list((await db.execute(select(WorkflowInstance))).scalars().all())


async def test_a_workflow_job_run_now_starts_an_instance(db, anna, monkeypatch):
    """The core of the bug: run_job must not give the job into the prompt path."""
    d = await _prozess(db)
    job = Job(user_id=anna.id, name="Preise", kind="workflow", workflow_definition_id=d.id,
              type="cron", schedule="0 3 * * *")
    db.add(job)
    await db.commit()

    eingereiht = []

    async def fake_enqueue(payload):
        eingereiht.append(payload)

    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", fake_enqueue)

    out = await call_traccoon_tool(db, anna.id, "traccoon_run_job", {"job_id": job.id})
    assert "ok" in out and "workflow" in out
    assert eingereiht == [], "a workflow job does not belong in the worker queue"
    inst = await _instanzen(db)
    assert len(inst) == 1 and inst[0].definition_id == d.id
    jr = (await db.execute(select(JobRun))).scalars().one()
    assert jr.status == "ok"


async def test_the_agent_starts_a_flow_and_sees_only_startable_ones(db, anna):
    startbar = await _prozess(db, key="startbar")
    await _prozess(db, key="entwurf", published=False)

    listing = await call_traccoon_tool(db, anna.id, "traccoon_list_workflows", {})
    assert "startbar" in listing and "entwurf" not in listing

    out = await call_traccoon_tool(db, anna.id, "traccoon_start_workflow",
                                   {"workflow_id": startbar.id, "context": {"quelle": "models.dev"}})
    assert "gestartet" in out
    inst = await _instanzen(db)
    assert len(inst) == 1
    assert inst[0].context["quelle"] == "models.dev"
    # The origin has to stay recognisable; otherwise it is unclear later who triggered the run.
    assert inst[0].source == f"agent:{anna.id}"


async def test_a_flow_on_a_ticket_demands_a_ticket(db, anna):
    d = await _prozess(db, key="ticket-prozess", subject=WorkflowSubjectKind.issue)
    out = await call_traccoon_tool(db, anna.id, "traccoon_start_workflow", {"workflow_id": d.id})
    assert "issue_key" in out
    assert await _instanzen(db) == []


async def test_an_unpublished_flow_does_not_start(db, anna):
    d = await _prozess(db, key="entwurf", published=False)
    out = await call_traccoon_tool(db, anna.id, "traccoon_start_workflow", {"workflow_id": d.id})
    assert "veröffentlichte" in out
    assert await _instanzen(db) == []


async def test_starting_a_flow_needs_a_grant(db):
    """A process can set off agent runs, approvals and calls to the outside."""
    assert "traccoon_start_workflow" in TRACCOON_GATED_TOOLS
    assert "traccoon_list_workflows" not in TRACCOON_GATED_TOOLS
