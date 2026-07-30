"""Eine Antwort im Telegram-Chat ist eine Chat-Nachricht — keine Sackgasse.

Anlass: Der Reply-Handler war für Ticket-Kommentare gedacht und griff bei JEDER Antwort.
Antwortete Dennis auf eine Nachricht des Assistenten (im Chat das Naheliegendste), fand der
Handler keinen `[ABC-1]`-Schlüssel im Bezugstext und kehrte still zurück: keine Aufgabe, keine
Rückmeldung. Von außen war das nicht von „der Assistent ignoriert mich" zu unterscheiden.
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


async def test_chat_auftrag_wird_angelegt_und_eingereiht(db, anna, monkeypatch):
    eingereiht = []

    async def fake_enqueue(payload):
        eingereiht.append(payload)

    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", fake_enqueue)

    t = await create_chat_task(db, anna.id, "was liegt heute an?", "277")
    assert t.kind == "chat" and t.status == "approved"
    assert t.meta["chat_text"] == "was liegt heute an?" and t.meta["chat_id"] == "277"
    assert "agent" not in t.meta            # ohne Angabe übernimmt der Assistent
    assert eingereiht == [{"kind": "assistant", "task_id": f"assistant-{t.id}",
                           "assistant_task_id": t.id}]


async def test_eigener_agent_landet_im_meta(db, anna, monkeypatch):
    """/gameproj geht denselben Weg — der Agent steht im meta, sonst läuft der Assistent."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    t = await create_chat_task(db, anna.id, "neue baurunde", "277", agent="gameproj-operator")
    assert t.meta["agent"] == "gameproj-operator"


async def _nichts():
    return None


async def test_langer_text_kuerzt_nur_den_titel(db, anna, monkeypatch):
    """Der Titel ist auf 200 Zeichen begrenzt — der Auftrag selbst darf nicht beschnitten
    werden, sonst arbeitet der Assistent an einer abgeschnittenen Frage."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    lang = "x" * 500
    t = await create_chat_task(db, anna.id, lang, "277")
    assert len(t.title) == 200
    assert t.meta["chat_text"] == lang
    rows = (await db.execute(select(AssistantTask))).scalars().all()
    assert len(rows) == 1
