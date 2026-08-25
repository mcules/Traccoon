"""An answer in the Telegram chat is a chat message, not a dead end.

The occasion: the reply handler was meant for ticket comments and took hold on EVERY answer.
If somebody answered a message of the assistant (the most obvious thing in a chat), the
handler found no `[ABC-1]` key in the quoted text and returned silently: no task, no
feedback. From the outside that was indistinguishable from "the assistant ignores me".
"""
import pytest
from app.models.assistant import AssistantTask
from app.services.assistant_inbox import create_chat_task
from sqlalchemy import select

from conftest import make_user


@pytest.fixture
async def anna(db):
    u = await make_user(db, "anna")
    u.telegram_chat_id = "277"
    await db.commit()
    return u


async def test_chat_task_is_created_and_queued(db, anna, monkeypatch):
    queued = []

    async def fake_enqueue(payload):
        queued.append(payload)

    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", fake_enqueue)

    t = await create_chat_task(db, anna.id, "was liegt heute an?", "277")
    assert t.kind == "chat" and t.status == "approved"
    assert t.meta["chat_text"] == "was liegt heute an?" and t.meta["chat_id"] == "277"
    assert "agent" not in t.meta            # without an entry the assistant takes over
    assert queued == [{"kind": "assistant", "task_id": f"assistant-{t.id}",
                           "assistant_task_id": t.id}]


async def test_a_named_agent_lands_in_the_meta(db, anna, monkeypatch):
    """A role command goes the same way: the agent stands in the meta, otherwise the assistant runs."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nothing())

    t = await create_chat_task(db, anna.id, "neue baurunde", "277", agent="game-operator")
    assert t.meta["agent"] == "game-operator"


async def _nothing():
    return None


async def test_long_text_shortens_only_the_title(db, anna, monkeypatch):
    """The title is limited to 200 characters; the assignment itself must not be cut, because
    otherwise the assistant works on a truncated question."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nothing())

    long = "x" * 500
    t = await create_chat_task(db, anna.id, long, "277")
    assert len(t.title) == 200
    assert t.meta["chat_text"] == long
    rows = (await db.execute(select(AssistantTask))).scalars().all()
    assert len(rows) == 1


async def test_the_answer_carries_the_reference_along(db, anna, monkeypatch):
    """An answer means EXACTLY the quoted message; otherwise "do that" lacks its object."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nothing())

    t = await create_chat_task(db, anna.id, "ja, mach das", "277",
                               reference="Rücksendung erfasst\nSoll ich die Erstattung überwachen?")
    assert t.meta["bezug_text"].startswith("Rücksendung erfasst")
    assert "bezug_task_id" not in t.meta      # no matching notification, so no process


async def test_the_answer_finds_the_originating_case(db, anna, monkeypatch):
    """Quoted assistant messages come from notifications, and over them the way leads back to
    the inbox item that should be worked on further."""
    from app.models.notification import Notification
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nothing())

    intake = AssistantTask(owner_user_id=anna.id, kind="email", source="mail",
                            title="Rücksendung Bias Tee", status="done",
                            result="Bestellung auf retourniert gesetzt.")
    db.add(intake)
    await db.commit()
    await db.refresh(intake)
    db.add(Notification(user_id=anna.id, kind="assistant", title="Rücksendung Bias Tee",
                        body="Bestellung auf retourniert gesetzt.", chat_id="277",
                        assistant_task_id=intake.id))
    await db.commit()

    t = await create_chat_task(db, anna.id, "und die Erstattung?", "277",
                               reference="Rücksendung Bias Tee\nBestellung auf retourniert gesetzt.")
    assert t.meta["bezug_task_id"] == intake.id


async def test_a_foreign_chat_is_not_linked(db, anna, monkeypatch):
    """The notification of another chat must not establish a reference."""
    from app.models.notification import Notification
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nothing())

    foreign = AssistantTask(owner_user_id=anna.id, kind="email", source="mail",
                          title="Fremde Sache", status="done")
    db.add(foreign)
    await db.commit()
    await db.refresh(foreign)
    db.add(Notification(user_id=anna.id, kind="assistant", title="Fremde Sache",
                        body="…", chat_id="999", assistant_task_id=foreign.id))
    await db.commit()

    t = await create_chat_task(db, anna.id, "hm?", "277", reference="Fremde Sache\n…")
    assert "bezug_task_id" not in t.meta
