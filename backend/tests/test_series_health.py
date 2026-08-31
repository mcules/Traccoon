"""Health readings from a phone: many series in one delivery, and no reading twice.

The three claims worth a test each are the ones the location intake could not make: one
token writes into several series, an overlapping poll window costs nothing because the
repetition is recognised, and a sender may create series only in its own corner.
"""
import datetime as dt

from app.models.series import Series, SeriesPoint
from app.services import series as service
from app.services import series_health
from conftest import auth, make_user
from sqlalchemy import select
from test_api_tokens import bearer, mint

WHEN = "2026-08-31T23:36:00+02:00"
UTC = dt.datetime(2026, 8, 31, 21, 36, tzinfo=dt.timezone.utc)
SLEEP_BODY = '{"stages": []}'


def payload(*points, device="phone"):
    return {"device": device, "points": list(points)}


def reading(series, value, ts=WHEN, **rest):
    return {"series": series, "ts": ts, "value": value, "source": "health-connect", **rest}


# -- Erkennen und Umformen ---------------------------------------------------

def test_the_shape_is_recognised_by_the_series_field():
    assert series_health.looks_like(payload(reading("health.heart-rate", 94)))
    # The location payloads must not fall into this door.
    assert not series_health.looks_like({"_type": "location", "lat": 50.0, "lon": 10.0})
    assert not series_health.looks_like({"locations": [{"geometry": {}}]})
    assert not series_health.looks_like({"points": [{"value": 1}]})


def test_the_timestamp_lands_in_utc():
    """The phone reports in its own offset. Two deliveries of the same instant have to
    produce the same row, otherwise the duplicate check never hits."""
    bundle = series_health.normalise(payload(reading("health.heart-rate", 94)))
    assert bundle["health.heart-rate"][0]["ts"] == UTC


def test_what_is_unusable_is_dropped_not_thrown():
    bundle = series_health.normalise(payload(
        reading("health.heart-rate", 94),
        reading("health.heart-rate", None),
        reading("health.heart-rate", 80, ts="gestern"),
        {"series": "", "ts": WHEN, "value": 5},
        "kein Objekt",
    ))
    assert len(bundle["health.heart-rate"]) == 1


def test_the_device_travels_in_the_context():
    p = series_health.normalise(payload(reading("health.steps", 1200),
                                        device="sm-s938b"))["health.steps"][0]
    assert p["context"]["device"] == "sm-s938b"


def test_a_night_becomes_a_text_point():
    bundle = series_health.normalise(payload(
        {"series": "health.sleep", "ts": WHEN, "body": SLEEP_BODY}))
    point = bundle["health.sleep"][0]
    assert point["body"] == SLEEP_BODY and point["format"] == "json"
    assert "value" not in point


# -- Der Endpunkt ------------------------------------------------------------

async def test_one_delivery_fills_several_series(client, db):
    user = await make_user(db, "traeger")
    r = await client.post("/ingest", json=payload(
        reading("health.blood-pressure-systolic", 148, unit="mmHg"),
        reading("health.blood-pressure-diastolic", 92, unit="mmHg"),
        reading("health.heart-rate", 94, unit="bpm"),
    ), headers=auth(user))

    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 3
    keys = {s.key for s in (await db.execute(select(Series))).scalars().all()}
    assert keys == {"health.blood-pressure-systolic", "health.blood-pressure-diastolic",
                    "health.heart-rate"}


async def test_a_new_series_gets_unit_and_limits_from_the_catalogue(client, db):
    user = await make_user(db, "traeger")
    await client.post("/ingest", json=payload(reading("health.spo2", 97)), headers=auth(user))

    row = (await db.execute(select(Series).where(Series.key == "health.spo2"))).scalar_one()
    assert row.kind == "number" and row.name == "Oxygen saturation"
    assert row.settings["unit"] == "%" and row.settings["min"] == 50


async def test_an_unknown_health_key_takes_the_unit_of_the_sender(client, db):
    """A new record type in the app should not have to wait for a release here."""
    user = await make_user(db, "traeger")
    await client.post("/ingest", json=payload(
        reading("health.vo2max", 42, unit="ml/kg/min")), headers=auth(user))

    row = (await db.execute(select(Series).where(Series.key == "health.vo2max"))).scalar_one()
    assert row.settings == {"unit": "ml/kg/min"}


