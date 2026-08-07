"""Karteileichen in der Lauf-Liste.

Ein Worker-Neustart lässt den abgebrochenen Lauf als `running` zurück; die Reliable-Queue
holt den Auftrag zurück und startet ihn neu. Die Aufräumung beim Worker-Start greift aber
erst nach `STALE_GRACE_SEC` — und ließ damit ausgerechnet die Leichen stehen, die der
Neustart selbst erzeugt hat (Lauf 714 am 2026-08-07 war acht Sekunden alt, blieb danach
für immer „läuft"). Wer denselben Auftrag neu startet, räumt seinen Vorgänger deshalb
selbst ab — genauer als jede Zeitgrenze.
"""
from app.models.agents import Run
from app.worker.runtime import _start_run
from sqlalchemy import select
from test_lifecycle_process import _projekt_mit_ticket


async def _lauf(db, issue, task_id, **kw):
    return await _start_run(db, issue.id, "developer", "execute", "claude_code",
                            "claude-sonnet-5", kw.pop("parent_run_id", None), 0, task_id,
                            project_id=issue.project_id, **kw)


async def test_neuer_lauf_schliesst_die_leiche_desselben_auftrags(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    leiche = await _lauf(db, issue, "wf-1-1-exec-abc")
    assert leiche.status == "running"

    neu = await _lauf(db, issue, "wf-1-1-exec-abc")

    await db.refresh(leiche)
    assert leiche.status == "failed"
    assert leiche.finished_at is not None
    assert "neu gestartet" in (leiche.error or "")
    assert neu.status == "running"          # der frische Lauf bleibt unangetastet


async def test_fremder_auftrag_bleibt_unberuehrt(db):
    _, _, issue, _ = await _projekt_mit_ticket(db)
    anderer = await _lauf(db, issue, "wf-1-1-exec-xyz")
    await _lauf(db, issue, "wf-1-1-exec-abc")
    await db.refresh(anderer)
    assert anderer.status == "running"


async def test_delegierter_unterlauf_raeumt_seinen_elternlauf_nicht_ab(db):
    """Der teure Fehlgriff: ein Unterlauf trägt DIESELBE task_id wie sein Elternteil (der
    Verbund-Schlüssel fürs Büro) und startet, während der Elternlauf noch läuft."""
    _, _, issue, _ = await _projekt_mit_ticket(db)
    eltern = await _lauf(db, issue, "wf-1-1-exec-abc")

    await _lauf(db, issue, "wf-1-1-exec-abc", parent_run_id=eltern.id, spawn_depth=1)

    await db.refresh(eltern)
    assert eltern.status == "running", "Elternlauf wurde von seinem eigenen Kind abgeräumt"
    laeufe = (await db.execute(select(Run).where(Run.task_id == "wf-1-1-exec-abc"))).scalars().all()
    assert len(laeufe) == 2
