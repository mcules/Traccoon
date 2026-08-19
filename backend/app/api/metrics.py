"""Look at metric series: the numbers flows write along.

Conclusions are drawn from the history, not from the last value. That is why the overview
delivers where a series is heading along with it, and the single view the points themselves;
more is not needed in order to see whether a forecast is credible.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..db import get_session
from ..models.metrics import MetricPoint, MetricSeries
from ..models.user import User
from ..services import metrics
from .deps import get_current_user

router = APIRouter(tags=["metrics"])


def _reihe_out(r: MetricSeries, stand: dict | None = None) -> dict:
    return {"id": r.id, "key": r.key, "name": r.name or r.key, "unit": r.unit,
            "description": r.description,
            "last_value": r.last_value,
            "last_at": metrics._mit_zone(r.last_at).isoformat() if r.last_at else None,
            "warned_at": metrics._mit_zone(r.warned_at).isoformat() if r.warned_at else None,
            "trend": stand}


@router.get("/metrics")
async def list_series(mit_trend: bool = Query(True), ziel: float = Query(0.0),
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """One's own series. Nobody sees foreign ones: they are operating data of their devices."""
    rows = (await db.execute(select(MetricSeries)
                             .where(MetricSeries.owner_user_id == user.id)
                             .order_by(MetricSeries.key))).scalars().all()
    return [_reihe_out(r, await metrics.trend(db, r, ziel=ziel) if mit_trend else None)
            for r in rows]


@router.get("/metrics/{key:path}/punkte")
async def series_points(key: str, tage: int = Query(60, ge=1, le=3650),
                        ziel: float = Query(0.0),
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """The points of a series in the chosen period, including the trend to the same target value.

    The period applies to BOTH: what is shown and what is computed is the same window.
    Otherwise the view draws a line that does not fit the visible points, and one doubts the
    number instead of the axis.
    """
    r = await metrics.reihe(db, user.id, key)
    if r is None:
        raise Fehler(status.HTTP_404_NOT_FOUND, "err.metric_series_not_found",
                     "Metric series not found")
    seit = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=tage)
    ps = await metrics.punkte(db, r.id, seit=seit)
    return {**_reihe_out(r, await metrics.trend(db, r, ziel=ziel, fenster_tage=tage)),
            "ziel": ziel,
            "punkte": [{"id": p.id, "ts": metrics._mit_zone(p.ts).isoformat(),
                        "wert": p.value, "kontext": p.context or {}} for p in ps]}


@router.delete("/metrics/{key:path}/punkte/{punkt_id}", status_code=204)
async def delete_point(key: str, punkt_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Remove a single value.

    A device reports nonsense at some point that no plausibility limit catches, and a single
    outlier bends the line and with it the forecast. Without this path the only option would
    be to throw the whole series away and the history with it.
    """
    r = await metrics.reihe(db, user.id, key)
    if r is None:
        raise Fehler(status.HTTP_404_NOT_FOUND, "err.metric_series_not_found",
                     "Metric series not found")
    punkt = await db.get(MetricPoint, punkt_id)
    if punkt is None or punkt.series_id != r.id:
        raise Fehler(status.HTTP_404_NOT_FOUND, "err.value_does_not_belong_series",
                     "The value does not belong to this series")
    await db.delete(punkt)
    await db.flush()
    # The head of the series points at the last value; if that was it, it has to move up.
    letzter = (await db.execute(
        select(MetricPoint).where(MetricPoint.series_id == r.id)
        .order_by(MetricPoint.ts.desc()).limit(1))).scalars().first()
    r.last_value = letzter.value if letzter else None
    r.last_at = letzter.ts if letzter else None
    await db.commit()


@router.delete("/metrics/{key:path}", status_code=204)
async def delete_series(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    r = await metrics.reihe(db, user.id, key)
    if r is not None:
        await db.execute(MetricPoint.__table__.delete().where(MetricPoint.series_id == r.id))
        await db.delete(r)
        await db.commit()
