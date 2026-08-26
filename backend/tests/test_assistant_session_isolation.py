"""A conversation is a boundary, or it is nothing.

Three ways lead to the same assistant: the browser, the messenger and the plugin in the
note-taking program. Each of them opens a conversation of its own, and what is said in one
has no business appearing in another. The occasion was a screen showing all three at once,
one below the other, looking like a single thread.

The second half is patience: background work is patient, a person typing is not. A burst of
incoming mail must not push a conversation out of the way.
"""
import pytest
from app.models.assistant import AssistantSession, AssistantTask
from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _session(db, user, title):
    s = AssistantSession(owner_user_id=user.id, agent="assistent", title=title)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _say(db, user, session, text):
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", title=text,
                      status="done", result="Ja.", session_id=session.id,
                      meta={"chat_text": text})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def test_a_page_holds_one_conversation_only(db, client):
    anna = await make_user(db, "anna")
    one = await _session(db, anna, "Rechnungen")
    two = await _session(db, anna, "Termin")
    await _say(db, anna, one, "wo sind die rechnungen")
    await _say(db, anna, two, "wann ist der termin")

    r = await client.get(f"/assistant/chat?session_id={one.id}", headers=auth(anna))
    texts = [m["text"] for m in r.json()["messages"]]
    assert texts == ["wo sind die rechnungen"]


async def test_without_a_named_session_the_newest_one_comes_back(db, client):
    """Not everything. A client that has not chosen yet, which is every client on its first
    request, used to get three conversations in one window."""
    anna = await make_user(db, "anna")
    old = await _session(db, anna, "Rechnungen")
    fresh = await _session(db, anna, "Termin")
    await _say(db, anna, old, "wo sind die rechnungen")
    await _say(db, anna, fresh, "wann ist der termin")

    r = await client.get("/assistant/chat", headers=auth(anna))
    texts = [m["text"] for m in r.json()["messages"]]
    assert texts == ["wann ist der termin"]


async def test_a_conversation_of_somebody_else_stays_theirs(db, client):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    mine = await _session(db, anna, "Meins")
    theirs = await _session(db, bert, "Ihres")
    await _say(db, anna, mine, "meine frage")
    await _say(db, bert, theirs, "ihre frage")

    r = await client.get("/assistant/chat", headers=auth(anna))
    assert [m["text"] for m in r.json()["messages"]] == ["meine frage"]


async def test_a_conversation_is_queued_into_its_own_lane(db, client, monkeypatch):
    """Background work is patient, a person typing is not.

    Five mails arriving at once took every slot the worker had, and a message typed into the
    chat waited behind them. From where the person sat, their conversation had stopped.
    """
    from app.core import redis as redismod

    queued = []

    async def fake_enqueue(payload):
        queued.append(payload)

    monkeypatch.setattr(redismod, "enqueue_task", fake_enqueue)
    anna = await make_user(db, "anna")
    r = await client.post("/assistant/chat", headers=auth(anna), json={"text": "hallo"})
    assert r.status_code in (200, 201), r.text
    assert queued and queued[0]["is_chat"] is True


async def test_a_mail_item_stays_in_the_patient_lane(db, monkeypatch):
    """Otherwise the lane kept free for people fills up with background work."""
    from app.core import redis as redismod
    from app.services.assistant_inbox import approve_assistant_task

    queued = []

    async def fake_enqueue(payload):
        queued.append(payload)

    monkeypatch.setattr(redismod, "enqueue_task", fake_enqueue)
    anna = await make_user(db, "anna")
    t = AssistantTask(owner_user_id=anna.id, kind="email", source="webhook",
                      title="Eine Rechnung", status="new", meta={})
    db.add(t)
    await db.commit()

    await approve_assistant_task(db, t)
    assert queued and queued[0]["is_chat"] is False
