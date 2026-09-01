"""Take points in — and notice when a place is entered or left in the process.

The structure follows `services/metrics.erfassen`: a thin layer that writes and updates the
denormalised state. Three things are added for locations, and each of them has
einen handfesten Grund:

1. **Discarding instead of raising.** A phone occasionally reports a position with 2 km of
   inaccuracy or a timestamp from the future. That is not a fault of the sender but the daily
   life of GPS — the call succeeds, the point is dropped.
2. **Rest filter.** Without it the table grows just as fast at the desk at night as on the
   motorway: a device that sends the same coordinate every four minutes produces 375 rows a
   day that tell nothing.
3. **Geofence.** After writing, a comparison is made between the places the device stands in
   now and the ones from before. The difference is reported as an event — and with that a
   location is what it is stored for here at all: a trigger like a mail.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import encrypt_secret
from ..models.series import Series, SeriesPlace, SeriesPoint, SeriesShare
from . import geo
from .events import emit

log = logging.getLogger("series")

# Defaults of a location device. They sit in `Series.settings` and can be overridden per
# change; a car may report more often than a phone.
DEFAULTS_LOCATION = {
    "min_distance_m": 25,     # Closer to the last point: do not write …
    "min_interval_s": 300,    # … unless it has been this long.
    "max_accuracy_m": 500,    # Ungenauer: verwerfen.
}
# Only this far out does a place count as left. Without this margin a device at the edge of a
# fence flutters between "in" and "out" and fires a flow every time.
HYSTERESIS_M = 50
# Further into the future than this is not a timestamp but a broken clock.
FUTURE_TOLERANCE = dt.timedelta(minutes=5)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _with_zone(value: dt.datetime | None) -> dt.datetime | None:
    """Read naive timestamps as UTC (SQLite in the tests returns them without a zone)."""
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=dt.timezone.utc)


def setting(series: Series, name: str) -> int:
    return int((series.settings or {}).get(name, DEFAULTS_LOCATION.get(name, 0)))


# ── Token ────────────────────────────────────────────────────────────────────

def token_hash(raw: str) -> str:
    """The lookup key for a token.

    sha256 instead of a comparison over all series: with two devices both would be the same
    fast, but the ingest path is the one that runs more often than anything else in the house.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token(series: Series) -> str:
    """Set a fresh token and return it in the clear — once."""
    raw = "trk_" + secrets.token_urlsafe(24)
    series.token_hash = token_hash(raw)
    series.token_enc = encrypt_secret(raw)
    return raw


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

def visible(user_id: int, is_admin: bool = False):
    """The filter for the series this person may see: their own, shared ones, global ones."""
    if is_admin:
        return True
    shared = select(SeriesShare.series_id).where(SeriesShare.user_id == user_id)
    return or_(Series.owner_user_id == user_id, Series.owner_user_id.is_(None),
               Series.id.in_(shared))


async def may_update(db: AsyncSession, series: Series, user_id: int,
                       is_admin: bool = False) -> bool:
    if is_admin or series.owner_user_id == user_id:
        return True
    grant = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == series.id, SeriesShare.user_id == user_id))).scalar_one_or_none()
    return bool(grant and grant.level == "manage")


# ── Finden und Anlegen ───────────────────────────────────────────────────────

async def series(db: AsyncSession, owner_id: int | None, key: str, *, kind: str = "location",
                create: bool = False, name: str = "", color: str = "") -> Series | None:
    """Fetch a series — on request creating it with the first point.

    As with the metric series: a flow should not have to get a series created by hand first.
    Whoever writes a position away also means that this series is to exist.
    """
    r = (await db.execute(select(Series).where(
        Series.owner_user_id == owner_id, Series.key == key))).scalar_one_or_none()
    if r is None and create:
        r = Series(owner_user_id=owner_id, key=key, kind=kind, name=name or key, color=color)
        db.add(r)
        await db.flush()
    return r


# ── Aufnehmen ────────────────────────────────────────────────────────────────

