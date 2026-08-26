"""Nothing that arrived may get lost.

The promise of this table is narrow and absolute: once a delivery has landed, no mishap of
ours can make it disappear. Not a missing flow, not a restart in the middle, not a bug in the
work that follows. That promise is what is checked here, above all in the cases where the
old, synchronous way silently dropped the payload.
"""
import datetime as dt

import pytest
from app.models.notification import Notification
from app.models.ops import InboundDelivery
from app.services import inbound
from sqlalchemy import select

from conftest import make_user, make_webhook

pytestmark = pytest.mark.asyncio


async def _sub(db, owner, **fields):
    return await make_webhook(db, owner, fields.pop("route", "r"), mode="event",
                              event_name="tracker.alarm", **fields)


async def _post(client, sub, payload=None, headers=None):
    return await client.post(f"/hooks/{sub.public_id}", json=payload or {"a": 1},
                             headers=headers or {})


async def test_the_answer_is_a_receipt_and_the_payload_is_stored(client, db):
    anna = await make_user(db, "anna")
    sub = await _sub(db, anna)

    r = await _post(client, sub, {"device": {"name": "Shelter"}})
    assert r.status_code == 202
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    assert row.status == "new" and row.attempts == 0
    assert b"Shelter" in bytes(row.body), "byte for byte, so the signature still checks out"
    assert row.route == "r"


async def test_a_delivery_survives_a_flow_that_is_not_there(client, db):
    """The case the old way lost outright: it answered 400 and the payload was gone."""
    anna = await make_user(db, "anna")
    sub = await make_webhook(db, anna, "wf", mode="workflow", workflow_definition_id=None)

    r = await _post(client, sub)
    await inbound.drain(db)
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    await db.refresh(row)
    assert row.status == "new", "it waits instead of disappearing"
    assert row.attempts == 1 and row.next_try_at is not None
    assert b'"a": 1' in bytes(row.body) or b'"a":1' in bytes(row.body)


async def test_after_enough_attempts_it_parks_and_says_so(client, db):
    """Not endlessly: a broken flow must not hammer for ever, and a delivery standing still
    that nobody sees is the same as a lost one."""
    anna = await make_user(db, "anna")
    sub = await make_webhook(db, anna, "wf", mode="workflow", workflow_definition_id=None)
    r = await _post(client, sub)
    row_id = r.json()["delivery_id"]

    for _ in range(inbound.MAX_ATTEMPTS):
        row = await db.get(InboundDelivery, row_id)
        row.next_try_at = None          # the waiting time is not what is under test here
        await db.commit()
        await inbound.drain(db)

    row = await db.get(InboundDelivery, row_id)
    await db.refresh(row)
    assert row.status == "parked" and row.attempts == inbound.MAX_ATTEMPTS
    assert row.last_error
    notes = list((await db.execute(select(Notification))).scalars().all())
    assert notes and "wf" in notes[0].title


async def test_a_wrong_signature_is_dropped_and_not_repeated(client, db):
    """The same bytes give the same signature for ever, repeating it would be pointless."""
    anna = await make_user(db, "anna")
    sub = await _sub(db, anna, secret="s3cret")

    r = await _post(client, sub, {"a": 1}, {"X-Webhook-Signature": "deadbeef"})
    await inbound.drain(db)
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    await db.refresh(row)
    assert row.status == "dropped" and "signature" in row.outcome


async def test_the_right_signature_goes_through(client, db):
    """And the check happens over the stored bytes, not over something re-encoded."""
    import hashlib
    import hmac
    import json as _json

    anna = await make_user(db, "anna")
    sub = await _sub(db, anna, secret="s3cret")
    raw = _json.dumps({"device": {"name": "Shelter"}}).encode()
    sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()

    r = await client.post(f"/hooks/{sub.public_id}", content=raw,
                          headers={"content-type": "application/json",
                                   "X-Webhook-Signature": sig})
    await inbound.drain(db)
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    await db.refresh(row)
    assert row.status == "done", row.last_error


async def test_a_delivery_for_a_route_that_is_gone_is_kept(client, db):
    """Evidence, not rubbish: whoever switched a route off may want to see what still came."""
    anna = await make_user(db, "anna")
    sub = await _sub(db, anna)
    r = await _post(client, sub)
    await db.delete(sub)
    await db.commit()

    await inbound.drain(db)
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    await db.refresh(row)
    assert row.status == "parked" and "route" in row.outcome
    assert bytes(row.body), "the payload is still there"


async def test_a_secret_never_lands_in_the_table(client, db):
    """The front door stores headers, and there is no reason for a login to be among them."""
    anna = await make_user(db, "anna")
    sub = await _sub(db, anna)
    r = await _post(client, sub, {"a": 1},
                    {"Authorization": "Bearer geheim", "X-Github-Event": "push"})
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    assert "authorization" not in row.headers
    assert row.headers.get("x-github-event") == "push"


async def test_one_failure_does_not_take_the_others_with_it(client, db):
    """Every delivery is written away on its own. Otherwise one broken one would roll back a
    whole batch of good ones, which is exactly the loss this table exists to prevent."""
    anna = await make_user(db, "anna")
    good = await _sub(db, anna, route="gut")
    bad = await make_webhook(db, anna, "kaputt", mode="workflow", workflow_definition_id=None)

    ids = [(await _post(client, bad)).json()["delivery_id"],
           (await _post(client, good)).json()["delivery_id"],
           (await _post(client, bad)).json()["delivery_id"]]
    await inbound.drain(db)

    rows = [await db.get(InboundDelivery, i) for i in ids]
    for row in rows:
        await db.refresh(row)
    assert [r.status for r in rows] == ["new", "done", "new"]


async def test_what_is_not_due_yet_stays_where_it_is(client, db):
    anna = await make_user(db, "anna")
    sub = await _sub(db, anna)
    r = await _post(client, sub)
    row = await db.get(InboundDelivery, r.json()["delivery_id"])
    row.next_try_at = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(hours=1)
    await db.commit()

    assert await inbound.drain(db) == 0
    await db.refresh(row)
    assert row.status == "new" and row.attempts == 0
