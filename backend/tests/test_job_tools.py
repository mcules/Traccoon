"""The job control tools of the assistant.

The occasion: the assistant could not see scheduled jobs. Asked about the move of the news
job from predecessor to Traccoon, it answered that this was still open and it needed a ticket; the
job had long been running in Traccoon. Whoever is to talk about jobs has to be able to read them.
"""
import pytest
from app.models.ops import Job, JobRun
from app.worker.tools_traccoon import (
    TRACCOON_GATED_TOOLS, TRACCOON_TOOL_NAMES, call_traccoon_tool,
)
from conftest import make_user
from sqlalchemy import select


@pytest.fixture
async def anna(db):
    u = await make_user(db, "anna")
    u.telegram_chat_id = "123"
    await db.commit()
    return u


async def _tool(db, user, tool, **args) -> str:
    # `werkzeug` instead of `name`: the job name is an argument itself.
    return await call_traccoon_tool(db, user.id, tool, args)


async def test_listing_jobs_shows_schedule_and_state(db, anna):
    db.add(Job(user_id=anna.id, name="KI- & Tech-News", type="cron", schedule="0 6 * * *",
               kind="prompt", agent="news", enabled=True))
    await db.commit()
    out = await _tool(db, anna, "traccoon_list_jobs")
    assert "KI- & Tech-News" in out and "cron:0 6 * * *" in out and "[an]" in out


async def test_foreign_jobs_are_invisible(db, anna):
    bob = await make_user(db, "bob")
    db.add(Job(user_id=bob.id, name="Bobs Job", kind="prompt", prompt="x"))
    await db.commit()
    assert "Bobs Job" not in await _tool(db, anna, "traccoon_list_jobs")
    j = (await db.execute(select(Job))).scalars().first()
    assert await _tool(db, anna, "traccoon_get_job", job_id=j.id) == "Job not found."


async def test_creating_from_a_template(db, anna):
    from app.services.research_flow import ensure
    d = await ensure(db)
    out = await _tool(db, anna, "traccoon_create_job", name="Security-News",
                      template="research-digest",
                      params={"ablage": "security-news", "auftrag": "Was gab es zu IT-Sicherheit?"})
    assert "created" in out and "CAREFUL" not in out
    j = (await db.execute(select(Job))).scalars().one()
    # The template hands out the shared flow — no job builds one of its own any more.
    assert j.user_id == anna.id and j.kind == "workflow" and j.workflow_definition_id == d.id
    assert j.args["ablage"] == "security-news"
    assert j.args["agent"] == "news"        # the default of the template stays
    assert j.notify_chat == "123"                  # meldet an denselben Chat


async def test_a_template_without_its_flow_creates_nothing(db, anna):
    """Better no job than one that is scheduled and has nothing to do."""
    out = await _tool(db, anna, "traccoon_create_job", name="Security-News",
                      template="research-digest")
    assert "flow" in out and "Nothing was created" in out
    assert (await db.execute(select(Job))).scalars().first() is None


async def test_creating_reports_open_placeholders(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="Halbfertig",
                      prompt="Report on {{topic}} out of {{sources}}.", params={"topic": "x"})
    assert "CAREFUL" in out and "sources" in out


async def test_creating_without_a_prompt_and_a_template(db, anna):
    assert "No job without a prompt" in await _tool(db, anna, "traccoon_create_job", name="Leer")
    assert (await db.execute(select(Job))).scalars().first() is None


async def test_an_unknown_template_names_the_real_ones(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="X", template="quatsch")
    assert "research-digest" in out


async def test_parameters_are_merged_not_replaced(db, anna):
    """Otherwise a job loses all other values when ONE is changed."""
    db.add(Job(user_id=anna.id, name="Digest", kind="prompt", prompt="{{thema}} {{sprache}}",
               args={"thema": "Funk", "sprache": "Deutsch"}))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()
    await _tool(db, anna, "traccoon_update_job", job_id=j.id, params={"thema": "Recht"})
    await db.refresh(j)
    assert j.args == {"thema": "Recht", "sprache": "Deutsch"}


async def test_disabling_through_an_update(db, anna):
    db.add(Job(user_id=anna.id, name="Alt", kind="prompt", prompt="x", enabled=True))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()
    out = await _tool(db, anna, "traccoon_update_job", job_id=j.id, enabled=False)
    await db.refresh(j)
    assert j.enabled is False and "enabled" in out


async def test_the_agent_receives_the_result_of_the_run(db, anna, redis_stub):
    """A job is executed here, not queued: ever since the kinds became flows there is no
    second way left on which a run starts past the schedule."""
    db.add(Job(user_id=anna.id, name="Digest", kind="prompt", prompt="x"))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()

    out = await _tool(db, anna, "traccoon_run_job", job_id=j.id)
    assert "ran:" in out
    run = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert run.workflow_instance_id is not None


async def test_writing_job_tools_need_a_grant(db):
    """A schedule keeps acting permanently, unlike a comment on a ticket."""
    assert TRACCOON_GATED_TOOLS <= TRACCOON_TOOL_NAMES
    assert "traccoon_create_job" in TRACCOON_GATED_TOOLS
    assert "traccoon_list_jobs" not in TRACCOON_GATED_TOOLS