async def ingest(db: AsyncSession, series: Series, points: list[dict],
                    source: str = "") -> dict:
    """Punkte anhaengen — welche Art, entscheidet die Reihe.

    Appending itself is the same for all kinds: timestamp, values, update the state. What
    differs is only what makes up a point (a number, a place, a text) and what additionally
    happens along the way — the rest filter and the geofences exist for locations only, because
    only there does the same entry arrive a hundred times in a row.
    """
    if series.kind == "location":
        return await _locations_ingest(db, series, points, source)
    return await _values_ingest(db, series, points, source)


async def _values_ingest(db: AsyncSession, series: Series, points: list[dict],
                           source: str) -> dict:
    """Numbers and texts: write, update the state, prune with texts.

    No rest filter: a measurement that has not changed is still a statement ("the device is
    alive and reports 22 %"), and a text is a new one every time anyway.
    """
    limits = series.settings or {}
    below, above = limits.get("min"), limits.get("max")
    written, discarded = 0, 0
    last, last_ts = None, None

    for p in sorted(points, key=lambda x: x.get("ts") or _now()):
        ts = _with_zone(p.get("ts")) or _now()
        entry = SeriesPoint(series_id=series.id, ts=ts, source=p.get("source") or source,
                              context=p.get("context") or {})
        if series.kind == "number":
            value = p.get("value")
            if value is None:
                discarded += 1
                continue
            # Plausibility limits as with the measurement: devices report nonsense when they
            # do not know something — and an outlier warps every evaluation afterwards.
            if (below is not None and value < below) or (above is not None and value > above):
                discarded += 1
                continue
            entry.value = float(value)
        else:
            text = str(p.get("body") or "")
            if not text.strip():
                discarded += 1
                continue
            entry.title = str(p.get("title") or "")[:200]
            entry.body = text
            entry.format = str(p.get("format") or "markdown")
        db.add(entry)
        written += 1
        last, last_ts = entry, ts

    series.points = (series.points or 0) + written
    if last is not None:
        sofar = _with_zone(series.last_at)
        if sofar is None or last_ts >= sofar:
            state = dict(series.state or {})
            if series.kind == "number":
                state["value"] = last.value
            else:
                state["title"] = last.title
            series.state = state
            series.last_at = last_ts
            series.still_at = None
        await _prune(db, series)

    return {"accepted": written, "skipped": discarded, "still": 0,
            "betreten": [], "verlassen": []}


async def _prune(db: AsyncSession, series: Series) -> None:
    """Throw old entries away when the series names an upper limit.

    Only by count and only when `keep` is set. A time limit would be the wrong thing for
    locations (a year of trace is the whole point), and without an entry nothing is deleted
    — stilles Verschwinden von Daten will niemand geerbt bekommen.
    """
    limit = int((series.settings or {}).get("keep") or 0)
    if limit <= 0:
        return
    old = (await db.execute(select(SeriesPoint).where(SeriesPoint.series_id == series.id)
                             .order_by(SeriesPoint.id.desc()).offset(limit))).scalars().all()
    for e in old:
        await db.delete(e)
    if old:
        series.points = max(0, (series.points or 0) - len(old))


