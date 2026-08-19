"""Conversation with the assistant: pages instead of everything, and an archive.

The occasion was the view: it fetched the last fifty messages and then scrolled through all
of them down to the current one on every opening. And nothing ever left the window, because
there was no way to put a finished conversation away.

What is tested here is the half the server owns: a page holds what was asked for, says
whether anything older lies before it, and the archive takes a message out of the view
without taking it out of the world. A running message stays: archiving something one is
waiting for an answer to would hide exactly that answer.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantTask
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _chat(db, user, anzahl: int, status: str = "done") -> list[AssistantTask]:
    zeilen = [AssistantTask(owner_user_id=user.id, kind="chat", source="web", status=status,
                            title=f"Frage {i}", meta={"chat_text": f"Frage {i}"},
                            result=f"Antwort {i}")
              for i in range(anzahl)]
    db.add_all(zeilen)
    await db.commit()
    for z in zeilen:
        await db.refresh(z)
    return zeilen


async def test_seite_haelt_ihre_groesse_und_meldet_aeltere(db, client):
    anna = await make_user(db, "anna")
    await _chat(db, anna, 25)

    r = await client.get("/assistant/chat?limit=10", headers=auth(anna))
    seite = r.json()
    assert [n["text"] for n in seite["nachrichten"]] == [f"Frage {i}" for i in range(15, 25)], \
        "newest last, so that the conversation reads from top to bottom"
    assert seite["mehr"] is True

    aelteste = seite["nachrichten"][0]["id"]
    davor = (await client.get(f"/assistant/chat?limit=10&vor={aelteste}", headers=auth(anna))).json()
    assert [n["text"] for n in davor["nachrichten"]] == [f"Frage {i}" for i in range(5, 15)]
    assert davor["mehr"] is True

    anfang = (await client.get(f"/assistant/chat?limit=10&vor={davor['nachrichten'][0]['id']}",
                               headers=auth(anna))).json()
    assert len(anfang["nachrichten"]) == 5
    assert anfang["mehr"] is False, "nothing lies before the first message"


async def test_archivierte_nachricht_verschwindet_aus_dem_verlauf(db, client):
    anna = await make_user(db, "anna")
    (eine, andere) = await _chat(db, anna, 2)

    await client.post(f"/assistant/chat/{eine.id}/archive", headers=auth(anna))
    verlauf = (await client.get("/assistant/chat", headers=auth(anna))).json()
    assert [n["id"] for n in verlauf["nachrichten"]] == [andere.id]

    archiv = (await client.get("/assistant/chat?archiv=1", headers=auth(anna))).json()
    assert [n["id"] for n in archiv["nachrichten"]] == [eine.id]

    # Out of the view, not out of the world: it comes back.
    await client.post(f"/assistant/chat/{eine.id}/unarchive", headers=auth(anna))
    verlauf = (await client.get("/assistant/chat", headers=auth(anna))).json()
    assert {n["id"] for n in verlauf["nachrichten"]} == {eine.id, andere.id}


async def test_laufende_nachricht_bleibt_stehen(db, client):
    anna = await make_user(db, "anna")
    (laeuft,) = await _chat(db, anna, 1, status="running")
    (fertig,) = await _chat(db, anna, 1)

    r = await client.post(f"/assistant/chat/{laeuft.id}/archive", headers=auth(anna))
    assert r.status_code == 409

    r = await client.post("/assistant/chat/archive-all", headers=auth(anna))
    assert r.json()["archiviert"] == 1
    verlauf = (await client.get("/assistant/chat", headers=auth(anna))).json()
    assert [n["id"] for n in verlauf["nachrichten"]] == [laeuft.id]
    assert fertig.id not in [n["id"] for n in verlauf["nachrichten"]]


async def test_eingaenge_zeigen_keine_chat_nachrichten(db, client):
    """The inbox says "incoming"; a message somebody typed here is not one."""
    anna = await make_user(db, "anna")
    await _chat(db, anna, 3)
    mail = AssistantTask(owner_user_id=anna.id, kind="email", source="webhook:new-email",
                         status="done", title="Rechnung")
    db.add(mail)
    await db.commit()
    await db.refresh(mail)

    liste = (await client.get("/assistant/inbox", headers=auth(anna))).json()
    assert [e["id"] for e in liste] == [mail.id]

    # Archived items are gone from the list as well, and only there.
    mail.archived_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    assert (await client.get("/assistant/inbox", headers=auth(anna))).json() == []
    archiv = (await client.get("/assistant/inbox?archiv=1", headers=auth(anna))).json()
    assert [e["id"] for e in archiv] == [mail.id]
    assert (await db.execute(select(AssistantTask.id).where(AssistantTask.id == mail.id))).scalar_one()
