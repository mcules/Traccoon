"""The review gate must not invent findings.

TRA-31 on 2026-08-07: the reviewer run died on "answer truncated at max_tokens". The gate
only checked whether `<review-ok/>` stood in the text, and if it did not, the text counted
as a list of findings. The developer was then sent off to "fix" a provider error message: an
invented task that costs one of the two correction rounds and afterwards ends in the review hold.
"""
import app.worker.__main__ as worker
from app.worker.runtime import RunResult
from test_lifecycle_process import _project_with_ticket


class _Ctx:
    pass


async def _gate(db, monkeypatch, rev: RunResult, *, diff="--- a\n+++ b\n+x", rounds=0):
    _, proj, issue, _ = await _project_with_ticket(db)
    if rounds:
        issue.review_rounds = rounds
        await db.commit()
    runs = []

    async def fake_run_agent(**kw):
        role_name = kw["agent"].role
        runs.append(role_name)
        if role_name != "code_reviewer":
            return RunResult("done", "korrigiert")
        # First reviewer run: the case it is all about. From the second on it passes cleanly,
        # because otherwise the counter-check turns two full rounds and no longer checks what it should.
        return rev if runs.count("code_reviewer") == 1 else RunResult("done", "<review-ok/>")

    rounds = {"n": 0}

    async def fake_diff(_ctx):
        # A correction that takes effect changes the diff; otherwise the standstill detection
        # (rightly) takes hold, and that has a test of its own.
        if not diff:
            return diff
        rounds["n"] += 1
        return f"{diff}\n+runde {rounds['n']}\n"

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
    result = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})
    return result, runs, issue


async def test_an_aborted_reviewer_produces_no_task(db, monkeypatch):
    result, runs, issue = await _gate(
        db, monkeypatch, RunResult("failed", "claude: Antwort bei max_tokens abgeschnitten"))

    assert runs == ["code_reviewer"], "the developer was set on a phantom finding"
    assert result.status == "done"
    from app.models.ticket import Comment
    from sqlalchemy import select
    texts = [c.body for c in (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id))).scalars().all()]
    assert any("UNGEPRÜFT" in t for t in texts), "the human does not learn that nobody checked"


async def test_real_findings_still_trigger_a_correction(db, monkeypatch):
    """The counter-check: a reviewer that runs through CLEANLY and finds something sends the
    developer off as before."""
    result, runs, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. foo.ts:12 — Nullprüfung fehlt"))
    # Finding, correction, renewed check that passes this time. Exactly this chain must NOT
    # be triggered by the aborted reviewer.
    assert runs == ["code_reviewer", "developer", "code_reviewer"]
    assert result.status == "done"


async def test_a_passed_review_leaves_everything_standing(db, monkeypatch):
    result, runs, _ = await _gate(db, monkeypatch, RunResult("done", "<review-ok/>"))
    assert runs == ["code_reviewer"]
    assert result.text == "fertig"


async def test_used_rounds_survive_the_restart(db, monkeypatch):
    """The round counter belongs on the ticket, not in the loop.

    TRA-32 on 2026-08-07: the worker was restarted in the middle of correction round 2, and
    the gate began at round 1 again: check, correct, restart, check, correct. The limit that
    is supposed to fetch the human was never reached.
    """
    result, runs, _ = await _gate(
        db, monkeypatch, RunResult("done", "1. Befund"), rounds=worker.REVIEW_ROUNDS)

    assert runs == [], "used up rounds must not start another run"
    assert result.blocker_kind == "review"


async def test_a_started_round_is_booked_at_once(db, monkeypatch):
    """Booking happens at the start of the correction, not at its end; otherwise exactly the
    round the restart hits does not count."""
    _, runs, issue = await _gate(db, monkeypatch, RunResult("done", "1. Befund"))

    assert "developer" in runs
    assert issue.review_rounds >= 1


async def test_open_findings_land_on_the_ticket(db, monkeypatch):
    """Whoever has to decide needs the reason in the same place as the decision.

    TRA-32 on 2026-08-07: the gate handed the ticket to the human after two rounds, and on
    the ticket stood "hold: review" and nothing else. The findings sat in the run.
    """
    from sqlalchemy import select

    from app.models.ticket import Comment

    result, _, issue = await _gate(db, monkeypatch, RunResult("done", "1. Der Timeout ist zu kurz."),
                                     rounds=worker.REVIEW_ROUNDS - 1)

    assert result.blocker_kind == "review"
    texts = [c.body for c in (await db.execute(
        select(Comment).where(Comment.issue_id == issue.id))).scalars().all()]
    assert any("Der Timeout ist zu kurz." in t for t in texts), "the findings are missing on the ticket"


async def test_standstill_ends_the_gate_rather_than_the_round_count(db, monkeypatch):
    """The limit is standstill, not a number.

    A ticket should run as long as it makes progress. Only when a correction changes nothing
    in the code any more do further rounds bring nothing, and then (and only then) the human.
    """
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _project_with_ticket(db)
    runs = []

    async def fake_run_agent(**kw):
        runs.append(kw["agent"].role)
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

    result = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})

    assert result.blocker_kind == "review"
    assert "Stillstand" in (result.text or "")
    # Check, correct, the diff is unchanged, stop. NOT only after REVIEW_RUNDEN, and no
    # second check on the same state.
    assert runs.count("code_reviewer") == 1 < worker.REVIEW_ROUNDS


async def test_progress_may_carry_on(db, monkeypatch):
    """Counter-check: as long as the diff changes, the gate runs on, until it passes."""
    from app.worker.runtime import RunResult

    _, proj, issue, _ = await _project_with_ticket(db)
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

    result = await worker._review_gate(
        db, proj, issue, await fake_load_agent(db, "developer"), "/ws", False, {}, [],
        RunResult("done", "fertig"), _Ctx(), owner_id=None, task_id="t-1", base_urls={})

    assert result.blocker_kind is None, "a passed review must not block"
    assert runde["n"] == 4


async def test_breakdown_notices_do_not_reach_the_prompt(db):
    """An error message of the infrastructure is not a work assignment.

    On 2026-08-07 an agent read "❌ failed: claude: answer truncated at max_tokens, raise
    max_tokens" in the comment history of its ticket, took that for its task and built an
    escalation into the provider router, in a ticket about a failing job. Such messages stay
    visible in the ticket but out of the prompt.
    """
    from app.models.ticket import Comment
    from app.services.workflow_engine import _agent_note

    _, _, issue, _ = await _project_with_ticket(db)
    await _agent_note(db, issue.id, "failed", "Worker-Neustart: der Lauf war nicht zu Ende", False)
    await _agent_note(db, issue.id, "loop_exhausted", "Erkenntnisse: der Job-Pfad hat eine Wanduhr", False)
    await db.commit()

    from sqlalchemy import select
    rows = (await db.execute(select(Comment).where(Comment.issue_id == issue.id))).scalars().all()
    kinds = {c.kind: c.body for c in rows}

    assert "agent_fail" in kinds and "Worker-Neustart" in kinds["agent_fail"]
    assert "agent" in kinds and "Erkenntnisse" in kinds["agent"]
    # The prompt history filters exactly on `kind == "agent"` (see worker/__main__.py).
    history = [c.body for c in rows if c.kind == "agent"]
    assert not any("Worker-Neustart" in b for b in history)
    assert any("Erkenntnisse" in b for b in history)