async def _locations_ingest(db: AsyncSession, series: Series, points: list[dict],
                               source: str = "") -> dict:
    """Locations: with a rest filter and a geofence."""
    exactly_enough = setting(series, "max_accuracy_m")
    min_m = setting(series, "min_distance_m")
    min_s = setting(series, "min_interval_s")
    future = _now() + FUTURE_TOLERANCE

    state = dict(series.state or {})
    last_ts = _with_zone(series.last_at)
    last_lat, last_lon = state.get("lat"), state.get("lon")
    written, discarded, rested = 0, 0, 0
    last_point = None
    # The last *seen* valid position — regardless of whether it was stored as well. The rest
    # filter decides about the storing, not about where the device is: whoever walks across a
    # fence boundary on foot does not cover 25 m in five minutes and would otherwise count as
    # having arrived much later.
    seen: tuple[float, float, dt.datetime] | None = None

    # Feed in sorted by time: a batch does not necessarily arrive ordered, and the rest filter
    # always compares against the last written point.
    for p in sorted(points, key=lambda x: x.get("ts") or _now()):
        lat, lon = p.get("lat"), p.get("lon")
        ts = _with_zone(p.get("ts")) or _now()
        accuracy = (p.get("extra") or {}).get("accuracy")

        if lat is None or lon is None or (lat == 0 and lon == 0):
            discarded += 1
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            discarded += 1
            continue
        if exactly_enough and accuracy is not None and accuracy > exactly_enough:
            discarded += 1
            continue
        if ts > future:
            discarded += 1
            continue

        seen = (lat, lon, ts)

        # Rest filter: close by AND recent -> do not write.
        if last_lat is not None and last_lon is not None:
            path = geo.distance_m(last_lat, last_lon, lat, lon)
            old = (ts - last_ts).total_seconds() if last_ts else None
            if path < min_m and (old is None or 0 <= old < min_s):
                rested += 1
                continue

        point = SeriesPoint(
            series_id=series.id, ts=ts, lat=lat, lon=lon,
            extra=p.get("extra") or {}, source=p.get("source") or source,
            context={})
        db.add(point)
        written += 1
        last_point = point
        last_lat, last_lon, last_ts = lat, lon, ts

    series.points = (series.points or 0) + written

    places = {"betreten": [], "verlassen": []}
    if last_point is not None:
        # Update the state only when the new point really is the more recent one: a backfill
        # from last year must not count as the current position.
        sofar = _with_zone(series.last_at)
        if sofar is None or last_ts >= sofar:
            extra = last_point.extra or {}
            state = {**state, "lat": last_lat, "lon": last_lon,
                     "accuracy": extra.get("accuracy"), "battery": extra.get("battery"),
                     "speed": extra.get("speed")}
            series.state = state
            series.last_at = last_ts
            series.still_at = None

    if seen is not None:
        lat, lon, ts = seen
        # `seen_at` stands next to `last_at`: the device reported just now, while maybe nothing
        # has been stored for an hour. Both are pieces of information of their own — the map
        # shows the last point, the monitoring wants to know whether anything still arrives.
        series.state = {**state, "seen_at": ts.isoformat()}
        series.still_at = None
        sofar = _with_zone(series.last_at)
        # Check fences only at the most recent known spot: a backfill from yesterday must not
        # report that one has just arrived.
        if sofar is None or ts >= sofar:
            places = await _fences_check(db, series, lat, lon, ts)

    return {"accepted": written, "skipped": discarded, "still": rested, **places}


async def _fences_check(db: AsyncSession, series: Series, lat: float, lon: float,
                          ts: dt.datetime) -> dict:
    """Which places have been added, which left — and report events for that."""
    places = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == series.owner_user_id,
        or_(SeriesPlace.series_id.is_(None), SeriesPlace.series_id == series.id),
    ))).scalars().all()
    if not places:
        return {"betreten": [], "verlassen": []}

    before = set((series.state or {}).get("places") or [])
    now: set[str] = set()
    for place in places:
        path = geo.distance_m(lat, lon, place.lat, place.lon)
        # In from the radius on, out only later — otherwise it flutters at the edge.
        inside = path <= place.radius_m if place.key not in before else path <= place.radius_m + HYSTERESIS_M
        if inside:
            now.add(place.key)

    enter = [o for o in places if o.key in now - before]
    leave = [o for o in places if o.key in before - now]
    series.state = {**(series.state or {}), "places": sorted(now)}

    for place, name in ((o, "series.enter") for o in enter):
        await _report(db, series, place, name, lat, lon, ts)
    for place, name in ((o, "series.leave") for o in leave):
        await _report(db, series, place, name, lat, lon, ts)

    return {"betreten": [o.key for o in enter], "verlassen": [o.key for o in leave]}


async def _report(db: AsyncSession, series: Series, place: SeriesPlace, event: str,
                  lat: float, lon: float, ts: dt.datetime) -> None:
    """Emit an event — without an error in it toppling the intake.

    A point is a point first of all. If a flow fails on it, the database should still hold
    where the device was.
    """
    if not place.notify:
        return
    try:
        await emit(db, event, payload={
            "series": {"key": series.key, "name": series.name, "id": series.id},
            "place": {"key": place.key, "name": place.name or place.key,
                      "lat": place.lat, "lon": place.lon, "radius_m": place.radius_m},
            "lat": lat, "lon": lon, "ts": ts.isoformat(),
        }, actor_id=series.owner_user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ereignis %s fuer %s/%s nicht gemeldet: %s",
                    event, series.key, place.key, exc)

