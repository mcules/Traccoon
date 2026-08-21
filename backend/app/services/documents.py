"""Was eine Ablage kann: eine Fassung hinlegen, die letzte holen, alte vergessen.

Das Gegenstück zu `services/metrics.py` für Texte. Bewusst schlicht: kein Format, keine
Umwandlung, keine Suche — eine Fassung ist Überschrift plus Text plus Zeitpunkt.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.documents import DocEntry, DocSeries

log = logging.getLogger("traccoon.documents")

STD_BEHALTEN = 60


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


async def ablage(db: AsyncSession, owner_id: int | None, key: str, *, create: bool = False,
                 name: str = "", behalten: int = 0) -> DocSeries | None:
    """Die Ablage zu diesem Schlüssel; mit `anlegen` entsteht sie beim ersten Schreiben."""
    a = (await db.execute(select(DocSeries).where(
        DocSeries.owner_user_id == owner_id, DocSeries.key == key))).scalars().first()
    if a is None and create:
        a = DocSeries(owner_user_id=owner_id, key=key, name=name or key,
                      keep=behalten or STD_BEHALTEN)
        db.add(a)
        await db.flush()
    if a is not None and behalten and a.keep != behalten:
        a.keep = behalten
    return a


async def hinlegen(db: AsyncSession, owner_id: int | None, key: str, *, title: str, text: str,
                   format: str = "markdown", name: str = "", behalten: int = 0,
                   context: dict | None = None) -> DocEntry:
    """Eine neue Fassung ablegen. Legt die Ablage an, wenn es sie noch nicht gibt."""
    a = await ablage(db, owner_id, key, create=True, name=name, behalten=behalten)
    entry = DocEntry(series_id=a.id, title=title[:300], body=text,
                       format=format or "markdown", context=context or {})
    db.add(entry)
    await db.flush()
    a.last_at, a.last_title = entry.ts or _now(), entry.title
    await _prune(db, a)
    return entry


async def last(db: AsyncSession, owner_id: int | None, key: str) -> DocEntry | None:
    a = await ablage(db, owner_id, key)
    if a is None:
        return None
    return (await db.execute(select(DocEntry).where(DocEntry.series_id == a.id)
                             .order_by(DocEntry.id.desc()).limit(1))).scalars().first()


async def _prune(db: AsyncSession, a: DocSeries) -> None:
    """Alte Fassungen vergessen — sonst wächst ein täglicher Rückblick ohne Ende."""
    limit = max(1, int(a.keep or STD_BEHALTEN))
    alte = (await db.execute(select(DocEntry).where(DocEntry.series_id == a.id)
                             .order_by(DocEntry.id.desc()).offset(limit))).scalars().all()
    for e in alte:
        await db.delete(e)
    if alte:
        log.info("Ablage %s: %s alte Fassungen vergessen", a.key, len(alte))
