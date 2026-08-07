"""Nach einem Abbruch weiß der Nachfolger, was schon getan ist.

Ein Lauf, der geordnet endet, übergibt (`compaction.uebergabe`). Ein abgebrochener nicht:
beim Worker-Neustart am 2026-08-07 verloren die Läufe 753/754 ihren Verlauf, die Nachfolger
begannen bei null — und lasen dieselben Dateien noch einmal, obwohl ihre Änderungen längst
im Worktree standen. Die Fakten dazu liegen in der Datenbank.
"""
import datetime as dt

from app.models.agents import Run, RunStep
from app.worker.runtime import _abbruch_uebergabe
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, *, status, minuten_her=5, last_text="", fehler="Worker-Neustart") -> Run:
    jetzt = dt.datetime.now(dt.UTC)
    run = Run(issue_id=issue.id, project_id=issue.project_id, task_id="wf-1-1-exec-x",
              agent="developer", phase="execution", provider="claude_code",
              model="claude-sonnet-5", status=status, last_text=last_text, error=fehler,
              started_at=jetzt - dt.timedelta(minutes=minuten_her + 5),
              finished_at=jetzt - dt.timedelta(minutes=minuten_her))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _schritt(db, run, *, tool, target, ok=True, rolle="tool", kind="tool_result", seq=1):
    db.add(RunStep(run_id=run.id, seq=seq, role=rolle, kind=kind, tool_name=tool,
                   target=target, ok=ok, content="x"))
    await db.commit()


async def test_geaenderte_dateien_werden_uebergeben(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed", last_text="Ich war beim Timeout-Fix.")
    await _schritt(db, vor, tool="fs_edit", target="backend/app/bot/__main__.py", seq=1)
    await _schritt(db, vor, tool="fs_write", target="backend/tests/test_voice.py", seq=2)
    await _schritt(db, vor, tool="fs_read", target="README.md", seq=3)   # Lesen zählt nicht
    await _schritt(db, vor, tool=None, target=None, rolle="assistant", kind="usage", seq=4)

    text = await _abbruch_uebergabe(db, issue.id, run_id=vor.id + 999)

    assert "backend/app/bot/__main__.py" in text
    assert "backend/tests/test_voice.py" in text
    assert "README.md" not in text
    assert "Ich war beim Timeout-Fix." in text
    assert "Worker-Neustart" in text


async def test_ohne_schreibzugriff_keine_uebergabe(db):
    """Wer nichts geschrieben hat, hinterlässt nichts — dann ist Schweigen ehrlicher als
    eine Übergabe, die nur „ich habe gelesen" sagt."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed")
    await _schritt(db, vor, tool="fs_read", target="README.md")

    assert await _abbruch_uebergabe(db, issue.id, run_id=vor.id + 999) == ""


async def test_geordnet_beendeter_lauf_wird_nicht_uebergeben(db):
    """`loop_exhausted` hat seine eigene, bessere Übergabe — die hier ist nur der Notnagel."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="loop_exhausted")
    await _schritt(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_uebergabe(db, issue.id, run_id=vor.id + 999) == ""


async def test_alter_abbruch_bleibt_liegen(db):
    """Ein Abbruch von gestern beschreibt nicht den heutigen Worktree."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed", minuten_her=600)
    await _schritt(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_uebergabe(db, issue.id, run_id=vor.id + 999) == ""


async def test_eigener_lauf_zaehlt_nicht(db):
    """Der laufende Lauf darf sich nicht selbst als Vorgänger sehen."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    vor = await _lauf(db, issue, status="failed")
    await _schritt(db, vor, tool="fs_edit", target="a.py")

    assert await _abbruch_uebergabe(db, issue.id, run_id=vor.id) == ""
