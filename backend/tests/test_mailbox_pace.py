"""Why the mailbox opens fast — and what keeps it honest.

Measured, building it cost 1.9 seconds: 266 ms for logging in alone (on EVERY call) and 900 ms
for one STATUS per folder. Both are avoidable, but only with two promises: a connection that
has seen an error is not passed on, and what has changed does not come from the cache any
more.
"""
import pytest
from app.services import mailbox, mailbox_cache as cache




class FakeClient:
    """A mailbox that counts how often it has actually been asked."""
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
    """Logging in costs a TLS handshake; on every call that would be half the waiting time."""
    built = []
    monkeypatch.setattr(mailbox, "_join", lambda a: built.append(FakeClient()) or built[-1])

    with mailbox._imap(Account()) as first:
        pass
    with mailbox._imap(Account()) as second:
        pass

    assert len(built) == 1 and first is second
    assert first.noops == 1, "die liegende Verbindung wird angetippt, bevor sie jemand bekommt"


def test_a_dead_connection_is_replaced(monkeypatch):
    """Servers disconnect after a few minutes of silence. Nobody may hit that in the middle of
    erfahren."""
    built = []

    def join(_a):
        # The first one is dead, the second alive.
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
    """After an abort the state is unclear (a half-read answer). Putting one like that back
    would mean passing the error on to the next call."""
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
    """One mail read, one moved, one new: afterwards the old state is wrong."""
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
    """The cache is a convenience, not a condition."""
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
