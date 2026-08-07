"""Das Review-Gate darf keine Befunde erfinden.

TRA-31 am 2026-08-07: der Prüfer-Lauf starb an „Antwort bei max_tokens abgeschnitten".
Das Gate prüfte nur, ob `<review-ok/>` im Text steht — stand es nicht drin, galt der Text
als Befundliste. Der Entwickler wurde daraufhin losgeschickt, eine Provider-Fehlermeldung
zu „beheben": eine erfundene Aufgabe, die eine der zwei Korrektur-Runden kostet und danach
im Review-Hold endet.
"""
import app.worker.__main__ as worker
from app.worker.runtime import RunResult
from test_lifecycle_process import _projekt_mit_ticket


class _Ctx:
    pass


async def _gate(db, monkeypatch, rev: RunResult, *, diff="--- a\n+++ b\n+x"):
    _, proj, issue, _ = await _projekt_mit_ticket(db)
    laeufe = []

    async def fake_run_agent(**kw):
        rolle = kw["agent"].role
        laeufe.append(rolle)
        if rolle != "code_reviewer":
            return RunResult("done", "korrigiert")
        # Erster Prüfer-Lauf: der Fall, um den es geht. Ab dem zweiten sauber bestanden,
        # sonst dreht die Gegenprobe zwei volle Runden und prüft nicht mehr, was sie soll.
        return rev if laeufe.count("code_reviewer") == 1 else RunResult("done", "<review-ok/>")

    async def fake_diff(_ctx):
        return diff

    async def fake_load_agent(_db, role, *a, **k):
        class A:
            pass
        a2 = A()
        a2.role = role
        return a2

    async def fake_flag(_name, *a, **k):
        return False

    # `get_flag` ist im Worker beim Import gebunden — der autouse-Stub ersetzt nur
    # `app.core.redis`, nicht dieses Modul. Ohne das liefe der Test in echten Redis.
    monkeypatch.setattr(worker, "get_flag", fake_flag)
    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker.gitops, "diff_text", fake_diff)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    ergebnis = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})
    return ergebnis, laeufe, issue


async def test_abgebrochener_pruefer_erzeugt_keinen_auftrag(db, monkeypatch):
    ergebnis, laeufe, issue = await _gate(
        db, monkeypatch, RunResult("failed", "claude: Antwort bei max_tokens abgeschnitten"))

    assert laeufe == ["code_reviewer"], "der Entwickler wurde auf einen Phantom-Befund angesetzt"
    assert ergebnis.status == "done"
    from app.models.ticket import Comment
    from sqlalchemy import select
    texte = [c.body for c in (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id))).scalars().all()]
    assert any("UNGEPRÜFT" in t for t in texte), "der Mensch erfährt nicht, dass niemand prüfte"


async def test_echte_befunde_loesen_weiter_eine_korrektur_aus(db, monkeypatch):
    """Die Gegenprobe: ein Prüfer, der SAUBER durchläuft und etwas findet, schickt den
    Entwickler los wie bisher."""
    ergebnis, laeufe, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. foo.ts:12 — Nullprüfung fehlt"))
    # Befund → Korrektur → erneute Prüfung, die diesmal besteht. Genau diese Kette darf der
    # abgebrochene Prüfer NICHT auslösen.
    assert laeufe == ["code_reviewer", "developer", "code_reviewer"]
    assert ergebnis.status == "done"


async def test_bestandener_review_laesst_alles_stehen(db, monkeypatch):
    ergebnis, laeufe, _ = await _gate(db, monkeypatch, RunResult("done", "<review-ok/>"))
    assert laeufe == ["code_reviewer"]
    assert ergebnis.text == "fertig"
