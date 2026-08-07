"""Der Job-Lauf selbst: bekommt der Agent den gefüllten Prompt?

Die Platzhalter-Mechanik ist anderswo geprüft (test_job_params). Hier geht es um die eine
Stelle, an der sie wirkt — und um das Zeitfenster, das aus den vorigen Läufen kommt.
"""
import datetime as dt

import pytest
from app.models.notification import Notification
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


async def test_loop_exhausted_bekommt_fortsetzung_statt_sofort_fehler(db, anna, monkeypatch):
    """Anlass: der News-Digest riss ab dem 03.08. jeden Tag das Wanduhr-Limit (`loop_exhausted`)
    und `_handle_job` wertete das sofort als `error` — ohne einen zweiten Versuch, wie es die
    Workflow-Engine für Ticket-Läufe längst tut. Bricht der erste Anlauf ab, muss ein frischer
    `run_agent`-Aufruf weiterarbeiten dürfen und bei Erfolg `ok` liefern."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Erschoepft:
        status, text, summary, run_id, blocker_kind = "loop_exhausted", "", "Zwischenstand", None, None

    class Fertig:
        status, text, summary, run_id, blocker_kind = "done", "fertig", "fertig", None, None

    rufe = {"n": 0}

    async def fake_run_agent(**kw):
        rufe["n"] += 1
        return Erschoepft() if rufe["n"] == 1 else Fertig()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    await db.refresh(jr)
    assert rufe["n"] == 2                  # ein Fortsetzungsversuch, dann fertig
    assert jr.status == "ok" and jr.output == "fertig"


async def test_dauerhaft_erschoepfter_job_nennt_korrekte_rundenzahl(db, anna, monkeypatch):
    """Anlass Review-Befund: `cont_index` wurde nach JEDER `loop_exhausted`-Runde erhöht,
    auch nach der letzten (die Schleife endet dort durch Erschöpfung von `range`, nicht durch
    `break`) — bei `JOB_MAX_CONTINUATIONS=2` und drei `loop_exhausted`-Versuchen in Folge stand
    fälschlich „Nach 3" statt „Nach 2" in `jr.error` (eine Runde zu viel gemeldet)."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Erschoepft:
        status, text, summary, run_id, blocker_kind = "loop_exhausted", "immer noch nicht fertig", "", None, None

    rufe = {"n": 0}

    async def fake_run_agent(**kw):
        rufe["n"] += 1
        return Erschoepft()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    monkeypatch.setattr(worker, "JOB_MAX_CONTINUATIONS", 2)
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    await db.refresh(jr)
    assert rufe["n"] == 3                          # 1 Erstversuch + 2 Fortsetzungsrunden
    assert jr.status == "error"
    assert "Nach 2 Fortsetzungsrunde(n)" in jr.error   # NICHT "Nach 3"


async def test_ohne_fortsetzung_erlaubt_zaehlt_null_runden(db, anna, monkeypatch):
    """`JOB_MAX_CONTINUATIONS=0`: ein einziger Versuch, keine Fortsetzung — die Meldung muss
    „Nach 0 Fortsetzungsrunde(n)" sagen, nicht „Nach 1" (Off-by-one aus dem Review)."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Erschoepft:
        status, text, summary, run_id, blocker_kind = "loop_exhausted", "nicht fertig", "", None, None

    async def fake_run_agent(**kw):
        return Erschoepft()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    monkeypatch.setattr(worker, "JOB_MAX_CONTINUATIONS", 0)
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    await db.refresh(jr)
    assert "Nach 0 Fortsetzungsrunde(n)" in jr.error


async def test_gesamtzeitbudget_bricht_weitere_fortsetzungsrunden_ab(db, anna, monkeypatch):
    """Ohne Gesamtzeitbudget könnte die Fortsetzungskette bis zu
    (JOB_MAX_CONTINUATIONS+1) * JOB_RUN_TIMEOUT_SEC einen Worker-Slot blockieren. Ist das
    Gesamtbudget schon vor einer weiteren Runde aufgebraucht, darf `run_agent` kein weiteres
    Mal aufgerufen werden — der erste Versuch läuft aber immer."""
    j = Job(user_id=anna.id, name="Digest", kind="prompt", agent="news", prompt="x", args={})
    db.add(j)
    await db.commit()
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Erschoepft:
        status, text, summary, run_id, blocker_kind = "loop_exhausted", "nicht fertig", "", None, None

    rufe = {"n": 0}

    async def fake_run_agent(**kw):
        rufe["n"] += 1
        return Erschoepft()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    monkeypatch.setattr(worker, "JOB_MAX_CONTINUATIONS", 4)
    monkeypatch.setattr(worker, "JOB_MAX_TOTAL_SECONDS", 0)   # sofort aufgebraucht
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    await db.refresh(jr)
    assert rufe["n"] == 1                          # nur der erste (garantierte) Versuch
    assert jr.status == "error"
    assert "Nach 0 Fortsetzungsrunde(n)" in jr.error


async def test_fehlgeschlagener_job_nennt_den_grund_in_der_meldung(db, anna, monkeypatch):
    """„Job … fehlgeschlagen" ohne Grund ist von außen nicht diagnostizierbar — die erste
    Zeile des Fehlers muss in Titel/Body der Telegram-Meldung stehen."""
    anna.telegram_chat_id = "123"
    j = Job(user_id=anna.id, name="KI- & Tech-News", kind="prompt", agent="news", prompt="x",
            args={}, notify_mode="always")
    db.add(j)
    await db.commit()   # inkl. telegram_chat_id — `_handle_job` öffnet eine EIGENE Session
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Ergebnis:
        status, text, summary, run_id, blocker_kind = (
            "loop_exhausted", "Web-Suche brach mit Rate-Limit ab", "", None, None)

    async def fake_run_agent(**kw):
        return Ergebnis()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    monkeypatch.setattr(worker, "JOB_MAX_CONTINUATIONS", 0)
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    await db.refresh(jr)
    assert jr.status == "error" and "Web-Suche brach mit Rate-Limit ab" in jr.error
    n = (await db.execute(select(Notification))).scalars().one()
    assert "fehlgeschlagen" in n.title and "Web-Suche brach mit Rate-Limit ab" in n.title


async def test_fehlgeschlagener_digest_job_zeigt_grund_nicht_nur_den_link(db, anna, monkeypatch):
    """Anlass Job #3: `result_html`-Jobs (Digests) überschrieben den Body IMMER mit dem
    nackten `/digest/<id>`-Link — bei `error` verlinkte das auf eine Seite ohne Digest, der
    Fehlergrund ging in der Telegram-Meldung komplett verloren. Der Body muss ihn tragen."""
    anna.telegram_chat_id = "123"
    j = Job(user_id=anna.id, name="KI- & Tech-News", kind="prompt", agent="news", prompt="x",
            args={}, notify_mode="always", result_html=True)
    db.add(j)
    await db.commit()
    jr = JobRun(job_id=j.id, status="running")
    db.add(jr)
    await db.commit()

    class Ergebnis:
        status, text, summary, run_id, blocker_kind = (
            "loop_exhausted", "Web-Suche brach mit Rate-Limit ab", "", None, None)

    async def fake_run_agent(**kw):
        return Ergebnis()

    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "news"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    monkeypatch.setattr(worker, "JOB_MAX_CONTINUATIONS", 0)
    await worker._handle_job({"job_id": j.id, "job_run_id": jr.id, "task_id": f"job-{jr.id}"}, None)

    n = (await db.execute(select(Notification))).scalars().one()
    assert "Web-Suche brach mit Rate-Limit ab" in n.body
    assert "/digest/" in n.body
