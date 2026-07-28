"""Wann der persönliche Assistent sich meldet — und wann er schweigt.

Anlass: ein erledigtes „nichts zu tun" (AliExpress-Empfangsbestätigung) kam als
Telegram-Nachricht an, obwohl der Assistent im Text selbst schrieb, er melde nicht. Grund
war der bedingungslose Abschlussbericht. Seither gilt: der Lauf selbst ist keine Nachricht;
gemeldet wird über `traccoon_notify_human`, bei Pannen und im Chat.
"""
import pytest
from app.models.assistant import AssistantTask
from app.models.notification import Notification
from app.models.user import User
from app.worker import __main__ as worker
from conftest import make_user
from sqlalchemy import select


async def _lauf(db, monkeypatch, *, owner: User, kind: str = "email",
                status: str = "done", meldet: bool = False, titel: str = "Bestellung 123"):
    """Ein Assistenten-Item durchlaufen lassen; `meldet` = der Agent ruft notify_human."""
    t = AssistantTask(owner_user_id=owner.id, kind=kind, title=titel, status="approved",
                      redacted_summary="Zusammenfassung", meta={"chat_text": "Wie spät?"})
    db.add(t)
    await db.commit()
    await db.refresh(t)

    class Ergebnis:
        def __init__(self):
            self.status = "done" if status == "done" else "failed"
            self.text = "Erledigt. Kein Statuswechsel, keine Telegram-Nachricht."
            self.summary = self.text
            self.run_id = None

    async def fake_run_agent(**kwargs):
        if meldet:
            # So würde `traccoon_notify_human` wirken: eigene Nachricht + Vermerk am Item.
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
    # run_agent wird im Rumpf importiert — dort ebenfalls ersetzen.
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
    """Der Fall aus der Praxis: abgelegt, nichts zu tun → keine Nachricht."""
    await _lauf(db, monkeypatch, owner=owner)
    assert await _nachrichten(db) == []


async def test_ausdrueckliche_meldung_kommt_an_und_zwar_einmal(db, owner, monkeypatch):
    """Meldet der Assistent selbst, geht GENAU seine Meldung raus — nicht zusätzlich der
    Abschlussbericht (sonst käme dieselbe Sache zweimal)."""
    await _lauf(db, monkeypatch, owner=owner, meldet=True)
    n = await _nachrichten(db)
    assert len(n) == 1
    assert n[0].title == "Frist am Freitag"


async def test_panne_meldet_sich_immer(db, owner, monkeypatch):
    await _lauf(db, monkeypatch, owner=owner, status="error")
    n = await _nachrichten(db)
    assert len(n) == 1 and "Fehler" in n[0].title


async def test_chat_wird_immer_beantwortet(db, owner, monkeypatch):
    """Eine gestellte Frage will man beantwortet haben — auch bei „gar nicht"."""
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


async def test_meldewerkzeug_braucht_keine_freigabe(db, owner):
    """Ohne diese Ausnahme hieße eine fehlende Allowlist-Freigabe „meldet nie" — dann
    bliebe auch Wichtiges stumm."""
    from app.worker.runtime import _ALWAYS_ALLOWED
    assert "traccoon_notify_human" in _ALWAYS_ALLOWED
