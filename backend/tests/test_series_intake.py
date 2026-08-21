"""What happens when location points are taken in — and what deliberately does not.

The rest filter and the discarding are the two places where data is lost on purpose. Both have
to hit precisely: too strict, and the trace gets holes; too lax, and the table fills up
overnight at the desk.
"""
import datetime as dt

from app.models.series import Series, SeriesPlace, SeriesPoint
from app.services import series as service
from conftest import make_user
from sqlalchemy import select

# Two points, a good 200 m apart (around the location from Home Assistant).
HIER = (50.0825308, 10.5663527)
THERE_DRUEBEN = (50.0843000, 10.5663527)


def _p(lat, lon, ts=None, **extra):
    return {"lat": lat, "lon": lon, "ts": ts, "extra": extra, "source": "test"}


async def _series(db, owner, **settings) -> Series:
    r = Series(owner_user_id=owner.id, key="handy", kind="location", name="Handy",
               settings=settings)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _points(db, series) -> list[SeriesPoint]:
    return list((await db.execute(select(SeriesPoint).where(
        SeriesPoint.series_id == series.id).order_by(SeriesPoint.ts))).scalars().all())


async def test_the_first_point_always_arrives(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user)

    e = await service.ingest(db, series, [_p(*HIER, accuracy=10, battery=80)])
    await db.commit()

    assert e["accepted"] == 1 and e["skipped"] == 0
    assert series.state["lat"] == HIER[0] and series.state["battery"] == 80
    assert series.points == 1


async def test_the_rest_filter_leaves_standstill_out(db):
    """The same spot, shortly after one another: only the first point counts."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    e = await service.ingest(db, series, [
        _p(*HIER, ts=start),
        _p(50.0825310, 10.5663530, ts=start + dt.timedelta(minutes=1)),   # 3 cm weiter
        _p(50.0825320, 10.5663540, ts=start + dt.timedelta(minutes=2)),
    ])
    await db.commit()
    assert (e["accepted"], e["still"]) == (1, 2)


async def test_the_rest_filter_gives_way_after_the_interval(db):
    """Even someone who does not move should leave a sign of life now and then."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    e = await service.ingest(db, series, [
        _p(*HIER, ts=start),
        _p(*HIER, ts=start + dt.timedelta(minutes=6)),
    ])
    await db.commit()
    assert e["accepted"] == 2


async def test_movement_gets_through(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    e = await service.ingest(db, series, [
        _p(*HIER, ts=start),
        _p(*THERE_DRUEBEN, ts=start + dt.timedelta(seconds=30)),
    ])
    await db.commit()
    assert e["accepted"] == 2


async def test_outliers_are_dropped_without_toppling_the_call(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user, max_accuracy_m=500)
    morgen = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=1)

    e = await service.ingest(db, series, [
        _p(*HIER, accuracy=2000),          # zu ungenau
        _p(0, 0),                          # Null-Insel
        _p(91.0, 10.0),                    # unmoeglich
        _p(*THERE_DRUEBEN, ts=morgen),        # Uhr kaputt
        _p(*HERE, accuracy=10),            # this one counts
    ])
    await db.commit()
    assert (e["accepted"], e["skipped"]) == (1, 4)


async def test_a_batch_is_ordered_by_time(db):
    """Overland sends batches; the order in them is not guaranteed."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    await service.ingest(db, series, [
        _p(50.10, 10.50, ts=start + dt.timedelta(minutes=2)),
        _p(50.08, 10.56, ts=start),
        _p(50.09, 10.55, ts=start + dt.timedelta(minutes=1)),
    ])
    await db.commit()
    times = [p.ts for p in await _points(db, series)]
    assert times == sorted(times)
    # The state points at the youngest point, not at the one handed over last.
    assert series.state["lat"] == 50.10


async def test_a_backfill_does_not_disturb_the_current_state(db):
    """A point from last year is no sign of life."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    await service.ingest(db, series, [_p(*HIER, ts=now)])
    await service.ingest(db, series, [_p(50.5, 11.5, ts=now - dt.timedelta(days=400))])
    await db.commit()

    assert series.state["lat"] == HIER[0]
    assert series.last_at.replace(tzinfo=dt.timezone.utc) == now
    assert series.points == 2   # gespeichert wurde er trotzdem


# ── Geozaun ──────────────────────────────────────────────────────────────────

async def _place(db, owner, key, lat, lon, radius=150) -> SeriesPlace:
    o = SeriesPlace(owner_user_id=owner.id, key=key, name=key, lat=lat, lon=lon,
                    radius_m=radius)
    db.add(o)
    await db.commit()
    return o


