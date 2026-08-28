"""Ungelesene Antworten in einer Meldung.

Eine Meldung ist ein Gespräch, und ein Gespräch, dessen letzter Satz niemand gehört hat, ist
abgebrochen: der Melder wartet auf jemanden, der nicht weiß, dass er dran ist. Deshalb zählt
die Liste, was von drüben kam und diese Person noch nicht gesehen hat.

Zwei Dinge dürfen dabei nie mitzählen — was wir selbst geschrieben haben (wir waren dabei)
und eine interne Notiz (die steht unter uns, darauf wartet niemand).
"""
import pytest

from conftest import auth, make_user
from test_report_mail import REPORT, mail, make_mailbox, make_source, outbox  # noqa: F401


async def eine_meldung(client, db, anna):
    """Eine Meldung samt Melder, wie ein Programm sie schickt.

    Mit `external_ref`: nur so darf das Programm die Unterhaltung später überhaupt sehen -
    ein Token öffnet die Meldungen seiner eigenen Leute und die von niemandem sonst.
    """
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    nummer = (await client.post("/bugs/report", json={**REPORT, "external_ref": "DL1XXX"},
                                headers={"X-Bug-Token": token})).json()["id"]
    return nummer, token


@pytest.mark.asyncio
async def test_eine_antwort_per_mail_ist_ungelesen(client, db, outbox):
    from app.services import report_mail

    anna = await make_user(db, "anna", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)
    await client.post(f"/bugs/{nummer}/posts", json={"body": "Schau mal."}, headers=auth(anna))

    # Was wir selbst geschrieben haben, ist nie ungelesen.
    liste = (await client.get("/bugs", headers=auth(anna))).json()
    assert [b["unread"] for b in liste] == [0]

    artifact, _ = await report_mail.match(db, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))
    await report_mail.file_reply(db, artifact, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))

    liste = (await client.get("/bugs", headers=auth(anna))).json()
    assert [b["unread"] for b in liste] == [1]
    warten = (await client.get("/bugs/waiting", headers=auth(anna))).json()
    assert warten["unread_posts"] == 1 and warten["unread_reports"] == 1


@pytest.mark.asyncio
async def test_wer_den_verlauf_liest_hat_gelesen(client, db, outbox):
    from app.services import report_mail

    anna = await make_user(db, "anna", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)
    await client.post(f"/bugs/{nummer}/posts", json={"body": "Schau mal."}, headers=auth(anna))
    artifact, _ = await report_mail.match(db, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))
    await report_mail.file_reply(db, artifact, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))

    # Aufmachen genügt - wer nachsieht und nichts sagt, kennt die Antwort trotzdem.
    await client.get(f"/bugs/{nummer}/posts", headers=auth(anna))

    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unread_posts"] == 0
    assert (await client.get(f"/bugs/{nummer}", headers=auth(anna))).json()["unread"] == 0


@pytest.mark.asyncio
async def test_gelesen_ist_pro_person(client, db, outbox):
    """Dass der eine die Antwort gesehen hat, sagt nichts über den anderen."""
    from app.services import report_mail

    anna = await make_user(db, "anna", admin=True)
    berta = await make_user(db, "berta", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)
    await client.post(f"/bugs/{nummer}/posts", json={"body": "Schau mal."}, headers=auth(anna))
    artifact, _ = await report_mail.match(db, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))
    await report_mail.file_reply(db, artifact, mail(headers={
        "In-Reply-To": outbox[0]["message_id"]}))

    await client.get(f"/bugs/{nummer}/posts", headers=auth(anna))

    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unread_posts"] == 0
    assert (await client.get("/bugs/waiting", headers=auth(berta))).json()["unread_posts"] == 1


