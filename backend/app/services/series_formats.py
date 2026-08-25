"""Four languages, one meaning: what a device reports when it reports its location.

Every app has its own version of the same message. Translating them here instead of in four
endpoints has a simple reason: the difference lies solely in the names of the fields, not in
what they mean. An endpoint that recognises from the content what it is dealing with needs no
setting — one enters the address, and it runs.

Die vier:

* **OwnTracks** — `{"_type":"location","lat":…,"lon":…,"tst":…,"acc":…,"batt":…}`
* **Overland** — `{"locations":[GeoJSON feature,…]}`, a batch; the coordinates stand there in
  the order **lon, lat** — the most common trap when dealing with GeoJSON.
* **Traccar / OsmAnd** — no body, everything in the address: `?id=…&lat=…&lon=…&timestamp=…`
* **flat** — `{"lat":…,"lon":…}` or `{"latitude":…,"longitude":…}`; what Home Assistant sends
  from a template, and what everybody builds themselves.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# A timestamp further back than this is no longer a figure in seconds but one in
# milliseconds: 10^11 seconds would be the year 5138.
_MS_LIMIT = 100_000_000_000


def _number(value: Any) -> float | None:
    """A number out of what arrives — or nothing.

    Devices send numbers as text, with a comma, with a unit behind them, or empty. A `float()`
    with try/except would be too coarse: `"12,5 km/h"` should yield 12.5 and not nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    allowed = "0123456789.-+eE"
    text = "".join(z for z in text if z in allowed).rstrip("eE+-")
    try:
        return float(text)
    except ValueError:
        return None


def moment(value: Any) -> dt.datetime | None:
    """A timestamp from unix seconds, unix milliseconds or ISO text."""
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number <= 0:
            return None
        if number > _MS_LIMIT:
            number /= 1000.0
        try:
            return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # `fromisoformat` only handles `Z` reliably from 3.11 on, and replacing it costs nothing.
        read = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return read if read.tzinfo else read.replace(tzinfo=dt.timezone.utc)


def _battery(value: Any) -> float | None:
    """Akkustand in Prozent.

    OwnTracks reports 0-100, others report 0-1 as a fraction. A value below 1 is therefore
    ambiguous — here it counts as a fraction, because a phone with 0.8 % battery would long
    since be off. Exactly this mix-up once produced 8200 % on the way to dawarich.
    """
    number = _number(value)
    if number is None:
        return None
    if 0 < number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def _fix(lat: Any, lon: Any, ts: Any, *, accuracy=None, altitude=None, speed=None,
         course=None, battery=None, source: str = "", raw: Any = None) -> dict | None:
    """A point in Traccoon's shape — or nothing, if no coordinate is in it."""
    la, lo = _number(lat), _number(lon)
    if la is None or lo is None:
        return None
    extra = {"accuracy": _number(accuracy), "altitude": _number(altitude),
              "speed": _number(speed), "course": _number(course), "battery": _battery(battery)}
    return {
        "lat": la, "lon": lo, "ts": moment(ts),
        # Empty fields are dropped: otherwise every point would hold `null` five times, and
        # with a million points that is a noticeable part of the table.
        "extra": {n: w for n, w in extra.items() if w is not None},
        "source": source,
        "raw": raw,
    }


def normalise(payload: Any, query: dict | None = None) -> list[dict]:
    """Everything that arrives, as a list of points. What is unintelligible yields an empty list."""
    query = {k.lower(): v for k, v in (query or {}).items()}
    data = payload if isinstance(payload, dict) else {}

    # Overland: ein Stapel GeoJSON-Features.
    if isinstance(data.get("locations"), list):
        return _overland(data["locations"])

    # OwnTracks: also reports waypoints and status messages — only locations are of interest.
    if data.get("_type"):
        if data["_type"] not in ("location", "transition"):
            return []
        p = _fix(data.get("lat"), data.get("lon"), data.get("tst"),
                 accuracy=data.get("acc"), altitude=data.get("alt"),
                 speed=data.get("vel"), course=data.get("cog"),
                 battery=data.get("batt"), source="owntracks", raw=data)
        return [p] if p else []

    # Traccar/OsmAnd: everything in the address. Recognisable by the body yielding nothing
    # while the address carries coordinates.
    if not data and ("lat" in query or "latitude" in query):
        p = _fix(query.get("lat") or query.get("latitude"),
                 query.get("lon") or query.get("longitude"),
                 query.get("timestamp") or query.get("ts"),
                 accuracy=query.get("accuracy") or query.get("hdop"),
                 altitude=query.get("altitude") or query.get("altitude_m"),
                 speed=query.get("speed"), course=query.get("bearing") or query.get("heading"),
                 battery=query.get("batt") or query.get("battery"),
                 source="traccar", raw=dict(query))
        return [p] if p else []

    # Flat: home automation and everything home grown.
    p = _fix(data.get("lat", data.get("latitude")),
             data.get("lon", data.get("lng", data.get("longitude"))),
             data.get("ts", data.get("timestamp", data.get("time"))),
             accuracy=data.get("accuracy", data.get("gps_accuracy", data.get("acc"))),
             altitude=data.get("altitude", data.get("alt")),
             speed=data.get("speed"),
             course=data.get("course", data.get("bearing", data.get("heading"))),
             battery=data.get("battery", data.get("batt", data.get("battery_level"))),
             source=str(data.get("source") or "api"), raw=data)
    return [p] if p else []


def _overland(entries: list) -> list[dict]:
    points = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        coord = ((e.get("geometry") or {}).get("coordinates") or [])
        eig = e.get("properties") or {}
        if len(coord) < 2:
            continue
        # GeoJSON zaehlt lon zuerst. Wer das vertauscht, landet mitten im Indischen Ozean.
        p = _fix(coord[1], coord[0], eig.get("timestamp"),
                 accuracy=eig.get("horizontal_accuracy"), altitude=eig.get("altitude"),
                 speed=eig.get("speed"), course=eig.get("course"),
                 battery=eig.get("battery_level"), source="overland", raw=eig)
        if p:
            points.append(p)
    return points
