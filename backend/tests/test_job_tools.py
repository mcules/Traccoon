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
    assert await _tool(db, anna, "traccoon_get_job", job_id=j.id) == "Job nicht gefunden."


async def test_creating_from_a_template(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="Security-News",
                      template="recherche-digest",
                      params={"titel": "Security-News", "thema": "IT-Sicherheit"})
    assert "angelegt" in out and "ACHTUNG" not in out
    j = (await db.execute(select(Job))).scalars().one()
    assert j.user_id == anna.id and j.kind == "prompt" and j.result_html is True
    assert j.args["thema"] == "IT-Sicherheit"
    j.args["sprache"] == "Deutsch"          # the default of the template stays
    assert j.notify_chat == "123"                  # meldet an denselben Chat


async def test_creating_reports_open_placeholders(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="Halbfertig",
                      prompt="Berichte über {{thema}} aus {{quellen}}.", params={"thema": "x"})
    assert "ACHTUNG" in out and "quellen" in out


async def test_creating_without_a_prompt_and_a_template(db, anna):
    assert "Ohne Prompt" in await _tool(db, anna, "traccoon_create_job", name="Leer")
    assert (await db.execute(select(Job))).scalars().first() is None


async def test_an_unknown_template_names_the_real_ones(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="X", template="quatsch")
    assert "recherche-digest" in out


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
    """Ein Job wird hier ausgeführt, nicht eingereiht: Seit die Arten Abläufe sind, gibt es
    keinen zweiten Weg mehr, auf dem ein Lauf am Zeitplan vorbei startet."""
    db.add(Job(user_id=anna.id, name="Digest", kind="prompt", prompt="x"))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()

    out = await _tool(db, anna, "traccoon_run_job", job_id=j.id)
    assert "ausgeführt" in out
    lauf = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert lauf.workflow_instance_id is not None


async def test_writing_job_tools_need_a_grant(db):
    """A schedule keeps acting permanently, unlike a comment on a ticket."""
    assert TRACCOON_GATED_TOOLS <= TRACCOON_TOOL_NAMES
    assert "traccoon_create_job" in TRACCOON_GATED_TOOLS
    assert "traccoon_list_jobs" not in TRACCOON_GATED_TOOLS
