"""Which way somebody is reached, and who is available as a recipient at all.

Until now there was exactly one way out: Telegram, if a chat id was stored. Whoever triggers
a notification rarely knows whether the recipient uses Telegram though, and in a flow the
recipient is often only settled at runtime. So the person decides, the sender may override,
and when the chosen way is not stored for that person, the other one is taken instead of none.
"""
import pytest
from app.models.notification import Notification
from app.models.user import User
from app.services import notify
from sqlalchemy import select

from conftest import add_member, auth, make_project, make_user

pytestmark = pytest.mark.asyncio


async def _person(db, name, *, chat=None, mail=None, standard="telegram") -> User:
    u = await make_user(db, name)
    u.telegram_chat_id = chat
    u.email = mail
    u.notify_default = standard
    await db.commit()
    return u


def _no_smtp(monkeypatch, sent):
    async def send_mail(db, to_addr, subject, html_body, text_body):
        sent.append({"to": to_addr, "subject": subject, "text": text_body})
        return True
    from app.services import mail
    monkeypatch.setattr(mail, "send_mail", send_mail)


async def test_the_persons_default_decides(db, monkeypatch):
    sent = []
    _no_smtp(monkeypatch, sent)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    path = await notify.deliver(db, user=anna, kind="test", title="Hallo", body="Text")
    await db.commit()
    assert path["kanal"] == "email" and sent[0]["to"] == "anna@example.org"
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id is None and n.notified_at is not None, "per Mail zugestellt, nichts offen"


async def test_the_sender_may_dictate_the_channel(db, monkeypatch):
    sent = []
    _no_smtp(monkeypatch, sent)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    path = await notify.deliver(db, user=anna, kind="test", title="Hallo", channel="telegram")
    await db.commit()
    assert path["kanal"] == "telegram" and not sent
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id == "111"


async def test_a_missing_channel_falls_back_to_the_other(db, monkeypatch):
    """A message that reaches nobody is the worst outcome."""
    sent = []
    _no_smtp(monkeypatch, sent)
    bert = await _person(db, "bert", chat=None, mail="bert@example.org", standard="telegram")
    path = await notify.deliver(db, user=bert, kind="test", title="Hallo")
    await db.commit()
    assert path["kanal"] == "email" and sent[0]["to"] == "bert@example.org"


async def test_a_differing_address_beats_the_login_address(db, monkeypatch):
    sent = []
    _no_smtp(monkeypatch, sent)
    anna = await _person(db, "anna", mail="login@example.org", standard="email")
    anna.notify_email = "melde-mich@example.org"
    await db.commit()
    await notify.deliver(db, user=anna, kind="test", title="Hallo")
    await db.commit()
    assert sent[0]["to"] == "melde-mich@example.org"


async def test_without_any_channel_the_bell_remains(db, monkeypatch):
    sent = []
    _no_smtp(monkeypatch, sent)
    mute = await _person(db, "stumm", chat=None, mail=None, standard="email")
    path = await notify.deliver(db, user=mute, kind="test", title="Hallo")
    await db.commit()
    assert path["kanal"] == "bell" and not sent
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.user_id == mute.id and n.notified_at is None


async def test_the_profile_manages_the_channels(client, db):
    anna = await _person(db, "anna", chat="111", mail="anna@example.org")
    r = await client.put("/me/notify", headers=auth(anna),
                         json={"notify_default": "email", "notify_email": "post@example.org",
                               "telegram_chat_id": "999"})
    assert r.status_code == 204
    await db.refresh(anna)
    assert (anna.notify_default, anna.notify_email, anna.telegram_chat_id) == \
        ("email", "post@example.org", "999")

    bad = await client.put("/me/notify", headers=auth(anna), json={"notify_default": "brieftaube"})
    assert bad.status_code == 400


async def test_visible_people(client, db):
    """Recipient selection: one's own projects, placeholders, oneself, not the whole world."""
    from app.models.enums import ProjectRole, UserStatus

    await make_user(db, "system")   # id 1 is the system account and never turns up
    anna = await make_user(db, "anna")
    colleague = await make_user(db, "kollege")
    foreign = await make_user(db, "fremder")
    placeholder = await make_user(db, "platzhalter")
    placeholder.status = UserStatus.placeholder
    p = await make_project(db, "TRA", "Traccoon")
    await add_member(db, p, anna, ProjectRole.owner)
    await add_member(db, p, colleague, ProjectRole.member)
    await db.commit()

    names = {u["username"] for u in (await client.get("/users/visible", headers=auth(anna))).json()}
    assert {"anna", "kollege", "platzhalter"} <= names
    assert "fremder" not in names


async def test_visible_people_show_their_channels(client, db):
    await make_user(db, "system")
    anna = await _person(db, "anna", chat="111", mail=None, standard="telegram")
    me = [u for u in (await client.get("/users/visible", headers=auth(anna))).json()
           if u["username"] == "anna"][0]
    assert me["channels"] == ["telegram"] and me["notify_default"] == "telegram"


# ── Der offene Weg: ein Ziel ─────────────────────────────────────────────────

async def _target(db, owner, name="ntfy"):
    from app.models.destination import Destination
    d = Destination(name=name, base_url="https://ntfy.example/traccoon", user_id=owner.id,
                    auth_type="none", enabled=True)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def test_destination_as_a_way_out(db, monkeypatch):
    """Telegram and e-mail were the only ways — every further one would have cost code.

    A destination carries a base URL and a login already; what sits behind it (ntfy, Matrix,
    Gotify, a bot of one's own) Traccoon need not know.
    """
    called = []

    async def call(db_, dest, **kw):
        called.append((dest.name, kw))
        return {"status_code": 200, "ok": True}

    from app.services import destinations
    monkeypatch.setattr(destinations, "call", call)

    anna = await _person(db, "anna", chat=None, mail=None, standard="ziel")
    target = await _target(db, anna)
    anna.notify_destination_id = target.id
    await db.commit()

    path = await notify.deliver(db, user=anna, kind="test", title="Hallo", body="Text")
    assert path["kanal"] == "ziel" and path["ok"] is True
    (name, kw) = called[0]
    assert name == "ntfy" and kw["body"] == {"art": "test", "titel": "Hallo", "text": "Text"}
    # The bell carries it all the same, and the timestamp says that nothing is pending outside.
    n = (await db.execute(select(Notification))).scalars().one()
    assert n.notified_at is not None


async def test_channel_without_a_destination_stays_in_the_bell(db):
    anna = await _person(db, "anna", chat=None, mail=None, standard="ziel")
    path = await notify.deliver(db, user=anna, kind="test", title="Hallo")
    assert path["kanal"] == "bell"


async def test_a_foreign_destination_cannot_be_picked(client, db):
    """Otherwise the channel would be a way to foreign credentials."""
    anna = await _person(db, "anna")
    bert = await _person(db, "bert")
    foreign = await _target(db, bert, name="bertfunk")

    bad = await client.put("/me/notify", headers=auth(anna),
                                json={"notify_destination_id": foreign.id})
    assert bad.status_code == 400

    own = await _target(db, anna, name="annafunk")
    good = await client.put("/me/notify", headers=auth(anna),
                           json={"notify_default": "ziel", "notify_destination_id": own.id})
    assert good.status_code == 204
    await db.refresh(anna)
    assert (anna.notify_default, anna.notify_destination_id) == ("ziel", own.id)
