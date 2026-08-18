"""Messreihen ansehen — die Zahlen, die Abläufe mitschreiben.

Rückschlüsse zieht man aus dem Verlauf, nicht aus dem letzten Wert. Deshalb liefert die
Übersicht zu jeder Reihe gleich mit, wohin sie läuft, und die Einzelansicht die Punkte
selbst — mehr braucht es nicht, um zu sehen, ob eine Prognose glaubwürdig ist.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Die eigenen Reihen. Fremde sieht niemand — es sind Betriebsdaten seiner Geräte."""
    rows = (await db.execute(select(MetricSeries)
                             .where(MetricSeries.owner_user_id == user.id)
                             .order_by(MetricSeries.key))).scalars().all()
    return [_reihe_out(r, await metrics.trend(db, r, ziel=ziel) if mit_trend else None)
            for r in rows]


@router.get("/metrics/{key:path}/punkte")
async def series_points(key: str, tage: int = Query(60, ge=1, le=730),
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    r = await metrics.reihe(db, user.id, key)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Messreihe nicht gefunden")
    seit = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=tage)
    ps = await metrics.punkte(db, r.id, seit=seit)
    return {**_reihe_out(r, await metrics.trend(db, r)),
            "punkte": [{"ts": metrics._mit_zone(p.ts).isoformat(), "wert": p.value}
                       for p in ps]}


@router.delete("/metrics/{key:path}", status_code=204)
async def delete_series(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    r = await metrics.reihe(db, user.id, key)
    if r is not None:
        await db.execute(MetricPoint.__table__.delete().where(MetricPoint.series_id == r.id))
        await db.delete(r)
        await db.commit()
