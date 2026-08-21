"""Warum das Postfach schnell aufgeht — und woran es ehrlich bleibt.

Gemessen kostete der Aufbau 1,9 Sekunden: 266 ms allein fürs Anmelden (bei JEDEM Aufruf) und
900 ms für einen STATUS je Ordner. Beides ist vermeidbar, aber nur mit zwei Zusagen: Eine
Verbindung, die einen Fehler gesehen hat, wird nicht weitergereicht, und was sich geändert
hat, kommt nicht mehr aus dem Cache.
"""
import pytest
from app.services import mailbox, mailbox_cache as cache




class FakeClient:
    """Ein Postfach, das mitzählt, wie oft man es tatsächlich gefragt hat."""
    def __init__(self, broken_at_noop: bool = False):
        self.noops = 0
        self.closed = False
        self.broken_at_noop = broken_at_noop

    def noop(self):
        self.noops += 1
        if self.broken_at_noop:
            raise OSError("Verbindung weg")

    def logout(self):
        self.closed = True


class Account:
    id = 4711


@pytest.fixture(autouse=True)
def empty_pool():
    mailbox.pool_empty()
    yield
    mailbox.pool_empty()


def test_the_connection_is_reused(monkeypatch):
    """Anmelden kostet einen TLS-Handschlag; bei jedem Aufruf wäre das die halbe Wartezeit."""
    built = []
    monkeypatch.setattr(mailbox, "_join", lambda a: built.append(FakeClient()) or built[-1])

    with mailbox._imap(Account()) as first:
        pass
    with mailbox._imap(Account()) as second:
        pass

    assert len(built) == 1 and first is second
    assert first.noops == 1, "die liegende Verbindung wird angetippt, bevor sie jemand bekommt"


def test_a_dead_connection_is_replaced(monkeypatch):
    """Server trennen nach ein paar Minuten Ruhe. Das darf niemand mitten in einer Antwort
    erfahren."""
    built = []

    def join(_a):
        # Die erste ist tot, die zweite lebt.
        client = FakeClient(broken_at_noop=not built)
        built.append(client)
        return client

    monkeypatch.setattr(mailbox, "_join", join)
    with mailbox._imap(Account()):
        pass
    with mailbox._imap(Account()) as second:
        pass

    assert len(built) == 2 and built[0].closed
    assert second is built[1]


def test_after_an_error_it_is_not_put_back(monkeypatch):
    """Nach einem Abbruch ist der Zustand unklar (halb gelesene Antwort). Eine solche
    zurückzulegen hieße, den Fehler an den nächsten Aufruf weiterzureichen."""
    built = []
    monkeypatch.setattr(mailbox, "_join", lambda a: built.append(FakeClient()) or built[-1])

    with pytest.raises(ValueError):
        with mailbox._imap(Account()):
            raise ValueError("mittendrin")
    assert built[0].closed

    with mailbox._imap(Account()) as fresh:
        pass
    assert fresh is built[1]


# ── Der Cache ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_question_is_asked_only_once(redis_stub_real):
    ask = []

    async def fetch():
        ask.append(1)
        return {"stand": len(ask)}

    first = await cache.cached(1, "folders:1", 60, fetch)
    second = await cache.cached(1, "folders:1", 60, fetch)
    assert first == second == {"stand": 1}
    assert len(ask) == 1


@pytest.mark.asyncio
async def test_what_has_changed_does_not_come_from_the_cache(redis_stub_real):
    """Eine gelesene Mail, eine verschobene, eine neue: danach ist der alte Stand falsch."""
    ask = []

    async def fetch():
        ask.append(1)
        return {"stand": len(ask)}

    await cache.cached(1, "unread", 60, fetch)
    await cache.invalidate(1)
    second = await cache.cached(1, "unread", 60, fetch)

    assert second == {"stand": 2}, "nach dem Entwerten wird wieder gefragt"


@pytest.mark.asyncio
async def test_without_redis_everything_runs_as_before(monkeypatch):
    """Der Cache ist eine Bequemlichkeit, keine Bedingung."""
    def broken():
        raise OSError("kein Redis")

    monkeypatch.setattr("app.services.mailbox_cache.get_redis", broken)
    ask = []

    async def fetch():
        ask.append(1)
        return "frisch"

    assert await cache.cached(1, "x", 60, fetch) == "frisch"
    assert await cache.cached(1, "x", 60, fetch) == "frisch"
    assert len(ask) == 2, "ohne Cache wird eben jedes Mal gefragt"
