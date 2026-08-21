"""Adopt the location stock out of dawarich.

dawarich was the way station as long as Traccoon knew no locations itself. What lies there are
two very different things: the trace of the phone of the last few days and a Google history
uploaded by hand that reaches back to 2012. Both are to be kept, in separate series — the
Google stock is an archive, not a device that still reports.

The rest filter deliberately stays off here: what has already been stored is the decision of
back then. Applying it retroactively would mean thinning out foreign data, and in a Google
history with gaps of hours the wrong thing gets dropped.

Aufruf (im Backend-Container):

    python scripts/import_dawarich.py <csv> <besitzer-id> [--trocken]

The CSV comes out of dawarich:

    \\copy (select coalesce(tracker_id,'google-verlauf'), timestamp,
                  st_y(lonlat::geometry), st_x(lonlat::geometry),
                  accuracy, altitude, battery, velocity
           from points order by timestamp) to stdout with (format csv)
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select                                   # noqa: E402
from app.db import SessionLocal                                 # noqa: E402
from app.models.series import Series, SeriesPoint               # noqa: E402

# Which `tracker_id` goes into which series, and what it is called.
SERIES = {
    "s26-ultra": ("tracker.s26-ultra", "S26 Ultra", "#3b82f6"),
    "google-verlauf": ("tracker.google-verlauf", "Google-Verlauf (Archiv)", "#6b7280"),
}
STAPEL = 500


def _ident(ts, lat, lon) -> tuple:
    """How a point is recognised again: the second and the place.

    Rounded to six decimals — that is a good 10 cm, finer than any GPS, and at the same time
    insensitive to the last digit, which on the way through two databases
    schon einmal kippt.
    """
    exactly = ts.replace(tzinfo=None) if ts else None
    return (exactly, round(lat, 6) if lat is not None else None,
            round(lon, 6) if lon is not None else None)


def _number(text: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


async def fetch_series(db, owner: int, key: str, name: str, color: str) -> Series:
    series = (await db.execute(select(Series).where(
        Series.owner_user_id == owner, Series.key == key))).scalar_one_or_none()
    if series is None:
        series = Series(owner_user_id=owner, key=key, kind="location", name=name,
                       color=color, settings={})
        db.add(series)
        await db.flush()
        print(f"  Reihe {key} angelegt")
    return series


async def run_import(csv_path: str, owner: int, dry: bool) -> None:
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    print(f"{len(rows)} Zeilen gelesen")

    async with SessionLocal() as db:
        series: dict[str, Series] = {}
        # What already lies in there, so a second run duplicates nothing. As the identifier
        # serve the timestamp **and** the position: the timestamp alone would be the obvious
        # choice but swallows real points — in the Google stock there are four pairs that share
        # tragen und trotzdem an verschiedenen Orten liegen.
        known: dict[str, set] = {}
        counted = {"neu": 0, "doppelt": 0, "kaputt": 0}

        for i, row in enumerate(rows):
            if len(row) < 4:
                counted["kaputt"] += 1
                continue
            device, ts_raw, lat_raw, lon_raw = row[0], row[1], row[2], row[3]
            if device not in SERIES:
                counted["kaputt"] += 1
                continue

            key, name, color = SERIES[device]
            if key not in series:
                series[key] = await fetch_series(db, owner, key, name, color)
                existing = (await db.execute(select(
                    SeriesPoint.ts, SeriesPoint.lat, SeriesPoint.lon).where(
                    SeriesPoint.series_id == series[key].id))).all()
                known[key] = {_ident(t, la, lo) for t, la, lo in existing if t}

            lat, lon, ts_number = _number(lat_raw), _number(lon_raw), _number(ts_raw)
            if lat is None or lon is None or ts_number is None:
                counted["kaputt"] += 1
                continue
            ts = dt.datetime.fromtimestamp(ts_number, tz=dt.timezone.utc)
            ident = _ident(ts, lat, lon)
            if ident in known[key]:
                counted["doppelt"] += 1
                continue
            known[key].add(ident)

            extra = {}
            for feld, column in (("accuracy", 4), ("altitude", 5), ("battery", 6),
                                 ("speed", 7)):
                value = _number(row[column]) if len(row) > column else None
                if value is not None:
                    extra[feld] = value

            if not dry:
                db.add(SeriesPoint(series_id=series[key].id, ts=ts, lat=lat, lon=lon,
                                   extra=extra, source="import", context={"aus": "dawarich"}))
            counted["neu"] += 1
            if not dry and counted["neu"] % STAPEL == 0:
                await db.flush()
                print(f"  {counted['neu']} …")

        if dry:
            print(f"TROCKEN: {counted}")
            return

        # Update the counter and the latest state — otherwise the overview shows zero points.
        for key, series in series.items():
            points = (await db.execute(select(SeriesPoint).where(
                SeriesPoint.series_id == series.id)
                .order_by(SeriesPoint.ts.desc()).limit(1))).scalars().all()
            series.points = len((await db.execute(select(SeriesPoint.id).where(
                SeriesPoint.series_id == series.id))).scalars().all())
            if points:
                last = points[0]
                ts = last.ts if last.ts.tzinfo else last.ts.replace(
                    tzinfo=dt.timezone.utc)
                if series.last_at is None or ts >= (
                        series.last_at if series.last_at.tzinfo
                        else series.last_at.replace(tzinfo=dt.timezone.utc)):
                    series.state = {**(series.state or {}), "lat": last.lat,
                                   "lon": last.lon, **(last.extra or {})}
                    series.last_at = ts
            print(f"  {key}: {series.points} Punkte, zuletzt {series.last_at}")

        await db.commit()
        print(f"fertig: {counted}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(run_import(sys.argv[1], int(sys.argv[2]), "--trocken" in sys.argv))
