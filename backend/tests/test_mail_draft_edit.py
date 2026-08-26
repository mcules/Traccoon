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


@pytest.fixture
def marks(monkeypatch):
    """Which mail was marked with what, without an IMAP."""
    seen = {"sent": [], "flags": [], "fail_flag": False}

    async def send(account, ident, fields):
        seen["sent"].append(fields)

    async def flag(account, folder, uid, name, on):
        if seen["fail_flag"]:
            raise RuntimeError("the server keeps no keywords")
        seen["flags"].append((folder, uid, name, on))

    monkeypatch.setattr(mailbox, "send", send)
    monkeypatch.setattr(mailbox, "flag", flag)
    return seen


async def test_an_answer_marks_the_mail_it_answers(db, client, marks):
    """Otherwise nobody can see afterwards which of the twenty in the list is dealt with."""
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Re: Frage",
        "text": "Ja.", "about_uid": 12, "about_folder": "INBOX", "about_kind": "reply"})
    assert r.status_code == 204
    assert marks["flags"] == [("INBOX", 12, "\\Answered", True)]


async def test_passing_one_on_marks_it_differently(db, client, marks):
    """The protocol has a flag for answering and none for passing on, so the mail programs
    agreed on a keyword. Both must not end up as the same mark."""
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Fwd: Frage",
        "text": "Siehe unten.", "about_uid": 12, "about_folder": "INBOX",
        "about_kind": "forward"})
    assert marks["flags"] == [("INBOX", 12, "$Forwarded", True)]


async def test_a_plain_mail_marks_nothing(db, client, marks):
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)
    await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Neu", "text": "x"})
    assert marks["sent"] and marks["flags"] == []


async def test_a_mark_that_fails_does_not_break_the_send(db, client, marks):
    """Not every server keeps keywords. That is its right, and no reason to turn a mail that
    went out into an error."""
    marks["fail_flag"] = True
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident.id, "to": ["du@example.org"], "subject": "Fwd: x", "text": "y",
        "about_uid": 12, "about_folder": "INBOX", "about_kind": "forward"})
    assert r.status_code == 204 and marks["sent"], "the mail went out"


async def test_a_mail_without_a_named_sender_takes_the_default(db, client, watch):
    """A mail always has a sender, and which one the mailbox can work out by itself.

    The occasion: sending a draft from the reading view left the field empty, and the answer
    was a validation error naming a field the person never filled in.
    """
    anna = await make_user(db, "anna")
    kid, ident = await _account_with_identity(db, client, anna)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "to": ["du@example.org"], "subject": "Ohne Absender", "text": "x"})
    assert r.status_code == 204, r.text
    assert watch["sent"]


async def test_a_foreign_sender_is_still_refused(db, client, watch):
    """Working it out is not the same as accepting anything."""
    anna = await make_user(db, "anna")
    kid, _ = await _account_with_identity(db, client, anna)
    other = await make_user(db, "bert")
    kid2, ident2 = await _account_with_identity(db, client, other)

    r = await client.post(f"/mailbox/accounts/{kid}/send", headers=auth(anna), json={
        "identity_id": ident2.id, "to": ["du@example.org"], "subject": "x", "text": "y"})
    assert r.status_code == 400 and "identity" in r.json()["key"]