async def test_only_the_health_corner_may_be_created(client, db):
    """A token that gets lost must not be able to sow the series list full."""
    user = await make_user(db, "traeger")
    r = await client.post("/ingest", json=payload(
        reading("akku.shelter", 42), reading("health.heart-rate", 94)), headers=auth(user))

    assert r.json()["accepted"] == 1
    keys = {s.key for s in (await db.execute(select(Series))).scalars().all()}
    assert keys == {"health.heart-rate"}


async def test_a_reading_outside_the_limits_is_discarded(client, db):
    user = await make_user(db, "traeger")
    r = await client.post("/ingest", json=payload(
        reading("health.blood-pressure-systolic", 148),
        reading("health.blood-pressure-systolic", 0, ts="2026-08-31T23:40:00+02:00"),
    ), headers=auth(user))

    body = r.json()
    assert (body["accepted"], body["skipped"]) == (1, 1)


async def test_nothing_usable_is_no_error(client, db):
    """A delivery without a single readable point must not put the app into a retry loop."""
    user = await make_user(db, "traeger")
    r = await client.post("/ingest", json={"device": "phone", "points": []},
                          headers=auth(user))
    assert r.status_code == 202 and r.json()["ignored"] is True


# -- Wiederholungen ----------------------------------------------------------

async def test_the_same_reading_twice_is_written_once(client, db):
    """Health Connect knows no change notification, so the app polls with overlapping
    windows and delivers what it has already delivered. That has to cost nothing."""
    user = await make_user(db, "traeger")
    body = payload(reading("health.heart-rate", 94))

    first = (await client.post("/ingest", json=body, headers=auth(user))).json()
    second = (await client.post("/ingest", json=body, headers=auth(user))).json()

    assert (first["accepted"], first["duplicate"]) == (1, 0)
    assert (second["accepted"], second["duplicate"]) == (0, 1)
    row = (await db.execute(select(Series).where(
        Series.key == "health.heart-rate"))).scalar_one()
    assert row.points == 1


async def test_a_corrected_value_at_the_same_second_still_arrives(client, db):
    """The mark is the timestamp plus the value. A device that corrects itself is not a
    repetition, and two different readings at the same second are two facts."""
    user = await make_user(db, "traeger")
    await client.post("/ingest", json=payload(reading("health.weight", 82.4)),
                      headers=auth(user))
    r = await client.post("/ingest", json=payload(reading("health.weight", 82.6)),
                          headers=auth(user))

    assert r.json()["accepted"] == 1
    rows = (await db.execute(select(SeriesPoint))).scalars().all()
    assert sorted(p.value for p in rows) == [82.4, 82.6]


async def test_the_duplicate_filter_holds_inside_one_batch(db):
    user = await make_user(db, "traeger")
    row = Series(owner_user_id=user.id, key="health.heart-rate", kind="number")
    db.add(row)
    await db.commit()

    got = await service.ingest(db, row, [
        {"ts": UTC, "value": 94}, {"ts": UTC, "value": 94}, {"ts": UTC, "value": 95}])
    await db.commit()
    assert (got["accepted"], got["duplicate"]) == (2, 1)


# -- Rechte ------------------------------------------------------------------

async def test_the_scope_reaches_the_intake_and_nothing_else(client, db):
    user = await make_user(db, "traeger")
    token, _ = await mint(client, user, scopes=("series_ingest",))

    r = await client.post("/ingest", json=payload(reading("health.heart-rate", 94)),
                          headers=bearer(token))
    assert r.status_code == 202 and r.json()["accepted"] == 1

    # Same token, a door it has no business at.
    assert (await client.get("/me/tokens", headers=bearer(token))).status_code == 403


async def test_without_a_token_nothing_goes_in(client):
    r = await client.post("/ingest", json=payload(reading("health.heart-rate", 94)))
    assert r.status_code == 401


async def test_series_belong_to_the_person_the_token_acts_as(client, db):
    """Two people, the same key: two series, and neither writes into the other's."""
    one, two = await make_user(db, "eins"), await make_user(db, "zwei")
    await client.post("/ingest", json=payload(reading("health.heart-rate", 94)),
                      headers=auth(one))
    await client.post("/ingest", json=payload(reading("health.heart-rate", 61)),
                      headers=auth(two))

    rows = (await db.execute(select(Series).where(
        Series.key == "health.heart-rate"))).scalars().all()
    assert {r.owner_user_id for r in rows} == {one.id, two.id}
