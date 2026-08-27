"""The three numbers of the mail card: new mail, spam, drafts, across the mailboxes.

Two things are worth a test here. The numbers are a **sum** over the mailboxes — a single
one of them would have looked right in every manual check. And a mailbox that is silent must
not take the card down with it: what one gets then is the sum of those that answered, plus
how many that were.
"""
import pytest
from app.services import mailbox

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _account(client, user, name: str) -> int:
    return (await client.post("/mailbox/accounts", headers=auth(user), json={
        "name": name, "imap_host": "imap.example.org", "imap_user": "ich",
        "imap_password": "geheim", "smtp_host": "smtp.example.org",
        "smtp_user": "ich", "smtp_password": "auch geheim"})).json()["id"]


async def test_counts_add_up_over_the_mailboxes(client, db, monkeypatch):
    user = await make_user(db, "counter")
    first = await _account(client, user, "eins")
    await _account(client, user, "zwei")

    async def counts(account):
        return ({"unread": 2, "spam": 7, "drafts": 1} if account.id == first
                else {"unread": 3, "spam": 0, "drafts": 4})

    monkeypatch.setattr(mailbox, "counts", counts)

    r = await client.get("/mailbox/counts", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert {k: body[k] for k in ("unread", "spam", "drafts", "accounts", "accounts_total")} == {
        "unread": 5, "spam": 7, "drafts": 5, "accounts": 2, "accounts_total": 2}
    # Per mailbox as well: the figure alone does not say which of them is drowning.
    assert body["boxes"] == [{"name": "eins", "unread": 2, "spam": 7, "drafts": 1},
                             {"name": "zwei", "unread": 3, "spam": 0, "drafts": 4}]


async def test_a_silent_mailbox_leaves_the_rest_standing(client, db, monkeypatch):
    user = await make_user(db, "halfway")
    first = await _account(client, user, "eins")
    await _account(client, user, "stumm")

    async def counts(account):
        if account.id != first:
            raise OSError("connection refused")
        return {"unread": 1, "spam": 0, "drafts": 2}

    monkeypatch.setattr(mailbox, "counts", counts)

    body = (await client.get("/mailbox/counts", headers=auth(user))).json()
    assert body["unread"] == 1 and body["drafts"] == 2
    # The card says the numbers come from one of two mailboxes, instead of claiming them
    # as the whole truth.
    assert (body["accounts"], body["accounts_total"]) == (1, 2)
    assert [b["name"] for b in body["boxes"]] == ["eins"]
