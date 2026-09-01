"""What a device sends along should be readable again, not only writable.

`SeriesPoint.context` has been in the model from the start and `series_record` fills it since
the extra fields were added. Nothing read it back: `_point_out` listed value, title and body
and dropped the column, so the pulse of a blood pressure reading and the duration of a step
interval were written into a box nobody could open.
"""
from app.models.series import Series, SeriesPoint
from conftest import auth, make_user


async def _series(db, owner, key, kind="number") -> Series:
    row = Series(owner_user_id=owner.id, key=key, kind=kind, name=key)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_a_number_point_carries_its_extra_fields_out(client, db):
    user = await make_user(db, "traeger")
    series = await _series(db, user, "health.blood-pressure-systolic")
    db.add(SeriesPoint(series_id=series.id, value=128.0, source="samsung-health",
                       context={"pulse": 69}))
    await db.commit()

    r = await client.get("/series/health.blood-pressure-systolic/points", headers=auth(user))
    assert r.status_code == 200, r.text
    point = r.json()["points"][0]
    assert point["value"] == 128.0
    assert point["context"] == {"pulse": 69}


async def test_a_text_point_carries_them_too(client, db):
    user = await make_user(db, "traeger")
    series = await _series(db, user, "health.sleep", kind="text")
    db.add(SeriesPoint(series_id=series.id, title="", body='{"stages": []}', format="json",
                       source="health-bridge", context={"duration_s": 17100}))
    await db.commit()

    r = await client.get("/series/health.sleep/points", headers=auth(user))
    point = r.json()["points"][0]
    assert point["body"] == '{"stages": []}'
    assert point["context"] == {"duration_s": 17100}


async def test_a_point_without_extras_stays_as_narrow_as_before(client, db):
    """A trace of a hundred thousand rows must not grow an empty box per row."""
    user = await make_user(db, "traeger")
    series = await _series(db, user, "health.heart-rate")
    db.add(SeriesPoint(series_id=series.id, value=61.0, source="health-bridge"))
    await db.commit()

    r = await client.get("/series/health.heart-rate/points", headers=auth(user))
    point = r.json()["points"][0]
    assert point["value"] == 61.0
    assert "context" not in point


async def test_a_location_keeps_spreading_its_extras_flat(client, db):
    """The map reads `accuracy` and `battery` at the top level, not in a box."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, "tracker.handy", kind="location")
    db.add(SeriesPoint(series_id=series.id, lat=50.08, lon=10.56, source="owntracks",
                       extra={"accuracy": 10.0, "battery": 80.0}))
    await db.commit()

    r = await client.get("/series/tracker.handy/points", headers=auth(user))
    point = r.json()["points"][0]
    assert point["accuracy"] == 10.0 and point["battery"] == 80.0
    assert "context" not in point
