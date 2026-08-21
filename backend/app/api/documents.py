"""Ablagen ansehen: die Texte, die Abläufe geschrieben haben.

Das Gegenstück zu `api/metrics.py`. Eine Ablage gehört einem Menschen — fremde sieht
niemand, denn darin steht, was seine Agenten für ihn geschrieben haben.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.documents import DocEntry, DocSeries
from ..models.user import User
from ..services import documents, metrics
from .deps import get_current_user

router = APIRouter(tags=["documents"])


def _store_out(a: DocSeries, count: int | None = None) -> dict:
    return {"id": a.id, "key": a.key, "name": a.name or a.key, "description": a.description,
            "keep": a.keep, "last_title": a.last_title,
            "last_at": metrics._with_zone(a.last_at).isoformat() if a.last_at else None,
            "count": count}


def _entry_out(e: DocEntry, with_text: bool = True) -> dict:
    return {"id": e.id, "title": e.title, "format": e.format,
            "ts": metrics._with_zone(e.ts).isoformat() if e.ts else None,
            "context": e.context or {},
            **({"body": e.body} if with_text else {})}


async def _my(db: AsyncSession, user: User, key: str) -> DocSeries:
    a = await documents.store(db, user.id, key)
    if a is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.storage_not_found", "Ablage nicht gefunden")
    return a


@router.get("/documents")
async def list_stores(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(DocSeries).where(DocSeries.owner_user_id == user.id)
                             .order_by(DocSeries.key))).scalars().all()
    aus = []
    for a in rows:
        count = len((await db.execute(select(DocEntry.id)
                                       .where(DocEntry.series_id == a.id))).scalars().all())
        aus.append(_store_out(a, count))
    return aus


@router.get("/documents/{key:path}/entries")
async def list_entries(key: str, limit: int = Query(30, ge=1, le=200),
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """Die Fassungen, neueste zuerst — ohne Text, sonst hinge die Liste an einem Rückblick
    von 30.000 Zeichen."""
    a = await _my(db, user, key)
    rows = (await db.execute(select(DocEntry).where(DocEntry.series_id == a.id)
                             .order_by(DocEntry.id.desc()).limit(limit))).scalars().all()
    return {"storage": _store_out(a), "entries": [_entry_out(e, with_text=False) for e in rows]}


@router.get("/documents/{key:path}/entries/{entry_id}")
async def get_entry(key: str, entry_id: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    a = await _my(db, user, key)
    e = await db.get(DocEntry, entry_id)
    if e is None or e.series_id != a.id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.entry_not_found", "Fassung nicht gefunden")
    return {"storage": _store_out(a), "entry": _entry_out(e)}


@router.get("/documents/{key:path}/latest")
async def get_last(key: str, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    """Der aktuelle Stand — darauf zeigt der Link in einer Meldung."""
    a = await _my(db, user, key)
    e = await documents.last(db, user.id, key)
    if e is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.storage_empty", "Die Ablage ist noch leer")
    return {"storage": _store_out(a), "entry": _entry_out(e)}


@router.delete("/documents/{key:path}/entries/{entry_id}", status_code=204)
async def delete_entry(key: str, entry_id: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    a = await _my(db, user, key)
    e = await db.get(DocEntry, entry_id)
    if e is None or e.series_id != a.id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.entry_not_found", "Fassung nicht gefunden")
    await db.delete(e)
    await db.commit()


@router.delete("/documents/{key:path}", status_code=204)
async def delete_store(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Die ganze Ablage samt Fassungen. Ein Ablauf, der weiter hineinschreibt, legt sie
    beim nächsten Mal neu an — löschen heißt hier vergessen, nicht abschalten."""
    a = await _my(db, user, key)
    await db.delete(a)
    await db.commit()
