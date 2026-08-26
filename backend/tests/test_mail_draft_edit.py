"""Editing a draft: write the new one, then take the old one away.

IMAP cannot change a message. Editing a draft therefore means appending a new one and
removing the one it replaces, and the order between those two is the whole risk: the other
way round, a failed save would leave the person with neither version.
"""
import pytest
from app.models.mail import MailIdentity
from app.services import mailbox

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _account_with_identity(db, client, user):
    kid = (await client.post("/mailbox/accounts", headers=auth(user), json={
        "name": "privat", "imap_host": "imap.example.org", "imap_user": "ich",
        "imap_password": "geheim", "smtp_host": "smtp.example.org",
        "smtp_user": "ich", "smtp_password": "auch geheim",
        "folder_drafts": "Drafts"})).json()["id"]
    ident = MailIdentity(account_id=kid, email="ich@example.org", display_name="Ich",
                         is_default=True)
    db.add(ident)
    await db.commit()
    await db.refresh(ident)
    return kid, ident


@pytest.fixture
def watch(monkeypatch):
    """What reached IMAP, without an IMAP."""
    seen = {"saved": [], "sent": [], "dropped": []}

    async def save(account, ident, fields):
        seen["saved"].append(fields)

    async def send(account, ident, fields):
        seen["sent"].append(fields)

    async def drop(account, uid):
        seen["dropped"].append(uid)

    monkeypatch.setattr(mailbox, "draft_save", save)
    monkeypatch.setattr(mailbox, "send", send)
    monkeypatch.setattr(mailbox, "draft_drop", drop)
    return seen


async def test_saving_an_edited_draft_removes_the_old_one(db, client, watch):
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/draft", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Halb fertig",
        "text": "Der Rest kommt noch.", "replaces_uid": 17})
    assert r.status_code == 204
    assert watch["saved"] and watch["saved"][0]["subject"] == "Halb fertig"
    assert watch["dropped"] == [17]


async def test_sending_a_draft_removes_it_as_well(db, client, watch):
    """Otherwise the sent mail would still stand in the drafts folder as unfinished."""
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Fertig",
        "text": "Bitte schön.", "replaces_uid": 17})
    assert r.status_code == 204
    assert watch["sent"] and watch["dropped"] == [17]


async def test_a_plain_draft_removes_nothing(db, client, watch):
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    await client.post(f"/mailbox/accounts/{kid}/draft", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Neu", "text": "x"})
    assert watch["saved"] and watch["dropped"] == []


async def test_a_failed_save_keeps_the_old_draft(db, client, monkeypatch, watch):
    """The order is the point: first store, then remove. The other way round a mishap in the
    middle would leave the person with neither version."""
    async def broken(account, ident, fields):
        raise RuntimeError("no connection")

    monkeypatch.setattr(mailbox, "draft_save", broken)
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    with pytest.raises(RuntimeError):
        await client.post(f"/mailbox/accounts/{kid}/draft", headers=auth(anna), json={
            "identity_id": ident.id, "to": ["du@example.org"], "subject": "x", "text": "y",
            "replaces_uid": 17})
    assert watch["dropped"] == [], "the old one is still the only version there is"


async def test_a_removal_that_fails_does_not_break_the_send(db, client, monkeypatch, watch):
    """The new version is safe by then. What is left is a duplicate, not a loss."""
    async def stubborn(account, uid):
        raise RuntimeError("still there")

    monkeypatch.setattr(mailbox, "draft_drop", stubborn)
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Fertig", "text": "x",
        "replaces_uid": 17})
    assert r.status_code == 204
    assert watch["sent"], "the mail went out"
