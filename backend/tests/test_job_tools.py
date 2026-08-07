"""Die Job-Steuertools des Assistenten.

Anlass: Der Assistent konnte geplante Jobs nicht sehen. Auf die Frage nach dem Umzug des
News-Jobs von nexus nach Traccoon antwortete er, das sei noch offen und er brauche ein
Ticket — der Job lief in Traccoon längst. Wer über Jobs reden soll, muss sie lesen können.
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


async def _tool(db, user, werkzeug, **args) -> str:
    # `werkzeug` statt `name`: der Job-Name ist selbst ein Argument.
    return await call_traccoon_tool(db, user.id, werkzeug, args)


async def test_jobs_auflisten_zeigt_zeitplan_und_zustand(db, anna):
    db.add(Job(user_id=anna.id, name="KI- & Tech-News", type="cron", schedule="0 6 * * *",
               kind="prompt", agent="news", enabled=True))
    await db.commit()
    out = await _tool(db, anna, "traccoon_list_jobs")
    assert "KI- & Tech-News" in out and "cron:0 6 * * *" in out and "[an]" in out


async def test_fremde_jobs_sind_unsichtbar(db, anna):
    bob = await make_user(db, "bob")
    db.add(Job(user_id=bob.id, name="Bobs Job", kind="prompt", prompt="x"))
    await db.commit()
    assert "Bobs Job" not in await _tool(db, anna, "traccoon_list_jobs")
    j = (await db.execute(select(Job))).scalars().first()
    assert await _tool(db, anna, "traccoon_get_job", job_id=j.id) == "Job nicht gefunden."


async def test_get_job_zeigt_fehlergrund_eines_laufs(db, anna):
    """Anlass: Job #3 stand seit dem 03.08. jeden Tag auf `error` — weder `traccoon_get_job`
    noch die Telegram-Meldung nannten den Grund, nur „error". Auf die Frage „warum
    funktioniert der nicht?" muss der Assistent den Fehlertext zitieren können."""
    import datetime as dt
    j = Job(user_id=anna.id, name="KI- & Tech-News", kind="prompt", agent="news", prompt="x")
    db.add(j)
    await db.commit()
    db.add(JobRun(job_id=j.id, status="error",
                  started_at=dt.datetime(2026, 8, 7, 6, 0, tzinfo=dt.timezone.utc),
                  error="Nach 4 Fortsetzungsrunde(n) noch nicht fertig (loop_exhausted)"))
    await db.commit()
    out = await _tool(db, anna, "traccoon_get_job", job_id=j.id)
    assert "error — Nach 4 Fortsetzungsrunde(n)" in out


async def test_anlegen_ueber_vorlage(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="Security-News",
                      template="recherche-digest",
                      params={"titel": "Security-News", "thema": "IT-Sicherheit"})
    assert "angelegt" in out and "ACHTUNG" not in out
    j = (await db.execute(select(Job))).scalars().one()
    assert j.user_id == anna.id and j.kind == "prompt" and j.result_html is True
    assert j.args["thema"] == "IT-Sicherheit"
    assert j.args["sprache"] == "Deutsch"          # Vorgabe der Vorlage bleibt
    assert j.notify_chat == "123"                  # meldet an denselben Chat


async def test_anlegen_meldet_offene_platzhalter(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="Halbfertig",
                      prompt="Berichte über {{thema}} aus {{quellen}}.", params={"thema": "x"})
    assert "ACHTUNG" in out and "quellen" in out


async def test_anlegen_ohne_prompt_und_vorlage(db, anna):
    assert "Ohne Prompt" in await _tool(db, anna, "traccoon_create_job", name="Leer")
    assert (await db.execute(select(Job))).scalars().first() is None


async def test_unbekannte_vorlage_nennt_die_echten(db, anna):
    out = await _tool(db, anna, "traccoon_create_job", name="X", template="quatsch")
    assert "recherche-digest" in out


async def test_parameter_werden_nachgezogen_nicht_ersetzt(db, anna):
    """Sonst verliert ein Job beim Ändern EINES Wertes alle anderen."""
    db.add(Job(user_id=anna.id, name="Digest", kind="prompt", prompt="{{thema}} {{sprache}}",
               args={"thema": "Funk", "sprache": "Deutsch"}))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()
    await _tool(db, anna, "traccoon_update_job", job_id=j.id, params={"thema": "Recht"})
    await db.refresh(j)
    assert j.args == {"thema": "Recht", "sprache": "Deutsch"}


async def test_abschalten_ueber_update(db, anna):
    db.add(Job(user_id=anna.id, name="Alt", kind="prompt", prompt="x", enabled=True))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()
    out = await _tool(db, anna, "traccoon_update_job", job_id=j.id, enabled=False)
    await db.refresh(j)
    assert j.enabled is False and "enabled" in out


async def test_lauf_wird_erst_nach_dem_commit_eingereiht(db, anna, monkeypatch):
    """Andersherum greift ein freier Worker den Auftrag, bevor es den JobRun gibt — der Lauf
    bliebe für immer auf 'running' (derselbe Fehler wie in api/ops.py)."""
    db.add(Job(user_id=anna.id, name="Digest", kind="prompt", prompt="x"))
    await db.commit()
    j = (await db.execute(select(Job))).scalars().one()

    gesehen = {}

    async def fake_enqueue(payload):
        # Zum Zeitpunkt des Einreihens MUSS der JobRun schon in der DB stehen.
        factory = getattr(db, "__test_factory__")
        async with factory() as s2:
            gesehen["run"] = await s2.get(JobRun, payload["job_run_id"])

    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", fake_enqueue)
    out = await _tool(db, anna, "traccoon_run_job", job_id=j.id)
    assert "läuft" in out and gesehen["run"] is not None


async def test_schreibende_jobtools_brauchen_freigabe(db):
    """Ein Zeitplan wirkt dauerhaft weiter — anders als ein Kommentar am Ticket."""
    assert TRACCOON_GATED_TOOLS <= TRACCOON_TOOL_NAMES
    assert "traccoon_create_job" in TRACCOON_GATED_TOOLS
    assert "traccoon_list_jobs" not in TRACCOON_GATED_TOOLS
