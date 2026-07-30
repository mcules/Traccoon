"""Eine Antwort im Telegram-Chat ist eine Chat-Nachricht — keine Sackgasse.

Anlass: Der Reply-Handler war für Ticket-Kommentare gedacht und griff bei JEDER Antwort.
Antwortete Dennis auf eine Nachricht des Assistenten (im Chat das Naheliegendste), fand der
Handler keinen `[TRA-1]`-Schlüssel im Bezugstext und kehrte still zurück: keine Aufgabe, keine
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
    """/uniwar geht denselben Weg — der Agent steht im meta, sonst läuft der Assistent."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    t = await create_chat_task(db, anna.id, "neue baurunde", "277", agent="uniwar-operator")
    assert t.meta["agent"] == "uniwar-operator"


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


async def test_antwort_traegt_den_bezug_mit(db, anna, monkeypatch):
    """Eine Antwort meint GENAU die zitierte Nachricht — sonst fehlt „mach das" der Gegenstand."""
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    t = await create_chat_task(db, anna.id, "ja, mach das", "277",
                               bezug="Rücksendung erfasst\nSoll ich die Erstattung überwachen?")
    assert t.meta["bezug_text"].startswith("Rücksendung erfasst")
    assert "bezug_task_id" not in t.meta      # keine passende Benachrichtigung → kein Vorgang


async def test_antwort_findet_den_ursprungsvorgang(db, anna, monkeypatch):
    """Zitierte Assistenten-Nachrichten stammen aus Notifications — darüber führt der Weg
    zurück zum Eingang, an dem weitergearbeitet werden soll."""
    from app.models.notification import Notification
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    eingang = AssistantTask(owner_user_id=anna.id, kind="email", source="mail",
                            title="Rücksendung Bias Tee", status="done",
                            result="Bestellung auf retourniert gesetzt.")
    db.add(eingang)
    await db.commit()
    await db.refresh(eingang)
    db.add(Notification(user_id=anna.id, kind="assistant", title="Rücksendung Bias Tee",
                        body="Bestellung auf retourniert gesetzt.", chat_id="277",
                        assistant_task_id=eingang.id))
    await db.commit()

    t = await create_chat_task(db, anna.id, "und die Erstattung?", "277",
                               bezug="Rücksendung Bias Tee\nBestellung auf retourniert gesetzt.")
    assert t.meta["bezug_task_id"] == eingang.id


async def test_fremder_chat_wird_nicht_verknuepft(db, anna, monkeypatch):
    """Die Benachrichtigung eines anderen Chats darf keinen Bezug stiften."""
    from app.models.notification import Notification
    import app.core.redis as redis_mod
    monkeypatch.setattr(redis_mod, "enqueue_task", lambda payload: _nichts())

    fremd = AssistantTask(owner_user_id=anna.id, kind="email", source="mail",
                          title="Fremde Sache", status="done")
    db.add(fremd)
    await db.commit()
    await db.refresh(fremd)
    db.add(Notification(user_id=anna.id, kind="assistant", title="Fremde Sache",
                        body="…", chat_id="999", assistant_task_id=fremd.id))
    await db.commit()

    t = await create_chat_task(db, anna.id, "hm?", "277", bezug="Fremde Sache\n…")
    assert "bezug_task_id" not in t.meta