# ── Lesen ────────────────────────────────────────────────────────────────────

# The maths and the thresholds come from `services/metrics`: the same question over the same
# shape of data, and two copies of a least squares fit would drift apart the first time one
# of them is corrected. Only the table underneath differs.
from .metrics import MIN_POINTS, MIN_SPAN_DAYS, WINDOW_DAYS, line_fit  # noqa: E402
from .metrics import silence_report  # noqa: E402  (`still_at` sits on this row as well)


async def points(db: AsyncSession, series_id: int, *, since: dt.datetime | None = None,
                 limit: int = 500) -> list[SeriesPoint]:
    question = select(SeriesPoint).where(SeriesPoint.series_id == series_id)
    if since is not None:
        question = question.where(SeriesPoint.ts >= since)
    return list((await db.execute(question.order_by(SeriesPoint.ts.desc()).limit(limit)))
                .scalars().all())[::-1]


async def trend(db: AsyncSession, series: Series, *, target: float = 0.0,
                window_days: int = WINDOW_DAYS) -> dict:
    """Where a number series is heading, and when it reaches the target value.

    The counterpart of `metrics.trend` for the kind that replaced it. `days_left` stays None
    as long as no direction can be read: too few points, too short a span, or a series moving
    away from its target. No number is more honest than one invented out of two readings.

    The age of the last value belongs to every answer, including the short ones: a series
    that has been quiet for weeks would otherwise keep serving its old line, and nobody would
    notice that it stopped being fed.
    """
    state = series.state or {}
    since = _now() - dt.timedelta(days=window_days)
    ps = [p for p in await points(db, series.id, since=since) if p.value is not None]
    last = _with_zone(series.last_at) if series.last_at else None
    out = {
        "points": len(ps),
        "value": state.get("value"),
        "unit": (series.settings or {}).get("unit", ""),
        "per_day": None, "days_left": None, "empty_at": None, "fit": None,
        "last_at": last.isoformat() if last else None,
        "age_hours": (round((_now() - last).total_seconds() / 3600.0, 2) if last else None),
        "first_value": ps[0].value if ps else None,
        "first_at": _with_zone(ps[0].ts).isoformat() if ps else None,
    }
    if len(ps) < MIN_POINTS:
        return out
    base = _with_zone(ps[0].ts)
    values = [((_with_zone(p.ts) - base).total_seconds() / 86400.0, p.value) for p in ps]
    out["span_days"] = round(values[-1][0], 2)
    if values[-1][0] < MIN_SPAN_DAYS:
        return out
    a, b, r2 = line_fit(values)
    out["per_day"] = round(a, 4)
    out["fit"] = round(r2, 3)
    now_x = (_now() - base).total_seconds() / 86400.0
    current = a * now_x + b
    if abs(a) > 1e-9:
        remainder = (target - current) / a
        if remainder >= 0:
            out["days_left"] = round(remainder, 1)
            out["empty_at"] = (_now() + dt.timedelta(days=remainder)).date().isoformat()
    return out


async def latest(db: AsyncSession, series: Series) -> dict:
    """The newest entry of a text series: what a store's `document_read` answered.

    The age comes along because it is the same question a number series answers with its
    trend: when did something last arrive. Computed here, where the clock already is.
    """
    rows = await points(db, series.id, limit=1)
    if not rows:
        return {"title": "", "body": "", "format": "", "ts": None, "age_hours": None,
                "points": series.points or 0}
    p = rows[-1]
    ts = _with_zone(p.ts) if p.ts else None
    return {
        "title": p.title, "body": p.body, "format": p.format,
        "ts": ts.isoformat() if ts else None,
        "age_hours": (round((_now() - ts).total_seconds() / 3600.0, 2) if ts else None),
        "points": series.points or 0,
    }