@pytest.mark.asyncio
async def test_eine_interne_notiz_wartet_auf_niemanden(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    berta = await make_user(db, "berta", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)

    await client.post(f"/bugs/{nummer}/posts",
                      json={"body": "Der hatte nie ein Profil.", "internal": True},
                      headers=auth(anna))

    assert (await client.get("/bugs/waiting", headers=auth(berta))).json()["unread_posts"] == 0


@pytest.mark.asyncio
async def test_die_antwort_aus_dem_programm_zaehlt_auch(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    nummer, token = await eine_meldung(client, db, anna)

    antwort = await client.post(f"/bugs/app/reports/{nummer}/posts",
                                headers={"X-Bug-Token": token},
                                json={"body": "Geht immer noch nicht.",
                                      "external_ref": "DL1XXX", "author": "DL1XXX"})
    assert antwort.status_code == 201, antwort.text

    liste = (await client.get("/bugs?state=unread", headers=auth(anna))).json()
    assert [b["id"] for b in liste] == [nummer]
    assert liste[0]["unread"] == 1


@pytest.mark.asyncio
async def test_auch_eine_geschlossene_meldung_meldet_sich(client, db, outbox):
    """Eine Antwort auf etwas längst Abgehaktes ist genau die, die verloren geht."""
    anna = await make_user(db, "anna", admin=True)
    nummer, token = await eine_meldung(client, db, anna)
    await client.post(f"/bugs/{nummer}/status", json={"status": "done"}, headers=auth(anna))

    await client.post(f"/bugs/app/reports/{nummer}/posts", headers={"X-Bug-Token": token},
                      json={"body": "Doch nicht.", "external_ref": "DL1XXX"})

    offen = (await client.get("/bugs?state=open", headers=auth(anna))).json()
    ungelesen = (await client.get("/bugs?state=unread", headers=auth(anna))).json()
    assert offen == []
    assert [b["id"] for b in ungelesen] == [nummer]


@pytest.mark.asyncio
async def test_alte_meldungen_sind_nicht_alle_neu(client, db, outbox):
    """Wer eine Meldung schon einmal gelesen hat, bekommt nur das Neue markiert."""
    from app.services import report_mail

    anna = await make_user(db, "anna", admin=True)
    nummer, token = await eine_meldung(client, db, anna)
    await client.post(f"/bugs/app/reports/{nummer}/posts", headers={"X-Bug-Token": token},
                      json={"body": "Erste Rückfrage.", "external_ref": "DL1XXX"})
    await client.get(f"/bugs/{nummer}/posts", headers=auth(anna))

    await client.post(f"/bugs/app/reports/{nummer}/posts", headers={"X-Bug-Token": token},
                      json={"body": "Zweite Rückfrage.", "external_ref": "DL1XXX"})

    assert (await client.get(f"/bugs/{nummer}", headers=auth(anna))).json()["unread"] == 1


# ── Unbeantwortet: wer wartet auf ein Wort von uns ───────────────────────────

@pytest.mark.asyncio
async def test_eine_frische_meldung_ist_unbeantwortet(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)

    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unanswered"] == 1
    liste = (await client.get("/bugs?state=unanswered", headers=auth(anna))).json()
    assert [b["id"] for b in liste] == [nummer]


@pytest.mark.asyncio
async def test_unsere_antwort_beendet_das_warten(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    nummer, token = await eine_meldung(client, db, anna)

    await client.post(f"/bugs/{nummer}/posts", json={"body": "Schau mal."}, headers=auth(anna))
    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unanswered"] == 0

    # Schreibt der Melder wieder, wartet er wieder.
    await client.post(f"/bugs/app/reports/{nummer}/posts", headers={"X-Bug-Token": token},
                      json={"body": "Immer noch nicht.", "external_ref": "DL1XXX"})
    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unanswered"] == 1


@pytest.mark.asyncio
async def test_eine_interne_notiz_ist_keine_antwort(client, db, outbox):
    """Sie steht unter uns — der Melder sieht sie nie und wartet weiter."""
    anna = await make_user(db, "anna", admin=True)
    await eine_meldung(client, db, anna)
    nummer = (await client.get("/bugs", headers=auth(anna))).json()[0]["id"]

    await client.post(f"/bugs/{nummer}/posts", headers=auth(anna),
                      json={"body": "Der hatte nie ein Profil.", "internal": True})

    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unanswered"] == 1


@pytest.mark.asyncio
async def test_abgehakt_wartet_nicht_mehr(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)

    await client.post(f"/bugs/{nummer}/status", json={"status": "rejected"},
                      headers=auth(anna))

    assert (await client.get("/bugs/waiting", headers=auth(anna))).json()["unanswered"] == 0


@pytest.mark.asyncio
async def test_gelesen_und_trotzdem_unbeantwortet(client, db, outbox):
    """Der häufigste Fall von allen — und der Grund, warum es zwei Zahlen sind."""
    anna = await make_user(db, "anna", admin=True)
    nummer, _ = await eine_meldung(client, db, anna)

    await client.get(f"/bugs/{nummer}/posts", headers=auth(anna))

    warten = (await client.get("/bugs/waiting", headers=auth(anna))).json()
    assert warten["unread_posts"] == 0 and warten["unanswered"] == 1
