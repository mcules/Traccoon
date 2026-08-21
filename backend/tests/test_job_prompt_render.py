"""The assignment the agent of a job sees.

The placeholder mechanism is checked elsewhere (test_job_params); here it is about the place
where it takes effect, and about the time window out of the previous runs.

Ever since the job kinds became flows, the way leads through the node `agent_run`: the prompt
becomes its assignment, the parameter set its start context. What is checked is therefore what
lands in the queue — there stands the finished text the agent gets to see.
"""
import datetime as dt

import pytest
from app.models.ops import Job, JobRun
from app.services.job_modes import as_flow
from app.services.scheduler import run_job_kind
from conftest import make_user
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _utc(*a) -> dt.datetime:
    return dt.datetime(*a, tzinfo=dt.timezone.utc)


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


async def _run(db, monkeypatch, job: Job) -> str:
    """Fire the job once; returns the assignment the agent was given."""
    tasks: list[dict] = []

    async def enqueue_task(payload):
        tasks.append(payload)

    import app.services.workflow_actions as wa
    monkeypatch.setattr("app.core.redis.enqueue_task", enqueue_task)
    monkeypatch.setattr(wa, "enqueue_task", enqueue_task, raising=False)

    await as_flow(db, job)
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.flush()
    await run_job_kind(db, job, jr)
    await db.commit()
    return next(a["prompt"] for a in tasks if a.get("kind") == "agent_frei")


async def test_the_prompt_is_filled_with_parameters(db, anna, monkeypatch, redis_stub):
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="Berichte über {{thema}} aus {{quellen}} auf {{sprache}}.",
            args={"thema": "Funk", "quellen": ["ARRL", "DARC"], "sprache": "Deutsch"})
    db.add(j)
    await db.commit()
    # The list becomes an enumeration, not its spelling — the conversion sees to that by
    # extending the placeholder with the filter.
    assert await _run(db, monkeypatch, j) == "Berichte über Funk aus ARRL, DARC auf Deutsch."


async def test_script_arguments_do_not_open_a_parameter_set(db, anna, redis_stub):
    """An `args` list was a script argument and must replace nothing in the assignment."""
    from app.models.workflow import WorkflowDefinition, WorkflowVersion

    j = Job(user_id=anna.id, name="Alt", kind="prompt", agent="news",
            prompt="Unverändert {{thema}}", args=["--flag"])
    db.add(j)
    await db.commit()
    await as_flow(db, j)
    await db.commit()

    d = await db.get(WorkflowDefinition, j.workflow_definition_id)
    v = await db.get(WorkflowVersion, d.current_version_id)
    work = next(n for n in v.graph["nodes"] if n["id"] == "arbeit")
    assert work["data"]["config"]["action"]["params"]["task"] == "Unverändert {{thema}}"


async def test_the_time_window_skips_broken_runs(db, anna, monkeypatch, redis_stub):
    """If the job was broken yesterday, the window has to reach back to the last SUCCESS;
    otherwise the day of the outage falls silently out of the review."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="{{since}}", args={})
    db.add(j)
    await db.commit()
    db.add_all([JobRun(job_id=j.id, status="ok", started_at=_utc(2026, 7, 27, 6, 0)),
                JobRun(job_id=j.id, status="error", started_at=_utc(2026, 7, 28, 6, 0))])
    await db.commit()
    assert await _run(db, monkeypatch, j) == "2026-07-27 08:00"    # Europe/Berlin


async def test_the_run_stays_open_until_the_result_is_there(db, anna, monkeypatch, redis_stub):
    """The job kicks off, the agent works — the result the engine fills in."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    await _run(db, monkeypatch, j)
    jr = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert jr.status == "ok" and jr.workflow_instance_id is not None
    assert jr.output.startswith("Workflow-Instanz")
