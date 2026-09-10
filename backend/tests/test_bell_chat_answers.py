"""What the bell shows of the assistant's answers.

The bell is not where a conversation is read — the assistant's panel is, and it marks the
conversation read as it opens it. An answer that went nowhere else therefore has exactly one
reason to sit here: nobody has looked at it yet.

Before this, every browser answer stayed for good: nothing had gone out over a messenger, so
`notified_at IS NULL` held for all of them, and the person had read them in the panel long
before the bell heard about it.
"""
import datetime as dt

import pytest
from app.api.notifications import list_notifications, unread_count
from app.models.assistant import AssistantSession, AssistantTask
from app.models.notification import Notification
from conftest import make_user
from sqlalchemy import select


def _now():
    return dt.datetime.now(tz=dt.timezone.utc)


@pytest.fixture
async def owner(db):
    return await make_user(db, "otto")


async def _answer(db, owner, *, read: bool, notified: bool = False) -> Notification:
    """One answer of the assistant in one conversation."""
    spoke = _now()
    s = AssistantSession(owner_user_id=owner.id, agent="assistent", title="Frage",
                         last_message_at=spoke,
                         read_at=spoke + dt.timedelta(seconds=1) if read else None)
    db.add(s)
    await db.flush()
    t = AssistantTask(owner_user_id=owner.id, kind="chat", title="Frage", status="done",
                      session_id=s.id)
    db.add(t)
    await db.flush()
    n = Notification(user_id=owner.id, kind="assistant", title="🤖 Assistent", body="Antwort",
                     assistant_task_id=t.id,
                     notified_at=_now() if notified else None)
    db.add(n)
    await db.commit()
    return n


async def test_an_unread_answer_is_in_the_bell(db, owner):
    n = await _answer(db, owner, read=False)
    rows = await list_notifications(all=False, user=owner, db=db)
    assert [r["id"] for r in rows] == [n.id]
    assert (await unread_count(user=owner, db=db))["count"] == 1


async def test_a_read_answer_is_gone_from_the_bell(db, owner):
    """Read in the panel — the bell has nothing left to say about it."""
    await _answer(db, owner, read=True)
    assert await list_notifications(all=False, user=owner, db=db) == []
    assert (await unread_count(user=owner, db=db))["count"] == 0


async def test_it_stays_findable_in_the_history(db, owner):
    """"Where was that answer again" needs a place to look."""
    n = await _answer(db, owner, read=True)
    rows = await list_notifications(all=True, user=owner, db=db)
    assert [r["id"] for r in rows] == [n.id]


async def test_an_answer_that_went_to_the_messenger_stays_out(db, owner):
    """It has been read there. Repeating it here is what made the bell a second inbox."""
    await _answer(db, owner, read=False, notified=True)
    assert await list_notifications(all=False, user=owner, db=db) == []


async def test_a_report_without_a_conversation_keeps_the_old_rule(db, owner):
    """The assistant also reports about items out of its inbox. Those belong to no
    conversation, so there is nowhere else they could be read."""
    t = AssistantTask(owner_user_id=owner.id, kind="email", title="Rechnung", status="done")
    db.add(t)
    await db.flush()
    n = Notification(user_id=owner.id, kind="assistant", title="🤖 Assistent: Rechnung",
                     body="abgelegt", assistant_task_id=t.id)
    db.add(n)
    await db.commit()

    rows = await list_notifications(all=False, user=owner, db=db)
    assert [r["id"] for r in rows] == [n.id]


async def test_ticked_away_in_the_bell_counts_as_read(db, owner):
    """"Mark all read" writes on the notification, not on the conversation. Without this the
    row could not be cleared here at all and old answers would sit in the bell for good."""
    n = await _answer(db, owner, read=False)
    n.read_at = _now()
    await db.commit()
    assert await list_notifications(all=False, user=owner, db=db) == []


async def test_a_new_answer_does_not_drag_the_older_ones_back(db, owner):
    """Measured against the conversation's unread flag, one new message would fetch every
    older answer of that thread back into the bell. What counts is whether anybody looked
    AFTER this particular answer."""
    old = await _answer(db, owner, read=True)
    s = (await db.execute(
        select(AssistantSession).order_by(AssistantSession.id.desc()).limit(1))).scalar_one()
    t = AssistantTask(owner_user_id=owner.id, kind="chat", title="Nachfrage", status="done",
                      session_id=s.id)
    db.add(t)
    await db.flush()
    fresh = Notification(user_id=owner.id, kind="assistant", title="🤖 Assistent",
                         body="Neue Antwort", assistant_task_id=t.id,
                         created_at=_now() + dt.timedelta(minutes=5))
    db.add(fresh)
    s.last_message_at = _now() + dt.timedelta(minutes=5)
    await db.commit()

    rows = await list_notifications(all=False, user=owner, db=db)
    assert [r["id"] for r in rows] == [fresh.id], "nur die neue Antwort, nicht die gelesene"
    assert old.id not in [r["id"] for r in rows]
