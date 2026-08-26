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


async def test_the_answer_comes_from_the_address_it_was_sent_to(db, box, monkeypatch):
    """A mailbox with six addresses answered everything from the first one.

    The far side knows exactly one of them, the one it wrote to. Answering from another is
    how a shop that only ever saw an address of its own gets a stranger writing about its
    order.
    """
    user, seen = box
    from app.models.mail import MailAccount, MailIdentity
    from sqlalchemy import select

    account = (await db.execute(select(MailAccount))).scalars().first()
    db.add(MailIdentity(account_id=account.id, email="shop@example.org",
                        display_name="Ich beim Shop"))
    await db.commit()

    origin = {**ORIGIN, "to": [{"addr": "shop@example.org"}],
              "cc": [{"addr": "wer@fremd.de"}]}

    async def message(acc, folder, uid):
        return origin

    monkeypatch.setattr(mailbox, "message", message)

    captured = {}

    async def send(acc, ident, fields):
        captured["from"] = ident.email
        seen["sent"].append(fields)

    monkeypatch.setattr(mailbox, "send", send)
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["vertrieb@beispiel.de"], "text": "Ja.", "reply_uid": 12})
    assert captured["from"] == "shop@example.org"


async def test_an_address_of_ours_in_copy_counts_too(db, box, monkeypatch):
    """Being kept in the loop is still being addressed. Only: `To` comes first."""
    user, seen = box
    from app.models.mail import MailAccount, MailIdentity
    from sqlalchemy import select

    account = (await db.execute(select(MailAccount))).scalars().first()
    db.add(MailIdentity(account_id=account.id, email="verteiler@example.org"))
    await db.commit()


    async def message(acc, folder, uid):
        return {**ORIGIN, "to": [{"addr": "wer@fremd.de"}],
                "cc": [{"addr": "verteiler@example.org"}]}

    monkeypatch.setattr(mailbox, "message", message)
    captured = {}

    async def send(acc, ident, fields):
        captured["from"] = ident.email

    monkeypatch.setattr(mailbox, "send", send)
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@fremd.de"], "text": "Ja.", "reply_uid": 12})
    assert captured["from"] == "verteiler@example.org"


async def test_an_own_wish_beats_the_addressed_one(db, box, monkeypatch):
    """Whoever names an identity means it."""
    user, seen = box
    captured = {}

    async def send(acc, ident, fields):
        captured["from"] = ident.email

    monkeypatch.setattr(mailbox, "send", send)
    await mail_mcp.execute(db, user, "mail_send", {
        "account": "privat", "to": ["x@fremd.de"], "text": "Ja.", "reply_uid": 12,
        "identity": "ich@example.org"})
    assert captured["from"] == "ich@example.org"


async def test_the_agent_writes_through_the_same_door(db, box, monkeypatch):
    """The native tool and the MCP server are one implementation, two entrances.

    The occasion: the assistant wrote through a foreign mail server that knows one sender
    address per mailbox. It answered a shop from a stranger's address and quoted nothing, and
    no prompt fixed that, because the foreign server cannot do either.
    """
    from app.worker.tools_traccoon import call_traccoon_tool

    user, seen = box
    answer = await call_traccoon_tool(db, user.id, "traccoon_mail_draft", {
        "account": "privat", "to": ["vertrieb@beispiel.de"], "text": "Passt.",
        "reply_uid": 12, "folder": "INBOX"})

    assert "quoted" in answer.lower() or "underneath" in answer.lower()
    assert "> anbei unser Angebot." in seen["drafts"][0]["text"]


async def test_a_mailbox_that_releases_nothing_says_so_instead_of_throwing(db, box):
    """A refusal is an answer the model can work with. An exception is a dead run."""
    from app.worker.tools_traccoon import call_traccoon_tool

    from app.models.mail import MailAccount
    from sqlalchemy import select

    user, _ = box
    account = (await db.execute(select(MailAccount))).scalars().first()
    account.mcp_tools = ["mail_draft"]          # drafting yes, sending no
    await db.commit()

    answer = await call_traccoon_tool(db, user.id, "traccoon_mail_send", {
        "account": "privat", "to": ["x@beispiel.de"], "text": "Hallo."})
    assert answer.startswith("ERROR:") and "mail_send" in answer
