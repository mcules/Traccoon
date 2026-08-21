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
    def __init__(self, kaputt_bei_noop: bool = False):
        self.noops = 0
        self.geschlossen = False
        self.kaputt_bei_noop = kaputt_bei_noop

    def noop(self):
        self.noops += 1
        if self.kaputt_bei_noop:
            raise OSError("Verbindung weg")

    def logout(self):
        self.geschlossen = True


class Konto:
    id = 4711


@pytest.fixture(autouse=True)
def leerer_pool():
    mailbox.pool_leeren()
    yield
    mailbox.pool_leeren()


def test_the_connection_is_reused(monkeypatch):
    """Anmelden kostet einen TLS-Handschlag; bei jedem Aufruf wäre das die halbe Wartezeit."""
    gebaut = []
    monkeypatch.setattr(mailbox, "_verbinden", lambda a: gebaut.append(FakeClient()) or gebaut[-1])

    with mailbox._imap(Konto()) as first:
        pass
    with mailbox._imap(Konto()) as zweite:
        pass

    assert len(gebaut) == 1 and first is zweite
    assert first.noops == 1, "die liegende Verbindung wird angetippt, bevor sie jemand bekommt"


def test_a_dead_connection_is_replaced(monkeypatch):
    """Server trennen nach ein paar Minuten Ruhe. Das darf niemand mitten in einer Antwort
    erfahren."""
    gebaut = []

    def verbinden(_a):
        # Die erste ist tot, die zweite lebt.
        client = FakeClient(kaputt_bei_noop=not gebaut)
        gebaut.append(client)
        return client

    monkeypatch.setattr(mailbox, "_verbinden", verbinden)
    with mailbox._imap(Konto()):
        pass
    with mailbox._imap(Konto()) as zweite:
        pass

    assert len(gebaut) == 2 and gebaut[0].geschlossen
    assert zweite is gebaut[1]


def test_after_an_error_it_is_not_put_back(monkeypatch):
    """Nach einem Abbruch ist der Zustand unklar (halb gelesene Antwort). Eine solche
    zurückzulegen hieße, den Fehler an den nächsten Aufruf weiterzureichen."""
    gebaut = []
    monkeypatch.setattr(mailbox, "_verbinden", lambda a: gebaut.append(FakeClient()) or gebaut[-1])

    with pytest.raises(ValueError):
        with mailbox._imap(Konto()):
            raise ValueError("mittendrin")
    assert gebaut[0].geschlossen

    with mailbox._imap(Konto()) as frisch:
        pass
    assert frisch is gebaut[1]


# ── Der Cache ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_question_is_asked_only_once(redis_stub_echt):
    fragen = []

    async def fetch():
        fragen.append(1)
        return {"stand": len(fragen)}

    first = await cache.gecacht(1, "folders:1", 60, fetch)
    zweite = await cache.gecacht(1, "folders:1", 60, fetch)
    assert first == zweite == {"stand": 1}
    assert len(fragen) == 1


@pytest.mark.asyncio
async def test_what_has_changed_does_not_come_from_the_cache(redis_stub_echt):
    """Eine gelesene Mail, eine verschobene, eine neue: danach ist der alte Stand falsch."""
    fragen = []

    async def fetch():
        fragen.append(1)
        return {"stand": len(fragen)}

    await cache.gecacht(1, "unread", 60, fetch)
    await cache.entwerten(1)
    zweite = await cache.gecacht(1, "unread", 60, fetch)

    assert zweite == {"stand": 2}, "nach dem Entwerten wird wieder gefragt"


@pytest.mark.asyncio
async def test_without_redis_everything_runs_as_before(monkeypatch):
    """Der Cache ist eine Bequemlichkeit, keine Bedingung."""
    def kaputt():
        raise OSError("kein Redis")

    monkeypatch.setattr("app.services.mailbox_cache.get_redis", kaputt)
    fragen = []

    async def fetch():
        fragen.append(1)
        return "frisch"

    assert await cache.gecacht(1, "x", 60, fetch) == "frisch"
    assert await cache.gecacht(1, "x", 60, fetch) == "frisch"
    assert len(fragen) == 2, "ohne Cache wird eben jedes Mal gefragt"
