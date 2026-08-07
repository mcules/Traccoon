"""Ein Lauf, hinter dem niemand mehr steht, darf nicht ewig „läuft" bleiben.

Lauf 753 am 2026-08-07: der Prozess starb an einem Deadlock beim Schreiben der
Schrittzeile — an einer Stelle also, an der er sich nicht mehr abmelden konnte. Die Zeile
blieb auf `running`, der Prozess war längst über den Störungs-Zweig weitergezogen, und die
Board-Regel („wer arbeitet, steht auf In Arbeit") hielt das Ticket in der Arbeit fest,
obwohl es auf eine Rückmeldung wartete.

Maßstab ist das Lebenszeichen, nicht die Uhr: ein Agent darf Stunden brauchen.
"""
import datetime as dt

from app.models.agents import Run
from app.services.workflow_engine import tote_laeufe_schliessen
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, task_id, *, alter_sek: int) -> Run:
    run = Run(issue_id=issue.id, project_id=issue.project_id, task_id=task_id,
              agent="developer", phase="execution", provider="claude_code",
              model="claude-sonnet-5", status="running",
              started_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=alter_sek))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def test_lauf_ohne_lebenszeichen_wird_geschlossen(db, monkeypatch):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-tot", alter_sek=3600)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _nein)
    assert await tote_laeufe_schliessen() == 1

    await db.refresh(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "Kein Lebenszeichen" in (run.error or "")


async def test_lebender_lauf_bleibt_unangetastet(db, monkeypatch):
    """Die wichtigere Hälfte: ein Agent, der seit Stunden arbeitet, wird NICHT abgeräumt."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-lebt", alter_sek=7200)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _ja)
    assert await tote_laeufe_schliessen() == 0

    await db.refresh(run)
    assert run.status == "running"


async def test_junger_lauf_bleibt_in_der_gnadenfrist(db, monkeypatch):
    """Innerhalb der Gnadenfrist wird gar nicht erst gefragt — der Puls kann Sekunden
    hinterherhinken, und ein gerade gestarteter Lauf ist kein Fall für die Bestattung."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    run = await _lauf(db, issue, "wf-1-1-exec-jung", alter_sek=5)

    monkeypatch.setattr("app.core.redis.lauf_lebt", _nein)
    assert await tote_laeufe_schliessen() == 0

    await db.refresh(run)
    assert run.status == "running"


async def _ja(task_id: str) -> bool:
    return True


async def _nein(task_id: str) -> bool:
    return False
