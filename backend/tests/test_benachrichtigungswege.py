"""Auf welchem Weg jemand erreicht wird — und wer überhaupt als Empfänger zur Wahl steht.

Bisher gab es genau einen Weg hinaus: Telegram, falls eine Chat-ID hinterlegt war. Wer
eine Benachrichtigung auslöst, weiß aber selten, ob der Empfänger Telegram benutzt —
in einem Ablauf steht der Empfänger oft erst zur Laufzeit fest. Also entscheidet die
Person, der Absender darf übersteuern, und wenn der gewählte Weg bei dieser Person nicht
hinterlegt ist, wird der andere genommen statt gar keiner.
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


async def test_standard_der_person_entscheidet(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo", body="Text")
    await db.commit()
    assert weg["kanal"] == "email" and gesendet[0]["to"] == "anna@example.org"
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id is None and n.notified_at is not None, "per Mail zugestellt, nichts offen"


async def test_absender_darf_den_weg_vorgeben(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", chat="111", mail="anna@example.org", standard="email")
    weg = await notify.zustellen(db, user=anna, kind="test", title="Hallo", kanal="telegram")
    await db.commit()
    assert weg["kanal"] == "telegram" and not gesendet
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.chat_id == "111"


async def test_fehlender_weg_faellt_auf_den_anderen(db, monkeypatch):
    """Eine Nachricht, die niemanden erreicht, ist der schlechteste Ausgang."""
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    bert = await _person(db, "bert", chat=None, mail="bert@example.org", standard="telegram")
    weg = await notify.zustellen(db, user=bert, kind="test", title="Hallo")
    await db.commit()
    assert weg["kanal"] == "email" and gesendet[0]["to"] == "bert@example.org"


async def test_abweichende_adresse_schlaegt_die_anmeldeadresse(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    anna = await _person(db, "anna", mail="login@example.org", standard="email")
    anna.notify_email = "melde-mich@example.org"
    await db.commit()
    await notify.zustellen(db, user=anna, kind="test", title="Hallo")
    await db.commit()
    assert gesendet[0]["to"] == "melde-mich@example.org"


async def test_ohne_jeden_weg_bleibt_die_glocke(db, monkeypatch):
    gesendet = []
    _kein_smtp(monkeypatch, gesendet)
    stumm = await _person(db, "stumm", chat=None, mail=None, standard="email")
    weg = await notify.zustellen(db, user=stumm, kind="test", title="Hallo")
    await db.commit()
    assert weg["kanal"] == "bell" and not gesendet
    (n,) = (await db.execute(select(Notification))).scalars().all()
    assert n.user_id == stumm.id and n.notified_at is None


async def test_profil_verwaltet_die_wege(client, db):
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


async def test_sichtbare_personen(client, db):
    """Empfängerauswahl: eigene Projekte, Platzhalter, man selbst — nicht die ganze Welt."""
    from app.models.enums import ProjectRole, UserStatus

    await make_user(db, "system")   # id 1 ist das Systemkonto und taucht nie auf
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


async def test_sichtbare_personen_zeigen_ihre_wege(client, db):
    await make_user(db, "system")
    anna = await _person(db, "anna", chat="111", mail=None, standard="telegram")
    ich = [u for u in (await client.get("/users/visible", headers=auth(anna))).json()
           if u["username"] == "anna"][0]
    assert ich["kanaele"] == ["telegram"] and ich["notify_default"] == "telegram"
