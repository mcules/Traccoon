"""Webhooks from foreign systems: filtering, addressing, not reporting twice.

Traccoon's own senders send flat payloads with headers. Everything else does not: a tracker
reports ignition, position and alarm over the *same* URL, without a header, with nested
JSON. Before, that meant: no filter possible (the event type came only from a header), no
addressing of deep fields (`{position.address}` stayed as text) and every repetition of the
sender a second message.
"""
import pytest
from app.models.notification import Notification
from app.models.ops import WebhookSub
from sqlalchemy import select

from conftest import auth, make_user, make_webhook

pytestmark = pytest.mark.asyncio

PAYLOAD = {
    "event": {"id": 1891, "type": "alarm", "attributes": {"alarm": "vibration"}},
    "position": {"latitude": 50.08, "address": "29 Regiomontanusstraße, Unfinden"},
    "device": {"id": 3, "name": "Shelter"},
}


async def _hook(db, owner, **fields) -> WebhookSub:
    """A reporter of the kind one used to create as `mode=notify`.

    The report itself is made today by a report node in the flow; what is checked here is the
    work of the trigger in front of it — filtering, finding deep fields, not reporting twice. The
    way there leads through the conversion, so that it is under observation as well.
    """
    return await make_webhook(db, owner, fields.pop("route", "r"), mode="notify",
                              body_template="{device.name}: {event.attributes.alarm}",
                              **fields)


async def _report(client, w, payload=PAYLOAD):
    return await client.post(f"/hooks/{w.public_id}", json=payload)


async def _last(db) -> list[Notification]:
    return list((await db.execute(select(Notification).order_by(Notification.id))).scalars().all())


async def test_deep_fields_are_substituted(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker1")
    r = await _report(client, w)
    assert r.status_code == 202
    (n,) = await _last(db)
    assert n.body == "Shelter: vibration"


async def test_a_filter_from_the_payload(client, db):
    """Without a header: the event type stands in the payload; otherwise everything comes through."""
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker2",
                    event_header="payload:event.attributes.alarm", event_filter="vibration")
    ignition = {"event": {"id": 7, "type": "ignitionOn", "attributes": {}},
                "device": {"name": "Shelter"}}
    r = await _report(client, w, ignition)
    assert r.json()["ignored"] is True
    assert await _last(db) == []

    assert (await _report(client, w)).status_code == 202
    assert len(await _last(db)) == 1


async def test_the_header_filter_stays(client, db):
    """The previous way must not change: GitHub and company send headers."""
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="gh", event_header="X-GitHub-Event", event_filter="push")
    r = await client.post(f"/hooks/{w.public_id}", json={"a": 1},
                          headers={"X-GitHub-Event": "issues"})
    assert r.json()["ignored"] is True
    r = await client.post(f"/hooks/{w.public_id}", json={"a": 1},
                          headers={"X-GitHub-Event": "push"})
    assert r.status_code == 202 and len(await _last(db)) == 1


async def test_the_same_report_only_once(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker3", ref_field="event.id")
    assert (await _report(client, w)).status_code == 202
    second = await _report(client, w)
    assert second.json().get("duplicate") is True
    assert len(await _last(db)) == 1

    # Another event is not a repetition.
    different = {**PAYLOAD, "event": {**PAYLOAD["event"], "id": 1892}}
    assert (await _report(client, w, different)).status_code == 202
    assert len(await _last(db)) == 2


async def test_without_a_reference_field_no_suppression(client, db):
    anna = await make_user(db, "anna")
    w = await _hook(db, anna, route="tracker4")
    await _report(client, w)
    await _report(client, w)
    assert len(await _last(db)) == 2


async def test_workflow_mode_recognises_repetitions(client, db):
    """The flow mode has to find the reference deep in the payload as well."""
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

    first = await _report(client, w)
    assert first.status_code == 202 and not first.json().get("duplicate")
    second = await _report(client, w)
    assert second.json().get("duplicate") is True
    runs = (await db.execute(select(WorkflowInstance))).scalars().all()
    assert len(runs) == 1


# ── Editing must lose nothing ───────────────────────────────────────────────

async def test_editing_keeps_the_context(client, db):
    """The answer did not carry the templates: the form filled them with defaults, and saving
    silently reset the entered text. The same trap stands open today at the context."""
    anna = await make_user(db, "anna")
    r = await client.post("/webhooks", headers=auth(anna), json={
        "route": "kontext", "mode": "event", "event_name": "tracker.alarm",
        "context_map": {"melder": "device.name"},
        "context_fixed": {"quelle": "Tracker {device.id}"}})
    assert r.status_code in (200, 201), r.text
    wid = r.json()["id"]

    read = [w for w in (await client.get("/webhooks", headers=auth(anna))).json()
               if w["id"] == wid][0]
    assert read["context_map"] == {"melder": "device.name"}
    assert read["context_fixed"] == {"quelle": "Tracker {device.id}"}

    # That is exactly what the interface does: write read values back.
    back = await client.put(f"/webhooks/{wid}", headers=auth(anna), json={
        "route": read["route"], "mode": read["mode"],
        "context_map": read["context_map"], "context_fixed": read["context_fixed"]})
    assert back.status_code == 200
    assert back.json()["context_fixed"] == {"quelle": "Tracker {device.id}"}


async def test_the_event_name_comes_back(client, db):
    anna = await make_user(db, "anna")
    r = await client.post("/webhooks", headers=auth(anna), json={
        "route": "melder", "mode": "event", "event_name": "sensor.ausgeloest"})
    assert r.json()["event_name"] == "sensor.ausgeloest"


async def test_the_collection_window_survives_duplicate_route_names(db):
    """Two webhooks may carry the same route name; the tick must not die of it."""
    import datetime as dt

    from app.models.ops import WebhookCoalesce
    from app.services.scheduler import _flush_coalesced

    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    for owner in (anna, bert):
        db.add(WebhookSub(public_id=f"doppelt-{owner.id}", route="gleich",
                          owner_user_id=owner.id, mode="notify", notify_chat="1"))
    db.add(WebhookCoalesce(route="gleich", event_key="x", payloads=[{"a": 1}],
                           window_until=dt.datetime.now(tz=dt.timezone.utc)
                           - dt.timedelta(minutes=1)))
    await db.commit()

    await _flush_coalesced()   # warf vorher MultipleResultsFound

    from app.models.notification import Notification
    sammel = (await db.execute(select(Notification).where(
        Notification.kind == "webhook"))).scalars().all()
    assert len(sammel) == 1 and "gleich" in sammel[0].title
