"""A spent budget is continued, not reported as a failure.

The occasion: a chat run hit the iteration limit and the person was told "🤖 Assistant —
Error / Iteration limit reached", followed by the note that 25 messages of their own
conversation could not be summarised and were lost. Two wrong answers to one technical
event. Ticket runs had been doing it right for months through their flow; the chat and the
job path each had an answer of their own.
"""
import pytest
from app.services import continuation
from app.worker import __main__ as worker


class _Result:
    def __init__(self, status, summary="", text=""):
        self.status, self.summary, self.text = status, summary, text
        self.run_id = None
        self.blocker_kind = None


def test_the_ceiling_holds():
    assert continuation.may_continue(0)
    assert continuation.may_continue(continuation.MAX_ROUNDS - 1)
    assert not continuation.may_continue(continuation.MAX_ROUNDS)


def test_the_hint_carries_the_state_and_never_reads_as_a_failure():
    text = continuation.hint("Two files written, the third is open.")
    assert "the third is open" in text
    assert "continuation" in text.lower()
    # Even without a note the next round is told to carry on, not to start over.
    assert continuation.hint("") and "carry on" in continuation.hint("")


def test_the_closing_message_leads_with_what_was_reached():
    note = continuation.paused_note("Three of five done.", 3)
    assert "Three of five done." in note
    assert "error" not in note.lower() and "fail" not in note.lower()


class _Redis:
    """Only what `_handle_agent_free` uses of it."""

    def __init__(self):
        self.reported = {}

    async def set(self, key, value, ex=None):
        import json
        self.reported = json.loads(value)

    async def publish(self, *a):
        return None


@pytest.fixture
def free_run(monkeypatch):
    """A free run whose agent hits the limit a given number of times."""
    async def _load_agent(*a, **kw):
        return object()

    async def _tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "_load_agent", _load_agent)
    monkeypatch.setattr(worker, "_build_tokens", _tokens)

    def build(limits: int, then: str = "done"):
        calls = []

        async def fake_run(**kw):
            calls.append(kw)
            if len(calls) <= limits:
                return _Result("loop_exhausted", summary=f"state {len(calls)}")
            return _Result(then, summary="finished")

        monkeypatch.setattr("app.worker.runtime.run_agent", fake_run)
        return calls

    return build


async def test_a_free_run_is_picked_up_again(db, free_run):
    """A job that runs out of budget used to be handed 'failed', and its work was gone."""
    calls = free_run(limits=2)
    redis = _Redis()
    await worker._handle_agent_free(
        {"task_id": "run-1", "agent": "assistent", "prompt": "do it", "owner_id": 1}, redis)

    assert len(calls) == 3, "two continuations, then the result"
    assert calls[1]["continuation_index"] == 1
    assert "state 1" in calls[1]["continuation_hint"], "the next round knows where it stopped"
    assert redis.reported["status"] == "done" and "finished" in redis.reported["output"]


async def test_a_free_run_stops_at_the_ceiling_and_says_so(db, free_run):
    """Not endlessly: after the ceiling it reports what was reached, not an error."""
    calls = free_run(limits=99)
    redis = _Redis()
    await worker._handle_agent_free(
        {"task_id": "run-2", "agent": "assistent", "prompt": "do it", "owner_id": 1}, redis)

    assert len(calls) == continuation.MAX_ROUNDS + 1
    assert redis.reported["status"] == "done", "a spent budget is no failure"
    assert "Paused" in redis.reported["output"]
    assert "state" in redis.reported["output"], "what was reached is the answer"


async def test_a_chat_run_carries_on_instead_of_reporting_an_error(db, monkeypatch):
    """The case from practice, on the way it actually took.

    A conversation ran into the iteration limit and the person was handed an error card. The
    run has to be picked up instead, and the person must not see any of it.
    """
    from app.models.assistant import AssistantTask
    from app.models.notification import Notification
    from sqlalchemy import select

    from conftest import make_user

    owner = await make_user(db, "anna")
    owner.telegram_chat_id = "123"
    t = AssistantTask(owner_user_id=owner.id, kind="chat", title="Wie spät?",
                      status="approved", meta={"chat_text": "Wie spät?"})
    db.add(t)
    await db.commit()
    await db.refresh(t)

    calls = []

    async def fake_run_agent(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return _Result("loop_exhausted", summary="Half of it is written.")
        return _Result("done", summary="Finished.")

    async def fake_load_agent(*a, **kw):
        class A:
            role = "assistent"
            name = "assistent"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)

    await worker._handle_assistant_task(
        {"assistant_task_id": t.id, "task_id": f"assistant-{t.id}"}, None)

    assert len(calls) == 2, "it carries on instead of stopping"
    assert "Half of it is written." in calls[1]["continuation_hint"]
    await db.refresh(t)
    assert t.status == "done" and "Finished." in (t.result or "")
    # A chat always answers, and the answer is the result — not an error card.
    notes = list((await db.execute(select(Notification))).scalars().all())
    assert notes and "Fehler" not in notes[0].title and "Error" not in notes[0].title
