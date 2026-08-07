"""Das Review-Gate darf keine Befunde erfinden.

ABC-31 am 2026-08-07: der Prüfer-Lauf starb an „Antwort bei max_tokens abgeschnitten".
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


async def _gate(db, monkeypatch, rev: RunResult, *, diff="--- a\n+++ b\n+x", runden=0):
    _, proj, issue, _ = await _projekt_mit_ticket(db)
    if runden:
        issue.review_rounds = runden
        await db.commit()
    laeufe = []

    async def fake_run_agent(**kw):
        rolle = kw["agent"].role
        laeufe.append(rolle)
        if rolle != "code_reviewer":
            return RunResult("done", "korrigiert")
        # Erster Prüfer-Lauf: der Fall, um den es geht. Ab dem zweiten sauber bestanden,
        # sonst dreht die Gegenprobe zwei volle Runden und prüft nicht mehr, was sie soll.
        return rev if laeufe.count("code_reviewer") == 1 else RunResult("done", "<review-ok/>")

    runden = {"n": 0}

    async def fake_diff(_ctx):
        # Eine Korrektur, die wirkt, verändert den Diff — sonst greift (zu Recht) die
        # Stillstands-Erkennung, und die prüft ein eigener Test.
        if not diff:
            return diff
        runden["n"] += 1
        return f"{diff}\n+runde {runden['n']}\n"

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


async def test_verbrauchte_runden_ueberleben_den_neustart(db, monkeypatch):
    """Der Runden-Zähler gehört ans Ticket, nicht in die Schleife.

    ABC-32 am 2026-08-07: der Worker wurde mitten in Korrektur-Runde 2 neu gestartet, das
    Gate begann wieder bei Runde 1 — prüfen → korrigieren → Neustart → prüfen → korrigieren.
    Die Grenze, die den Menschen holen soll, wurde nie erreicht.
    """
    ergebnis, laeufe, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. Befund"), runden=worker.REVIEW_RUNDEN)

    assert laeufe == [], "verbrauchte Runden dürfen keinen weiteren Lauf starten"
    assert ergebnis.blocker_kind == "review"


async def test_begonnene_runde_wird_sofort_verbucht(db, monkeypatch):
    """Verbucht wird beim Start der Korrektur, nicht bei ihrem Ende — sonst zählt genau die
    Runde nicht, die der Neustart trifft."""
    _, laeufe, issue = await _gate(db, monkeypatch, RunResult("done", "1. Befund"))

    assert "developer" in laeufe
    assert issue.review_rounds >= 1


async def test_offene_befunde_landen_am_ticket(db, monkeypatch):
    """Wer entscheiden soll, braucht den Grund am selben Ort wie die Entscheidung.

    ABC-32 am 2026-08-07: das Gate gab das Ticket nach zwei Runden an den Menschen — am
    Ticket stand „hold: review" und sonst nichts. Die Befunde steckten im Lauf.
    """
    from sqlalchemy import select

    from app.models.ticket import Comment

    ergebnis, _, issue = await _gate(db, monkeypatch, RunResult("done", "1. Der Timeout ist zu kurz."),
                                     runden=worker.REVIEW_RUNDEN - 1)

    assert ergebnis.blocker_kind == "review"
    texte = [c.body for c in (await db.execute(
        select(Comment).where(Comment.issue_id == issue.id))).scalars().all()]
    assert any("Der Timeout ist zu kurz." in t for t in texte), "die Befunde fehlen am Ticket"


async def test_stillstand_beendet_das_gate_statt_der_rundenzahl(db, monkeypatch):
    """Die Grenze ist Stillstand, nicht eine Zahl.

    Ein Ticket soll durchlaufen, solange es vorankommt. Erst wenn eine Korrektur am Code
    nichts mehr ändert, bringen weitere Runden nichts — dann (und nur dann) der Mensch.
    """
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _projekt_mit_ticket(db)
    laeufe = []

    async def fake_run_agent(**kw):
        laeufe.append(kw["agent"].role)
        # Der Prüfer findet immer etwas, der Entwickler ändert nie etwas → Stillstand.
        return (RunResult("done", "1. Immer derselbe Befund") if kw["agent"].role == "code_reviewer"
                else RunResult("done", "nichts geändert"))

    async def fake_diff(_ctx):
        return "--- a\n+++ b\n+unveraendert\n"      # bleibt über alle Runden gleich

    async def fake_load_agent(_db, role, *a, **k):
        class A:
            pass
        x = A()
        x.role = role
        return x

    async def fake_flag(_name, *a, **k):
        return False

    monkeypatch.setattr(worker, "get_flag", fake_flag)
    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker.gitops, "diff_text", fake_diff)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)

    ergebnis = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})

    assert ergebnis.blocker_kind == "review"
    assert "Stillstand" in (ergebnis.text or "")
    # Prüfen → korrigieren → der Diff ist unverändert → Schluss. NICHT erst nach
    # REVIEW_RUNDEN, und keine zweite Prüfung auf demselben Stand.
    assert laeufe.count("code_reviewer") == 1 < worker.REVIEW_RUNDEN


async def test_fortschritt_darf_weiterlaufen(db, monkeypatch):
    """Gegenprobe: solange sich der Diff ändert, läuft das Gate weiter — bis es besteht."""
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _projekt_mit_ticket(db)
    runde = {"n": 0}

    async def fake_run_agent(**kw):
        if kw["agent"].role != "code_reviewer":
            return RunResult("done", "korrigiert")
        runde["n"] += 1
        # Erst in Runde 4 zufrieden — deutlich mehr als die früheren zwei.
        return RunResult("done", "<review-ok/>" if runde["n"] >= 4 else f"{runde['n']}. Befund")

    async def fake_diff(_ctx):
        return f"--- a\n+++ b\n+stand {runde['n']}\n"   # ändert sich jede Runde

    async def fake_load_agent(_db, role, *a, **k):
        class A:
            pass
        x = A()
        x.role = role
        return x

    async def fake_flag(_name, *a, **k):
        return False

    monkeypatch.setattr(worker, "get_flag", fake_flag)
    monkeypatch.setattr(worker, "run_agent", fake_run_agent)
    monkeypatch.setattr(worker.gitops, "diff_text", fake_diff)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)

    ergebnis = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})

    assert ergebnis.blocker_kind is None, "bestandenes Review darf nicht blockieren"
    assert runde["n"] == 4
