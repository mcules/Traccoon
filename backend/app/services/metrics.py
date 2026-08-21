"""What a measurement series tells you.

The core is a line through the last points: how much changes per day, and when is the
target value reached? Battery levels, fill levels, disk space, supplies, anywhere a value
moves steadily in one direction and you do not want to wait for the impact.

Deliberately plain. No model that learns weekly rhythms, just the line a person would draw
through the points with a ruler. It can be explained: you can check for yourself why the
warning came, and where the assumption does not hold (jumpy values), the coefficient of
determination says so.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.metrics import MetricPoint, MetricSeries

log = logging.getLogger("traccoon.metrics")

WINDOW_DAYS = 30       # how far back the trend reads
MIN_POINTS = 3          # below that, any line is coincidence
# And below this it extrapolates noise: four voltage readings from three minutes produced
# "+14 V per day". There has to be real time between the first and the last point.
MIN_SPAN_DAYS = 0.5
# How far a value has to rise for the series to count as refilled (new battery, topped up
# tank), which also expires a warning that was already sent.
AUFFUELL_SPRUNG = 10.0


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _with_zone(ts: dt.datetime) -> dt.datetime:
    """Read a naive timestamp as UTC. SQLite hands them back without a zone."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


async def series(db: AsyncSession, owner_id: int | None, key: str,
                *, create: bool = False, name: str = "", unit: str = "") -> MetricSeries | None:
    r = (await db.execute(select(MetricSeries).where(
        MetricSeries.owner_user_id == owner_id, MetricSeries.key == key))).scalar_one_or_none()
    if r is None and create:
        r = MetricSeries(owner_user_id=owner_id, key=key, name=name or key, unit=unit)
        db.add(r)
        await db.flush()
    return r


async def record(db: AsyncSession, owner_id: int | None, key: str, value: float, *,
                   name: str = "", unit: str = "", ts: dt.datetime | None = None,
                   context: dict | None = None) -> tuple[MetricSeries, MetricPoint]:
    """Record a value. The series comes into existence with its first value."""
    r = await series(db, owner_id, key, create=True, name=name, unit=unit)
    if name and not r.name:
        r.name = name
    if unit and not r.unit:
        r.unit = unit
    point = MetricPoint(series_id=r.id, ts=ts or _now(), value=float(value),
                        context=context or {})
    db.add(point)
    # A clear rise means somebody refilled it, so the old warning no longer applies.
    if r.last_value is not None and float(value) - r.last_value >= AUFFUELL_SPRUNG:
        r.warned_at = None
        r.warned_value = None
    # Any value ends a phase of silence, even a bad one. The next silence may report
    # again.
    r.still_at = None
    # The head points at the value that is last IN TIME, not at the one entered last.
    # Otherwise a backfilled old value makes the series look current: the chart showed a
    # reading from two days ago as "now", and the forecast started from there.
    if r.last_at is None or _with_zone(point.ts) >= _with_zone(r.last_at):
        r.last_value = float(value)
        r.last_at = point.ts
    await db.flush()
    return r, point


async def points(db: AsyncSession, series_id: int, *, since: dt.datetime | None = None,
                 limit: int = 500) -> list[MetricPoint]:
    q = select(MetricPoint).where(MetricPoint.series_id == series_id)
    if since is not None:
        q = q.where(MetricPoint.ts >= since)
    return list((await db.execute(q.order_by(MetricPoint.ts.desc()).limit(limit)))
                .scalars().all())[::-1]


def line_fit(values: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares line y = a*x + b over (x=days, y=value), returns (a, b, r squared)."""
    n = len(values)
    if n < 2:
        return 0.0, (values[0][1] if values else 0.0), 0.0
    sx = sum(x for x, _ in values)
    sy = sum(y for _, y in values)
    sxx = sum(x * x for x, _ in values)
    sxy = sum(x * y for x, y in values)
    denominator = n * sxx - sx * sx
    if abs(denominator) < 1e-9:
        return 0.0, sy / n, 0.0
    a = (n * sxy - sx * sy) / denominator
    b = (sy - a * sx) / n
    mean = sy / n
    ss_dead = sum((y - mean) ** 2 for _, y in values)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in values)
    r2 = 1.0 - (ss_res / ss_dead) if ss_dead > 1e-12 else 1.0
    return a, b, max(0.0, min(1.0, r2))


async def trend(db: AsyncSession, r: MetricSeries, *, target: float = 0.0,
                window_days: int = WINDOW_DAYS) -> dict:
    """Where the series is heading, and when it reaches the target value.

    `rest_tage` stays None as long as no direction can be read: too few points, or the
    series moves away from the target. No number is more honest than one made up from two
    readings.
    """
    since = _now() - dt.timedelta(days=window_days)
    ps = await points(db, r.id, since=since)
    # The age of the last value belongs to every answer, including the short ones. A
    # series that has been quiet for weeks otherwise keeps serving its old line, and nobody
    # notices that it stopped being fed long ago.
    last = _with_zone(r.last_at) if r.last_at else None
    result = {"points": len(ps), "value": r.last_value, "unit": r.unit,
                "per_day": None, "days_left": None, "empty_at": None, "fit": None,
                "last_at": last.isoformat() if last else None,
                "age_hours": (round((_now() - last).total_seconds() / 3600.0, 2)
                                  if last else None),
                "first_value": ps[0].value if ps else None,
                "first_at": _with_zone(ps[0].ts).isoformat() if ps else None}
    if len(ps) < MIN_POINTS:
        return result
    basis = _with_zone(ps[0].ts)
    values = [((_with_zone(p.ts) - basis).total_seconds() / 86400.0, p.value) for p in ps]
    result["spanne_tage"] = round(values[-1][0], 2)
    if values[-1][0] < MIN_SPAN_DAYS:
        return result
    a, b, r2 = line_fit(values)
    result["per_day"] = round(a, 4)
    result["fit"] = round(r2, 3)
    now_x = (_now() - basis).total_seconds() / 86400.0
    current = a * now_x + b
    if abs(a) > 1e-9:
        remainder = (target - current) / a
        if remainder >= 0:
            result["days_left"] = round(remainder, 1)
            result["empty_at"] = (_now() + dt.timedelta(days=remainder)).date().isoformat()
    return result


def forewarn(r: MetricSeries, remainder_days: float | None, forewarn_days: float) -> bool:
    """Whether to warn NOW, exactly once per refill.

    Without that, a device reporting daily would produce the same warning every day. After
    three days you mute it and miss the one that mattered. When the value rises again
    (`erfassen`), the mark expires, so a new battery may warn again.
    """
    if remainder_days is None or remainder_days > forewarn_days:
        return False
    if r.warned_at is not None:
        return False
    r.warned_at = _now()
    r.warned_value = r.last_value
    return True


def silence_report(r: MetricSeries, age_hours: float | None,
                  threshold_hours: float) -> bool:
    """Whether to report NOW that the series went quiet, once per phase of silence.

    Built like `vorwarnen` and for the same reason: an hourly watchdog must not say the
    same thing every hour. The mark sits on the series, not in the flow, because it
    describes the state of the series and has to survive a restart.

    This is the counterpart to the forecast. The forecast says when something runs out,
    this notices that nothing arrives at all, including the case where the far side is down
    and can no longer even report its own failure.
    """
    if age_hours is None or threshold_hours <= 0 or age_hours < threshold_hours:
        return False
    if r.still_at is not None:
        return False
    r.still_at = _now()
    return True
