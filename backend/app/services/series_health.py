"""Health readings from a phone: one payload, many series.

A tracker reports one thing (where it is), so its token names one series and the endpoint
never has to ask which. A phone that mirrors a watch reports twenty things at once, and they
do not belong in one series: a blood pressure and a step count share a timestamp and nothing
else. Handing out twenty tokens for that would put twenty secrets into one configuration
screen, so the payload names the series itself and the caller proves who it is with a
personal access token instead.

The payload:

    {"device": "phone", "points": [
        {"series": "health.heart-rate", "ts": "2026-08-31T23:36:00+02:00",
         "value": 94, "unit": "bpm", "source": "health-connect"}]}

**Why a catalogue.** The keys are known in advance, and with them the kind, the unit and the
range a reading can plausibly lie in. That is what turns a typo into a discarded point
instead of a series nobody asked for, and it is what gives an automatically created series a
unit without the sender having to be trusted about it. A key that is not in the table is
still taken as long as it is a `health.` one, because a new record type in the app should not
wait for a release here; it then gets the unit the sender names and no plausibility limits.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .series_formats import moment

# What every automatically created series has to start with. Without this a leaked token
# could sow the series list full of whatever it likes.
PREFIX = "health."

# key: kind, name, unit, plausible range. The range lands in `Series.settings` as `min`/`max`
# and is applied by the intake itself (`_values_ingest`), so a sensor that reports 0 mmHg
# because it lost contact does not warp every chart afterwards.
CATALOGUE: dict[str, dict[str, Any]] = {
    "health.blood-pressure-systolic": {
        "name": "Blood pressure systolic", "unit": "mmHg", "min": 40, "max": 300},
    "health.blood-pressure-diastolic": {
        "name": "Blood pressure diastolic", "unit": "mmHg", "min": 20, "max": 200},
    "health.heart-rate": {"name": "Heart rate", "unit": "bpm", "min": 20, "max": 250},
    "health.resting-heart-rate": {
        "name": "Resting heart rate", "unit": "bpm", "min": 20, "max": 150},
    "health.hrv": {"name": "Heart rate variability", "unit": "ms", "min": 0, "max": 500},
    "health.spo2": {"name": "Oxygen saturation", "unit": "%", "min": 50, "max": 100},
    "health.respiratory-rate": {
        "name": "Respiratory rate", "unit": "/min", "min": 3, "max": 80},
    "health.body-temperature": {
        "name": "Body temperature", "unit": "degC", "min": 25, "max": 45},
    # Health Connect reports the skin temperature as a deviation from a baseline, not as an
    # absolute value, so this series is negative most of the time.
    "health.skin-temperature": {
        "name": "Skin temperature delta", "unit": "K", "min": -20, "max": 20},
    "health.weight": {"name": "Weight", "unit": "kg", "min": 20, "max": 400},
    "health.body-fat": {"name": "Body fat", "unit": "%", "min": 1, "max": 80},
    "health.muscle-mass": {"name": "Muscle mass", "unit": "kg", "min": 1, "max": 150},
    "health.body-water": {"name": "Body water", "unit": "l", "min": 1, "max": 100},
    "health.bmi": {"name": "Body mass index", "unit": "kg/m2", "min": 5, "max": 100},
    "health.steps": {"name": "Steps", "unit": "steps", "min": 0, "max": 200000},
    "health.distance": {"name": "Distance", "unit": "m", "min": 0, "max": 1000000},
    "health.speed": {"name": "Speed", "unit": "m/s", "min": 0, "max": 150},
    "health.calories-total": {
        "name": "Calories burned total", "unit": "kcal", "min": 0, "max": 30000},
    "health.calories-active": {
        "name": "Calories burned active", "unit": "kcal", "min": 0, "max": 30000},
    "health.floors": {"name": "Floors climbed", "unit": "floors", "min": 0, "max": 2000},
    "health.hydration": {"name": "Hydration", "unit": "l", "min": 0, "max": 30},
    "health.blood-glucose": {
        "name": "Blood glucose", "unit": "mg/dl", "min": 10, "max": 1000},
    # A night and a workout are not a number. They keep their stages and their laps as JSON in
    # the body of a text point, because carving them into columns would mean a table per sport.
    "health.sleep": {"name": "Sleep", "kind": "text"},
    "health.exercise": {"name": "Exercise", "kind": "text"},
}


def looks_like(payload: Any) -> bool:
    """Is this the health shape? A `points` list whose entries name their series."""
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
        return False
    return any(isinstance(p, dict) and str(p.get("series") or "").strip()
               for p in payload["points"])


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def settings_for(key: str, unit: str = "") -> dict[str, Any]:
    """What an automatically created series starts with."""
    entry = CATALOGUE.get(key) or {}
    out: dict[str, Any] = {"unit": entry.get("unit", unit or "")}
    for limit in ("min", "max"):
        if limit in entry:
            out[limit] = entry[limit]
    return out


def kind_of(key: str, point: dict) -> str:
    """`number` or `text`. The catalogue decides, and where it is silent the point does."""
    entry = CATALOGUE.get(key)
    if entry:
        return str(entry.get("kind", "number"))
    return "text" if point.get("body") is not None else "number"


def name_of(key: str) -> str:
    entry = CATALOGUE.get(key) or {}
    return str(entry.get("name") or key)


def normalise(payload: Any) -> dict[str, list[dict]]:
    """The payload sorted into a bundle of points per series key.

    Unusable entries are dropped instead of raising: a phone that sends one broken reading in
    a batch of five hundred should not have the other 499 rejected, and a 400 would put the
    app into a retry loop over a point that will never get better.
    """
    out: dict[str, list[dict]] = {}
    if not isinstance(payload, dict):
        return out
    device = str(payload.get("device") or "").strip()

    for raw in payload.get("points") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("series") or "").strip().lower()
        if not key.startswith(PREFIX):
            continue
        ts = moment(raw.get("ts"))
        if ts is None:
            continue
        # Everything to UTC before it is written. The phone reports in its local offset, and
        # the same instant sent twice must produce the same row for the duplicate check to
        # recognise it.
        ts = ts.astimezone(dt.timezone.utc)

        context = dict(raw.get("context") or {})
        if device:
            context.setdefault("device", device)
        point: dict[str, Any] = {
            "ts": ts,
            "source": str(raw.get("source") or "health")[:30],
            "context": context,
        }

        if kind_of(key, raw) == "text":
            body = raw.get("body")
            if body is None:
                continue
            point["title"] = str(raw.get("title") or "")
            point["body"] = body if isinstance(body, str) else str(body)
            point["format"] = str(raw.get("format") or "json")
        else:
            value = _number(raw.get("value"))
            if value is None:
                continue
            point["value"] = value
            # The unit travels with the first point of an unknown series and is put on the
            # series, not on every row: it is a property of what is measured, not of the
            # single reading.
            if raw.get("unit"):
                point["unit"] = str(raw["unit"])[:20]

        out.setdefault(key, []).append(point)
    return out
