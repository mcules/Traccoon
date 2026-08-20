"""Der Auftrag, den der Agent eines Jobs sieht.

Die Platzhalter-Mechanik steht anderswo (test_job_params); hier geht es um die Stelle, an der
sie greift, und um das Zeitfenster aus den vorigen Läufen.

Seit die Job-Arten Abläufe sind, führt der Weg über den Knoten `agent_lauf`: Der Prompt wird
sein Auftrag, der Parametersatz sein Startkontext. Geprüft wird deshalb, was in der
Warteschlange landet — dort steht der fertige Text, den der Agent zu sehen bekommt.
"""
import datetime as dt

import pytest
from app.models.ops import Job, JobRun
from app.services.job_modes import als_ablauf
from app.services.scheduler import run_job_kind
from conftest import make_user
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _utc(*a) -> dt.datetime:
    return dt.datetime(*a, tzinfo=dt.timezone.utc)


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


async def _lauf(db, monkeypatch, job: Job) -> str:
    """Den Job einmal auslösen; liefert den Auftrag, den der Agent bekommen hat."""
    auftraege: list[dict] = []

    async def enqueue_task(payload):
        auftraege.append(payload)

    import app.services.workflow_actions as wa
    monkeypatch.setattr("app.core.redis.enqueue_task", enqueue_task)
    monkeypatch.setattr(wa, "enqueue_task", enqueue_task, raising=False)

    await als_ablauf(db, job)
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.flush()
    await run_job_kind(db, job, jr)
    await db.commit()
    return next(a["prompt"] for a in auftraege if a.get("kind") == "agent_frei")


async def test_prompt_wird_mit_parametern_gefuellt(db, anna, monkeypatch, redis_stub):
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="Berichte über {{thema}} aus {{quellen}} auf {{sprache}}.",
            args={"thema": "Funk", "quellen": ["ARRL", "DARC"], "sprache": "Deutsch"})
    db.add(j)
    await db.commit()
    # Die Liste wird zur Aufzählung, nicht zu ihrer Schreibweise — dafür sorgt die
    # Umstellung, indem sie den Platzhalter um den Filter ergänzt.
    assert await _lauf(db, monkeypatch, j) == "Berichte über Funk aus ARRL, DARC auf Deutsch."


async def test_script_argumente_machen_keinen_parametersatz_auf(db, anna, redis_stub):
    """Eine `args`-Liste war ein Skript-Argument und darf im Auftrag nichts ersetzen."""
    from app.models.workflow import WorkflowDefinition, WorkflowVersion

    j = Job(user_id=anna.id, name="Alt", kind="prompt", agent="news",
            prompt="Unverändert {{thema}}", args=["--flag"])
    db.add(j)
    await db.commit()
    await als_ablauf(db, j)
    await db.commit()

    d = await db.get(WorkflowDefinition, j.workflow_definition_id)
    v = await db.get(WorkflowVersion, d.current_version_id)
    arbeit = next(n for n in v.graph["nodes"] if n["id"] == "arbeit")
    assert arbeit["data"]["config"]["action"]["params"]["task"] == "Unverändert {{thema}}"


async def test_zeitfenster_ueberspringt_kaputte_laeufe(db, anna, monkeypatch, redis_stub):
    """War der Job gestern kaputt, muss das Fenster bis zum letzten ERFOLG zurückreichen;
    sonst fällt der Tag des Ausfalls lautlos aus dem Rückblick."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="{{since}}", args={})
    db.add(j)
    await db.commit()
    db.add_all([JobRun(job_id=j.id, status="ok", started_at=_utc(2026, 7, 27, 6, 0)),
                JobRun(job_id=j.id, status="error", started_at=_utc(2026, 7, 28, 6, 0))])
    await db.commit()
    assert await _lauf(db, monkeypatch, j) == "2026-07-27 08:00"    # Europe/Berlin


async def test_der_lauf_bleibt_offen_bis_das_ergebnis_da_ist(db, anna, monkeypatch, redis_stub):
    """Der Job stößt an, der Agent arbeitet — das Ergebnis trägt die Engine nach."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    await _lauf(db, monkeypatch, j)
    jr = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert jr.status == "ok" and jr.workflow_instance_id is not None
    assert jr.output.startswith("Workflow-Instanz")
