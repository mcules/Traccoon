"""Webhooks von Fremdsystemen: filtern, anschreiben, nicht doppelt melden.

Traccoon-eigene Absender schicken flache Nutzlasten mit Kopfzeilen. Alles andere tut das
nicht: ein Tracker meldet Zündung, Standort und Alarm über *dieselbe* URL, ohne Kopfzeile,
mit verschachteltem JSON. Vorher hieß das: kein Filter möglich (der Ereignis-Typ kam nur
aus einer Kopfzeile), keine Anrede tiefer Felder (`{position.address}` blieb als Text
stehen) und jede Wiederholung des Absenders eine zweite Nachricht.
"""
import pytest
from app.models.notification import Notification
from app.models.ops import WebhookSub
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio

NUTZLAST = {
    "event": {"id": 1891, "type": "alarm", "attributes": {"alarm": "vibration"}},
    "position": {"latitude": 50.08, "address": "29 Regiomontanusstraße, Unfinden"},
    "device": {"id": 3, "name": "Shelter"},
}


async def _hook(db, besitzer, **felder) -> WebhookSub:
    w = WebhookSub(public_id=f"probe-{felder.get('route', 'r')}", route=felder.pop("route", "r"),
                   owner_user_id=besitzer.id, mode="notify",
                   body_template="{device.name}: {event.attributes.alarm}", **felder)
    db.add(w)
    await db.commit()
    return w


async def _melden(client, w, nutzlast=NUTZLAST):
    return await client.post(f"/hooks/{w.public_id}", json=nutzlast)


async def _letzte(db) -> list[Notification]:
    return list((await db.execute(select(Notification).order_by(Notification.id))).scalars().all())


async def test_tiefe_felder_werden_eingesetzt(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker1")
    r = await _melden(client, w)
    assert r.status_code == 202
    (n,) = await _letzte(db)
    assert n.body == "Shelter: vibration"


async def test_filter_aus_der_nutzlast(client, db):
    """Ohne Kopfzeile: der Ereignis-Typ steht in der Nutzlast — sonst kommt alles durch."""
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker2",
                    event_header="payload:event.attributes.alarm", event_filter="vibration")
    zuendung = {"event": {"id": 7, "type": "ignitionOn", "attributes": {}},
                "device": {"name": "Shelter"}}
    r = await _melden(client, w, zuendung)
    assert r.json()["ignored"] is True
    assert await _letzte(db) == []

    assert (await _melden(client, w)).status_code == 202
    assert len(await _letzte(db)) == 1


async def test_kopfzeilen_filter_bleibt(client, db):
    """Der bisherige Weg darf sich nicht ändern — GitHub & Co. schicken Kopfzeilen."""
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="gh", event_header="X-GitHub-Event", event_filter="push")
    r = await client.post(f"/hooks/{w.public_id}", json={"a": 1},
                          headers={"X-GitHub-Event": "issues"})
    assert r.json()["ignored"] is True
    r = await client.post(f"/hooks/{w.public_id}", json={"a": 1},
                          headers={"X-GitHub-Event": "push"})
    assert r.status_code == 202 and len(await _letzte(db)) == 1


async def test_dieselbe_meldung_nur_einmal(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker3", ref_field="event.id")
    assert (await _melden(client, w)).status_code == 202
    zweite = await _melden(client, w)
    assert zweite.json().get("duplicate") is True
    assert len(await _letzte(db)) == 1

    # Ein anderes Ereignis ist keine Wiederholung.
    anders = {**NUTZLAST, "event": {**NUTZLAST["event"], "id": 1892}}
    assert (await _melden(client, w, anders)).status_code == 202
    assert len(await _letzte(db)) == 2


async def test_ohne_bezugsfeld_kein_unterdruecken(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker4")
    await _melden(client, w)
    await _melden(client, w)
    assert len(await _letzte(db)) == 2


async def test_workflow_modus_erkennt_wiederholungen(client, db):
    """Auch der Ablauf-Modus muss den Bezug tief in der Nutzlast finden."""
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion

    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="tracker", name="Tracker", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {}}},
                            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "s", "target": "e"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    w = WebhookSub(public_id="probe-wf", route="wf", owner_user_id=anna.id, mode="workflow",
                   workflow_definition_id=d.id, ref_field="event.id")
    db.add(w)
    await db.commit()

    erste = await _melden(client, w)
    assert erste.status_code == 202 and not erste.json().get("duplicate")
    zweite = await _melden(client, w)
    assert zweite.json().get("duplicate") is True
    laeufe = (await db.execute(select(WorkflowInstance))).scalars().all()
    assert len(laeufe) == 1
