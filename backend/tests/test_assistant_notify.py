"""When the personal assistant speaks up, and when it stays silent.

The occasion: a finished "nothing to do" (an AliExpress receipt confirmation) arrived as a
Telegram message although the assistant wrote in the text itself that it was not reporting.
The reason was the unconditional closing report. Since then the rule is: the run itself is
no message; reporting happens over `traccoon_notify_human`, on mishaps and in the chat.
"""
import pytest
from app.models.assistant import AssistantTask
from app.models.notification import Notification
from app.models.user import User
from app.worker import __main__ as worker
from conftest import make_user
from sqlalchemy import select


async def _lauf(db, monkeypatch, *, owner: User, kind: str = "email",
                status: str = "done", meldet: bool = False, titel: str = "Bestellung 123",
                blocker_kind: str | None = None):
    """Let an assistant item run through; `meldet` = the agent calls notify_human."""
    t = AssistantTask(owner_user_id=owner.id, kind=kind, title=titel, status="approved",
                      redacted_summary="Zusammenfassung", meta={"chat_text": "Wie spät?"})
    db.add(t)
    await db.commit()
    await db.refresh(t)

    class Ergebnis:
        def __init__(self):
            self.status = {"done": "done", "error": "failed"}.get(status, status)
            # ask_human delivers the question as `text`, without a summary.
            self.text = ("Ticket oder API-Freigabe?" if self.status == "blocked"
                         else "Erledigt. Kein Statuswechsel, keine Telegram-Nachricht.")
            self.summary = "" if self.status == "blocked" else self.text
            self.run_id = None
            self.blocker_kind = blocker_kind

    async def fake_run_agent(**kwargs):
        if meldet:
            # This is how `traccoon_notify_human` would act: its own message plus a note on the item.
            db.add(Notification(user_id=owner.id, kind="assistant", title="Frist am Freitag",
                                body="Rechnung 240 € fällig", chat_id=owner.telegram_chat_id))
            obj = await db.get(AssistantTask, t.id)
            obj.notified = True
            await db.commit()
        return Ergebnis()

    async def fake_load_agent(*a, **kw):
        class A:
            role = "assistent"
            name = "assistent"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    # run_agent is imported in the body, so replace it there as well.
    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)

    await worker._handle_assistant_task(
        {"assistant_task_id": t.id, "task_id": f"assistant-{t.id}"}, None)
    return t


async def _nachrichten(db) -> list[Notification]:
    return list((await db.execute(select(Notification))).scalars().all())


@pytest.fixture
async def owner(db):
    u = await make_user(db, "anna")
    u.telegram_chat_id = "123"
    await db.commit()
    return u


async def test_erledigtes_ohne_handlungsbedarf_bleibt_still(db, owner, monkeypatch):
    """The case from practice: filed, nothing to do, so no message."""
    await _lauf(db, monkeypatch, owner=owner)
    assert await _nachrichten(db) == []


async def test_ausdrueckliche_meldung_kommt_an_und_zwar_einmal(db, owner, monkeypatch):
    """If the assistant reports itself, EXACTLY its message goes out, not the closing report
    in addition (because otherwise the same thing would come twice)."""
    await _lauf(db, monkeypatch, owner=owner, meldet=True)
    n = await _nachrichten(db)
    assert len(n) == 1
    assert n[0].title == "Frist am Freitag"


async def test_panne_meldet_sich_immer(db, owner, monkeypatch):
    await _lauf(db, monkeypatch, owner=owner, status="error")
    n = await _nachrichten(db)
    assert len(n) == 1 and "Fehler" in n[0].title


async def test_chat_wird_immer_beantwortet(db, owner, monkeypatch):
    """A question asked is a question one wants answered, even with "not at all"."""
    owner.assistant_notify = "never"
    await db.commit()
    await _lauf(db, monkeypatch, owner=owner, kind="chat")
    n = await _nachrichten(db)
    assert len(n) == 1 and n[0].title.startswith("🤖 Assistent")


async def test_modus_immer_meldet_auch_erledigtes(db, owner, monkeypatch):
    owner.assistant_notify = "always"
    await db.commit()
    await _lauf(db, monkeypatch, owner=owner)
    assert len(await _nachrichten(db)) == 1


async def test_modus_gar_nicht_schweigt_auch_bei_pannen(db, owner, monkeypatch):
    owner.assistant_notify = "never"
    await db.commit()
    await _lauf(db, monkeypatch, owner=owner, status="error")
    assert await _nachrichten(db) == []


async def test_rueckfrage_im_chat_kommt_an(db, owner, monkeypatch):
    """The occasion: `ask_human` ended as 'blocked' and was misread as a tool gate; the
    question disappeared silently and the human saw only an eternal 'running'."""
    t = await _lauf(db, monkeypatch, owner=owner, kind="chat", status="blocked",
                    blocker_kind="ask_human")
    n = await _nachrichten(db)
    assert len(n) == 1 and "Ticket oder API-Freigabe?" in n[0].body
    await db.refresh(t)
    # Finished, not 'running'; otherwise the exchange is missing in the history later.
    assert t.status == "done" and t.result == "Ticket oder API-Freigabe?"


async def test_rueckfrage_ohne_chat_meldet_trotz_modus_bedarf(db, owner, monkeypatch):
    """Outside the chat as well (mail inbox): a question without a recipient is pointless."""
    owner.assistant_notify = "needed"
    await db.commit()
    await _lauf(db, monkeypatch, owner=owner, status="blocked", blocker_kind="ask_human")
    n = await _nachrichten(db)
    assert len(n) == 1 and "Rückfrage" in n[0].title


async def test_tool_freigabe_bleibt_still_und_offen(db, owner, monkeypatch):
    """The counterpart: with the tool gate the item waits for the approval card; it must be
    neither finalised nor reported twice."""
    t = await _lauf(db, monkeypatch, owner=owner, status="blocked",
                    blocker_kind="assistant_perm")
    assert await _nachrichten(db) == []
    await db.refresh(t)
    assert t.status == "running"


async def test_meldewerkzeug_braucht_keine_freigabe(db, owner):
    """Without this exception a missing allowlist approval would mean "never reports", and
    then important things would stay mute as well."""
    from app.worker.runtime import _ALWAYS_ALLOWED
    assert "traccoon_notify_human" in _ALWAYS_ALLOWED
