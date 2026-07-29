"""Der Job-Lauf selbst: bekommt der Agent den gefüllten Prompt?

Die Platzhalter-Mechanik ist anderswo geprüft (test_job_params). Hier geht es um die eine
Stelle, an der sie wirkt — und um das Zeitfenster, das aus den vorigen Läufen kommt.
"""
import datetime as dt

import pytest
from app.models.ops import Job, JobRun
from app.worker import __main__ as worker
from conftest import make_user
from sqlalchemy import select


def _utc(*a) -> dt.datetime:
    return dt.datetime(*a, tzinfo=dt.timezone.utc)


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


async def _lauf(db, monkeypatch, job: Job) -> str:
    """Job einmal durchlaufen lassen; liefert den Prompt, den der Agent gesehen hat."""
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    await db.commit()
    gesehen = {}

    class Ergebnis:
        status, text, summary, run_id, blocker_kind = "done", "fertig", "fertig", None, None

    async def fake_run_agent(**kw):
        gesehen["prompt"] = kw["issue"]["description"]
        return Ergebnis()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    # `_handle_job` importiert run_agent im Rumpf — dort ersetzen, nicht am Modul.
    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    await worker._handle_job({"job_id": job.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"},
                             None)
    return gesehen["prompt"]


async def test_prompt_wird_mit_parametern_gefuellt(db, anna, monkeypatch):
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="Berichte über {{thema}} aus {{quellen}} auf {{sprache}}.",
            args={"thema": "Funk", "quellen": ["ARRL", "DARC"], "sprache": "Deutsch"})
    db.add(j)
    await db.commit()
    assert await _lauf(db, monkeypatch, j) == "Berichte über Funk aus ARRL, DARC auf Deutsch."


async def test_script_job_argumente_bleiben_liste(db, anna, monkeypatch):
    """Eine `args`-Liste ist Script-Argument und darf im Prompt nichts ersetzen."""
    j = Job(user_id=anna.id, name="Alt", kind="prompt", agent="news",
            prompt="Unverändert {{thema}}", args=["--flag"])
    db.add(j)
    await db.commit()
    assert await _lauf(db, monkeypatch, j) == "Unverändert {{thema}}"


async def test_zeitfenster_ueberspringt_kaputte_laeufe(db, anna, monkeypatch):
    """War der Job gestern kaputt, muss das Fenster bis zum letzten ERFOLG zurückreichen —
    sonst fällt der Ausfalltag stillschweigend aus dem Rückblick."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news",
            prompt="{{seit}}", args={})
    db.add(j)
    await db.commit()
    db.add_all([JobRun(job_id=j.id, status="ok", started_at=_utc(2026, 7, 27, 6, 0)),
                JobRun(job_id=j.id, status="error", started_at=_utc(2026, 7, 28, 6, 0))])
    await db.commit()
    assert await _lauf(db, monkeypatch, j) == "2026-07-27 08:00"    # Europe/Berlin


async def test_job_run_wird_abgeschlossen(db, anna, monkeypatch):
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    await _lauf(db, monkeypatch, j)
    jr = (await db.execute(select(JobRun).order_by(JobRun.id.desc()))).scalars().first()
    assert jr.status == "ok" and jr.output == "fertig"
