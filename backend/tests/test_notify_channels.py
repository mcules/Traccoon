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


def _kein_smtp(monkeypatch, gesendet):
    async def send_mail(db, to_addr, subject, html_body, text_body):
        gesendet.append({"to": to_addr, "subject": subject, "text": text_body})
        return True
    from app.services import mail
    monkeypatch.setattr(mail, "send_mail", send_mail)


async def test_the_persons_default_decides(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo", body="Text")
    await db.commit()
    assert weg["kanal"] == "email" and gesendet[0]["to"] == "anna@example.org"
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id is None and n.notified_at is not None, "per Mail zugestellt, nichts offen"


async def test_the_sender_may_dictate_the_channel(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo", kanal="telegram")
    await db.commit()
    assert weg["kanal"] == "telegram" and not gesendet
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id == "111"


async def test_a_missing_channel_falls_back_to_the_other(db, monkeypatch):
    """A message that reaches nobody is the worst outcome."""
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    bert = await _person(db, "bert", chat=None, mail="bert@example.org", standard="telegram")
    weg = await notify.zustellen(db, user=bert, kind="test", title="Hallo")
    await db.commit()
    assert weg["kanal"] == "email" and gesendet[0]["to"] == "bert@example.org"


async def test_a_differing_address_beats_the_login_address(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", mail="login@example.org", standard="email")
    anna.notify_email = "melde-mich@example.org"
    await db.commit()
    await notify.zustellen(db, user=anna, kind="test", title="Hallo")
    await db.commit()
    assert gesendet[0]["to"] == "melde-mich@example.org"


async def test_without_any_channel_the_bell_remains(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    stumm = await _person(db, "stumm", chat=None, mail=None, standard="email")
    weg = await notify.zustellen(db, user=stumm, kind="test", title="Hallo")
    await db.commit()
    assert weg["kanal"] == "bell" and not gesendet
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.user_id == stumm.id and n.notified_at is None


async def test_the_profile_manages_the_channels(client, db):
    anna = await _person(db, "anna", chat="111", mail="anna@example.org")
    r = await client.put("/me/notify", headers=auth(anna),
                         json={"notify_default": "email", "notify_email": "post@example.org",
                               "telegram_chat_id": "999"})
    assert r.status_code == 204
    await db.refresh(anna)
    assert (anna.notify_default, anna.notify_email, anna.telegram_chat_id) == \
        ("email", "post@example.org", "999")

    schlecht = await client.put("/me/notify", headers=auth(anna), json={"notify_default": "brieftaube"})
    assert schlecht.status_code == 400


async def test_visible_people(client, db):
    """Recipient selection: one's own projects, placeholders, oneself, not the whole world."""
    from app.models.enums import ProjectRole, UserStatus

    await make_user(db, "system")   # id 1 is the system account and never turns up
    anna = await make_user(db, "anna")
    kollege = await make_user(db, "kollege")
    fremder = await make_user(db, "fremder")
    platzhalter = await make_user(db, "platzhalter")
    platzhalter.status = UserStatus.placeholder
    p = await make_project(db, "TRA", "Traccoon")
    await add_member(db, p, anna, ProjectRole.owner)
    await add_member(db, p, kollege, ProjectRole.member)
    await db.commit()

    namen = {u["username"] for u in (await client.get("/users/visible", headers=auth(anna))).json()}
    assert {"anna", "kollege", "platzhalter"} <= namen
    assert "fremder" not in namen


async def test_visible_people_show_their_channels(client, db):
    await make_user(db, "system")
    anna = await _person(db, "anna", chat="111", mail=None, standard="telegram")
    ich = [u for u in (await client.get("/users/visible", headers=auth(anna))).json()
           if u["username"] == "anna"][0]
    assert ich["channels"] == ["telegram"] and ich["notify_default"] == "telegram"


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
    """Telegram und E-Mail waren die einzigen Wege — jeder weitere hätte Code gekostet.

    Ein Ziel trägt Basis-URL und Anmeldung schon; was dahinter steckt (ntfy, Matrix, Gotify,
    ein eigener Bot), muss Traccoon nicht wissen.
    """
    gerufen = []

    async def call(db_, dest, **kw):
        gerufen.append((dest.name, kw))
        return {"status_code": 200, "ok": True}

    from app.services import destinations
    monkeypatch.setattr(destinations, "call", call)

    anna = await _person(db, "anna", chat=None, mail=None, standard="ziel")
    target = await _target(db, anna)
    anna.notify_destination_id = target.id
    await db.commit()

    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo", body="Text")
    assert weg["kanal"] == "ziel" and weg["ok"] is True
    (name, kw) = gerufen[0]
    assert name == "ntfy" and kw["body"] == {"art": "test", "titel": "Hallo", "text": "Text"}
    # Die Glocke trägt sie trotzdem, und der Zeitstempel sagt, dass draußen nichts aussteht.
    n = (await db.execute(select(Notification))).scalars().one()
    assert n.notified_at is not None


async def test_channel_without_a_destination_stays_in_the_bell(db):
    anna = await _person(db, "anna", chat=None, mail=None, standard="ziel")
    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo")
    assert weg["kanal"] == "bell"


async def test_a_foreign_destination_cannot_be_picked(client, db):
    """Sonst wäre der Kanal ein Weg an fremde Anmeldedaten."""
    anna = await _person(db, "anna")
    bert = await _person(db, "bert")
    fremd = await _target(db, bert, name="bertfunk")

    schlecht = await client.put("/me/notify", headers=auth(anna),
                                json={"notify_destination_id": fremd.id})
    assert schlecht.status_code == 400

    eigen = await _target(db, anna, name="annafunk")
    gut = await client.put("/me/notify", headers=auth(anna),
                           json={"notify_default": "ziel", "notify_destination_id": eigen.id})
    assert gut.status_code == 204
    await db.refresh(anna)
    assert (anna.notify_default, anna.notify_destination_id) == ("ziel", eigen.id)
