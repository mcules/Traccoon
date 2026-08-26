"""An answer carries the question it answers.

The assistant used to write a free text and nothing else: no subject, no reference, no quote,
unless the model happened to remember all three. It remembered sometimes. A week later such an
answer is a lone sentence, and no mail program can thread it.

So the tool does it, and the model only writes the answer. What is pinned here is the order —
the answer above, the quote below — because that is the part one only notices when it is
wrong.
"""
import pytest
from app.services import mail_mcp, mailbox
from app.models.mail import MailAccount, MailIdentity
from app.core.security import encrypt_secret

from conftest import make_user

pytestmark = pytest.mark.asyncio

ORIGIN = {
    "uid": 12, "folder": "INBOX", "subject": "Angebot 4711",
    "from": [{"name": "Firma", "addr": "vertrieb@beispiel.de"}],
    "date": "Mon, 25 Aug 2026 09:12:00 +0200",
    "message_id": "<abc@beispiel.de>",
    "text": "Guten Tag,\n\nanbei unser Angebot.\n\nViele Grüße",
}


@pytest.fixture
async def box(db, monkeypatch):
    user = await make_user(db, "anna")
    account = MailAccount(owner_user_id=user.id, name="privat",
                          imap_host="imap.example.org", imap_user="ich",
                          imap_password_enc=encrypt_secret("x"),
                          smtp_host="smtp.example.org", smtp_user="ich",
                          smtp_password_enc=encrypt_secret("x"),
                          mcp_enabled=True, enabled=True,
                          mcp_tools=["mail_send", "mail_draft"])
    db.add(account)
    await db.flush()
    db.add(MailIdentity(account_id=account.id, email="ich@example.org", is_default=True))
    await db.commit()

    seen = {"sent": [], "drafts": [], "flags": []}

    async def message(acc, folder, uid):
        assert (folder, uid) == ("INBOX", 12)
        return ORIGIN

    async def send(acc, ident, fields):
        seen["sent"].append(fields)

    async def draft_save(acc, ident, fields):
        seen["drafts"].append(fields)

    async def flag(acc, folder, uid, name, on):
        seen["flags"].append((folder, uid, name, on))

    monkeypatch.setattr(mailbox, "message", message)
    monkeypatch.setattr(mailbox, "send", send)
    monkeypatch.setattr(mailbox, "draft_save", draft_save)
    monkeypatch.setattr(mailbox, "flag", flag)
    return user, seen


async def test_the_answer_stands_above_the_quote(db, box):
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["vertrieb@beispiel.de"], "text": "Passt, wir nehmen es.",
        "reply_uid": 12, "folder": "INBOX"})

    (mail,) = seen["sent"]
    text = mail["text"]
    assert text.index("Passt, wir nehmen es.") < text.index("> Guten Tag,")
    assert "> anbei unser Angebot." in text, "the original stands there word for word"
    assert "schrieb vertrieb@beispiel.de:" in text


async def test_subject_and_reference_come_from_the_original(db, box):
    """Two things the model does not have to know, and therefore cannot get wrong."""
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["vertrieb@beispiel.de"], "text": "Ja.",
        "reply_uid": 12, "folder": "INBOX"})

    (mail,) = seen["sent"]
    assert mail["subject"] == "Re: Angebot 4711"
    assert mail["in_reply_to"] == "<abc@beispiel.de>"


async def test_an_own_subject_is_kept_and_only_gets_its_prefix(db, box):
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "subject": "Zum Angebot",
        "text": "Ja.", "reply_uid": 12})
    assert seen["sent"][0]["subject"] == "Re: Zum Angebot"


async def test_a_prefix_is_not_stacked(db, box):
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "subject": "Re: Angebot 4711",
        "text": "Ja.", "reply_uid": 12})
    assert seen["sent"][0]["subject"] == "Re: Angebot 4711"


async def test_the_answered_mail_is_marked(db, box):
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "text": "Ja.", "reply_uid": 12})
    assert seen["flags"] == [("INBOX", 12, "\\Answered", True)]


async def test_a_draft_is_quoted_the_same_way_but_marks_nothing(db, box):
    """Nothing has gone out yet, so the original has not been answered yet either."""
    user, seen = box
    await mail_mcp.execute(db, user, "mail_draft", {
        "account": "privat", "to": ["x@beispiel.de"], "text": "Ja.", "reply_uid": 12})
    assert "> anbei unser Angebot." in seen["drafts"][0]["text"]
    assert seen["flags"] == []


async def test_a_mail_that_answers_nothing_is_left_alone(db, box):
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "subject": "Neu", "text": "Hallo."})
    assert seen["sent"][0]["text"] == "Hallo."
    assert seen["sent"][0]["subject"] == "Neu"
    assert seen["flags"] == []


async def test_an_unreadable_original_still_lets_the_answer_out(db, box, monkeypatch):
    """Better an answer without a quote than none at all, but it must not happen quietly."""
    async def broken(acc, folder, uid):
        raise RuntimeError("no connection")

    monkeypatch.setattr(mailbox, "message", broken)
    user, seen = box
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "text": "Ja.", "reply_uid": 12})
    assert seen["sent"][0]["text"] == "Ja."
    assert seen["flags"] == [], "nothing may say it was answered when nothing was read"


async def test_the_house_rule_is_the_first_thing_said(db, box):
    """It is read on connecting, before the first tool runs, and it is the rule that gets
    forgotten. A mailbox with no rules of its own must not swallow it."""
    user, _ = box
    text = await mail_mcp.instructions(db, user)
    assert "reply_uid" in text
    assert text.index("reply_uid") < len(text) / 2, "it stands first, not at the end"
