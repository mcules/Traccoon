"""The address book of the To field: harvested from the mailbox, without its junk.

Two things carry the feature. The folder walk has to leave junk, trash and drafts out,
with everything underneath them, because `Trash/2024` is trash, while still telling the
sent folder from the rest. And the suggestion is a ranking across mailbox and vault: the
person one answers stands above the one who only writes, and nothing from junk ever shows.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantContact
from app.models.mail import MailCorrespondent
from app.services import mail_correspondents as mc
from app.services.mail_correspondents import _folder_list, suggest

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


class _Client:
    def __init__(self, folders):
        self._folders = folders

    def list_folders(self):
        return [(tuple(f.encode() for f in flags), b"/", name) for flags, name in self._folders]


class _Account:
    id = 1
    folder_sent, folder_drafts, folder_trash, folder_junk = "Sent", "Drafts", "Trash", "Junk"


async def test_folder_walk_leaves_junk_trash_and_drafts_out_with_their_children():
    client = _Client([
        ((), "INBOX"), ((), "Sent"), ((), "Sent/2023"), ((), "Drafts"),
        (("\\Junk",), "Spam"), ((), "Spam/old"), ((), "Trash"), ((), "Trash/2024"),
        ((), "Archive/2024"), (("\\Noselect",), "Archive"),
    ])
    out = {e["name"]: e["sent"] for e in _folder_list(client, _Account())}
    assert out == {"INBOX": False, "Sent": True, "Sent/2023": True, "Archive/2024": False}


async def _account(client, user, name: str) -> int:
    return (await client.post("/mailbox/accounts", headers=auth(user), json={
        "name": name, "imap_host": "imap.example.org", "imap_user": "ich",
        "imap_password": "geheim", "smtp_host": "smtp.example.org",
        "smtp_user": "ich", "smtp_password": "auch geheim"})).json()["id"]


async def test_suggestion_ranks_answers_over_senders_and_merges_the_vault(client, db):
    user = await make_user(db, "writer")
    first = await _account(client, user, "eins")
    second = await _account(client, user, "zwei")
    stranger = await _account(client, await make_user(db, "stranger"), "drei")
    now = dt.datetime.now(dt.timezone.utc)
    db.add_all([
        # Written to often, from two mailboxes: the counts add up.
        MailCorrespondent(account_id=first, email="anna@club.example", name="Anna A", sent=3,
                          received=1, last_seen=now),
        MailCorrespondent(account_id=second, email="anna@club.example", name="", sent=2),
        # Only ever wrote, forty times: still below three answers.
        MailCorrespondent(account_id=first, email="shop@club.example", name="Club Shop",
                          received=14, last_seen=now),
        # Somebody else's mailbox never shows.
        MailCorrespondent(account_id=stranger, email="anne@club.example",
                          name="Anne", sent=99),
        # From the vault, with the filed name that wins over the mailbox's spelling.
        AssistantContact(owner_user_id=user.id, email="Anna@club.example",
                         domain="club.example", name="Anna Amsel", source_kind="frontmatter"),
        AssistantContact(owner_user_id=user.id, email="bert@club.example",
                         domain="club.example", name="Bert", source_kind="body"),
    ])
    await db.commit()

    got = await suggest(db, user.id, "club")
    assert [g["email"] for g in got] == ["anna@club.example", "shop@club.example",
                                         "bert@club.example"]
    assert got[0]["name"] == "Anna Amsel"

    # Every word has to hit, in name or address alike.
    assert [g["email"] for g in await suggest(db, user.id, "amsel club")] == ["anna@club.example"]
    assert await suggest(db, user.id, "amsel shop") == []


async def test_the_endpoint_answers_only_from_two_characters_on(client, db):
    user = await make_user(db, "typer")
    assert (await client.get("/mailbox/addresses?q=a", headers=auth(user))).json() == []


async def test_harvest_counts_recipients_in_sent_and_senders_elsewhere(monkeypatch):
    class Part:
        def __init__(self, name, mailbox, host):
            self.name, self.mailbox, self.host = name, mailbox, host

    class Env:
        def __init__(self, from_=(), to=(), cc=()):
            self.from_, self.to, self.cc = from_, to, cc

    when = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)

    class Client:
        def list_folders(self):
            return [((), b"/", "INBOX"), ((), b"/", "Sent")]

        def select_folder(self, name, readonly=True):
            self.current = name
            return {b"UIDVALIDITY": 5}

        def search(self, criteria):
            return [1, 2] if self.current == "INBOX" else [1]

        def fetch(self, uids, fields):
            if self.current == "INBOX":
                return {1: {b"ENVELOPE": Env(from_=(Part(b"Anna", b"anna", b"x.example"),)),
                            b"INTERNALDATE": when},
                        2: {b"ENVELOPE": Env(from_=(Part(None, b"anna", b"x.example"),)),
                            b"INTERNALDATE": when}}
            return {1: {b"ENVELOPE": Env(to=(Part(None, b"bob", b"x.example"),),
                                         cc=(Part(b"Anna", b"ANNA", b"x.example"),)),
                        b"INTERNALDATE": when}}

    from contextlib import contextmanager

    @contextmanager
    def imap(_account):
        yield Client()

    monkeypatch.setattr(mc.mailbox, "_imap", imap)
    found, fresh = mc._harvest_sync(_Account(), {})
    assert found["anna@x.example"]["sent"] == 1 and found["anna@x.example"]["received"] == 2
    assert found["bob@x.example"] == {"name": "", "sent": 1, "received": 0, "last_seen": when}
    # Where the walk got to, per folder, with the validity that makes the number mean something.
    assert fresh == {"mail_correspondents:1:INBOX": "5:2", "mail_correspondents:1:Sent": "5:1"}
    # And the next walk asks only past that.
    found, _ = mc._harvest_sync(_Account(), fresh)
    assert found == {}