async def test_entering_and_leaving_once_each(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    await _place(db, user, "zuhause", *HIER, radius=150)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    # Ankommen
    e = await service.ingest(db, series, [_p(*HIER, ts=start)])
    assert e["betreten"] == ["zuhause"] and e["verlassen"] == []

    # Bleiben — kein zweites Betreten
    e = await service.ingest(db, series, [_p(50.08250, 10.56640, ts=start + dt.timedelta(minutes=5))])
    assert e["betreten"] == [] and e["verlassen"] == []

    # Weggehen (gut 1 km)
    e = await service.ingest(db, series, [_p(50.0920, 10.5663, ts=start + dt.timedelta(minutes=20))])
    assert e["verlassen"] == ["zuhause"]
    await db.commit()


async def test_hysteresis_at_the_edge(db):
    """Just outside the radius still counts as inside — otherwise GPS noise flutters."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    await _place(db, user, "zuhause", *HIER, radius=100)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    await service.ingest(db, series, [_p(*HIER, ts=start)])
    # 120 m away: outside the 100 m, but inside the 150 m with the margin.
    e = await service.ingest(db, series, [
        _p(50.0836100, 10.5663527, ts=start + dt.timedelta(minutes=1))])
    assert e["verlassen"] == []
    # 180 m weg: jetzt wirklich draussen.
    e = await service.ingest(db, series, [
        _p(50.0841500, 10.5663527, ts=start + dt.timedelta(minutes=2))])
    assert e["verlassen"] == ["zuhause"]
    await db.commit()


async def test_a_silent_place_reports_nothing(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    place = await _place(db, user, "leise", *HIER)
    place.notify = False
    await db.commit()

    e = await service.ingest(db, series, [_p(*HIER)])
    await db.commit()
    # The state remembers the place all the same — only nothing is reported.
    assert e["betreten"] == ["leise"]
    assert series.state["places"] == ["leise"]


async def test_foreign_places_do_not_count(db):
    """Der Zaun eines anderen Menschen darf an meiner Spur nichts ausloesen."""
    me = await make_user(db, "ich")
    different = await make_user(db, "andere")
    series = await _series(db, me, min_distance_m=0, min_interval_s=0)
    await _place(db, different, "fremd", *HIER)

    e = await service.ingest(db, series, [_p(*HIER)])
    await db.commit()
    assert e["betreten"] == []


async def test_the_fence_applies_even_when_the_point_rests(db):
    """Whoever crosses the boundary slowly still arrives.

    The rest filter decides whether a point is stored — not where the device is. Were both the
    same thing, the entering would shift by up to one rest period.
    """
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    # A fence with a 20 m radius, right next to the starting point.
    await _place(db, user, "tuer", 50.0825308, 10.5663527, radius=20)

    # Erster Punkt: 60 m weg vom Zaun, also draussen.
    e = await service.ingest(db, series, [_p(50.0830700, 10.5663527, ts=start)])
    assert e["betreten"] == []

    # Two minutes later 60 m closer — too little for the rest filter, but inside the fence.
    e = await service.ingest(db, series, [
        _p(50.0825308, 10.5663527, ts=start + dt.timedelta(minutes=2))])
    await db.commit()
    assert e["still"] == 0 or e["accepted"] == 1 or e["betreten"] == ["tuer"]
    assert e["betreten"] == ["tuer"], "Ankunft muss sofort gelten, nicht erst beim naechsten Punkt"


async def test_the_fence_applies_to_a_truly_standing_device(db):
    """A place that is created while the device already stands in it."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    await service.ingest(db, series, [_p(*HIER, ts=start)])
    await _place(db, user, "zuhause", *HIER, radius=150)

    # The same spot a minute later: it rests, but reports the arrival all the same.
    e = await service.ingest(db, series, [_p(*HIER, ts=start + dt.timedelta(minutes=1))])
    await db.commit()
    assert (e["still"], e["betreten"]) == (1, ["zuhause"])


async def test_seen_and_stored_are_two_separate_facts(db):
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=25, min_interval_s=300)
    start = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)

    await service.ingest(db, series, [_p(*HIER, ts=start)])
    await service.ingest(db, series, [_p(*HIER, ts=start + dt.timedelta(minutes=1))])
    await db.commit()

    # Only the first was stored, the device last reported a minute later.
    assert series.last_at.replace(tzinfo=dt.timezone.utc) == start
    assert series.state["seen_at"].startswith("2026-08-20T12:01")


async def test_a_backfill_reports_no_arrival(db):
    """A point from yesterday must not claim that one has just arrived."""
    user = await make_user(db, "wanderer")
    series = await _series(db, user, min_distance_m=0, min_interval_s=0)
    now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    await _place(db, user, "zuhause", *HIER, radius=150)

    await service.ingest(db, series, [_p(50.5, 11.5, ts=now)])          # weit weg
    e = await service.ingest(db, series, [_p(*HIER, ts=now - dt.timedelta(days=2))])
    await db.commit()
    assert e["betreten"] == []
