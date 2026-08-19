"""The review gate must not invent findings.

TRA-31 on 2026-08-07: the reviewer run died on "answer truncated at max_tokens". The gate
only checked whether `<review-ok/>` stood in the text, and if it did not, the text counted
as a list of findings. The developer was then sent off to "fix" a provider error message: an
invented task that costs one of the two correction rounds and afterwards ends in the review hold.
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
        # First reviewer run: the case it is all about. From the second on it passes cleanly,
        # because otherwise the counter-check turns two full rounds and no longer checks what it should.
        return rev if laeufe.count("code_reviewer") == 1 else RunResult("done", "<review-ok/>")

    runden = {"n": 0}

    async def fake_diff(_ctx):
        # A correction that takes effect changes the diff; otherwise the standstill detection
        # (rightly) takes hold, and that has a test of its own.
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

    # `get_flag` is bound in the worker at import time, and the autouse stub only replaces
    # `app.core.redis`, not this module. Without that the test would run into a real Redis.
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
    """The counter-check: a reviewer that runs through CLEANLY and finds something sends the
    developer off as before."""
    ergebnis, laeufe, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. foo.ts:12 — Nullprüfung fehlt"))
    # Finding, correction, renewed check that passes this time. Exactly this chain must NOT
    # be triggered by the aborted reviewer.
    assert laeufe == ["code_reviewer", "developer", "code_reviewer"]
    assert ergebnis.status == "done"


async def test_bestandener_review_laesst_alles_stehen(db, monkeypatch):
    ergebnis, laeufe, _ = await _gate(db, monkeypatch, RunResult("done", "<review-ok/>"))
    assert laeufe == ["code_reviewer"]
    assert ergebnis.text == "fertig"


async def test_verbrauchte_runden_ueberleben_den_neustart(db, monkeypatch):
    """The round counter belongs on the ticket, not in the loop.

    TRA-32 on 2026-08-07: the worker was restarted in the middle of correction round 2, and
    the gate began at round 1 again: check, correct, restart, check, correct. The limit that
    is supposed to fetch the human was never reached.
    """
    ergebnis, laeufe, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. Befund"), runden=worker.REVIEW_RUNDEN)

    assert laeufe == [], "verbrauchte Runden dürfen keinen weiteren Lauf starten"
    assert ergebnis.blocker_kind == "review"


async def test_begonnene_runde_wird_sofort_verbucht(db, monkeypatch):
    """Booking happens at the start of the correction, not at its end; otherwise exactly the
    round the restart hits does not count."""
    _, laeufe, issue = await _gate(db, monkeypatch, RunResult("done", "1. Befund"))

    assert "developer" in laeufe
    assert issue.review_rounds >= 1


async def test_offene_befunde_landen_am_ticket(db, monkeypatch):
    """Whoever has to decide needs the reason in the same place as the decision.

    TRA-32 on 2026-08-07: the gate handed the ticket to the human after two rounds, and on
    the ticket stood "hold: review" and nothing else. The findings sat in the run.
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
    """The limit is standstill, not a number.

    A ticket should run as long as it makes progress. Only when a correction changes nothing
    in the code any more do further rounds bring nothing, and then (and only then) the human.
    """
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _projekt_mit_ticket(db)
    laeufe = []

    async def fake_run_agent(**kw):
        laeufe.append(kw["agent"].role)
        # The reviewer always finds something, the developer never changes anything: standstill.
        return (RunResult("done", "1. Immer derselbe Befund") if kw["agent"].role == "code_reviewer"
                else RunResult("done", "nichts geändert"))

    async def fake_diff(_ctx):
        return "--- a\n+++ b\n+unveraendert\n"      # stays the same over all rounds

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
    # Check, correct, the diff is unchanged, stop. NOT only after REVIEW_RUNDEN, and no
    # second check on the same state.
    assert laeufe.count("code_reviewer") == 1 < worker.REVIEW_RUNDEN


async def test_fortschritt_darf_weiterlaufen(db, monkeypatch):
    """Counter-check: as long as the diff changes, the gate runs on, until it passes."""
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _projekt_mit_ticket(db)
    runde = {"n": 0}

    async def fake_run_agent(**kw):
        if kw["agent"].role != "code_reviewer":
            return RunResult("done", "korrigiert")
        runde["n"] += 1
        # Satisfied only in round 4, clearly more than the earlier two.
        return RunResult("done", "<review-ok/>" if runde["n"] >= 4 else f"{runde['n']}. Befund")

    async def fake_diff(_ctx):
        return f"--- a\n+++ b\n+stand {runde['n']}\n"   # changes every round

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


async def test_pannenmeldungen_erreichen_den_prompt_nicht(db):
    """An error message of the infrastructure is not a work assignment.

    On 2026-08-07 an agent read "❌ failed: claude: answer truncated at max_tokens, raise
    max_tokens" in the comment history of its ticket, took that for its task and built an
    escalation into the provider router, in a ticket about a failing job. Such messages stay
    visible in the ticket but out of the prompt.
    """
    from app.models.ticket import Comment
    from app.services.workflow_engine import _agent_note

    _, _, issue, _ = await _projekt_mit_ticket(db)
    await _agent_note(db, issue.id, "failed", "Worker-Neustart: der Lauf war nicht zu Ende", False)
    await _agent_note(db, issue.id, "loop_exhausted", "Erkenntnisse: der Job-Pfad hat eine Wanduhr", False)
    await db.commit()

    from sqlalchemy import select
    rows = (await db.execute(select(Comment).where(Comment.issue_id == issue.id))).scalars().all()
    arten = {c.kind: c.body for c in rows}

    assert "agent_fail" in arten and "Worker-Neustart" in arten["agent_fail"]
    assert "agent" in arten and "Erkenntnisse" in arten["agent"]
    # The prompt history filters exactly on `kind == "agent"` (see worker/__main__.py).
    verlauf = [c.body for c in rows if c.kind == "agent"]
    assert not any("Worker-Neustart" in b for b in verlauf)
    assert any("Erkenntnisse" in b for b in verlauf)
