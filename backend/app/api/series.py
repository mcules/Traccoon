"""Datenreihen: anlegen, lesen, teilen — und der Weg, auf dem Punkte hereinkommen.

Der Aufnahmepfad `POST /ingest/{token}` ist der einzige hier ohne Anmeldung und das vierte
Auth-Muster im Haus (neben JWT, Webhook-GUID und MCP-Token). Der Grund ist die Menge: Der
Weg ueber einen Webhook wuerde je Meldung eine Ablauf-Instanz samt Schritt-Zeilen anlegen,
und ein Telefon meldet 375-mal am Tag. Der Webhook bleibt fuer alles Ereignisfoermige; ein
Standortpunkt ist keins — erst das Betreten eines Ortes ist eins.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.security import decrypt_secret
from ..db import get_session
from ..models.enums import GlobalRole
from ..models.series import KINDS, Series, SeriesPlace, SeriesPoint, SeriesShare
from ..models.user import User
from ..services import series as service
from ..services import series_formats
from .deps import get_current_user

router = APIRouter(tags=["series"])


# ── Aufnahme ─────────────────────────────────────────────────────────────────

async def _series_to_token(db: AsyncSession, token: str) -> Series:
    series = (await db.execute(select(Series).where(
        Series.token_hash == service.token_hash(token)))).scalar_one_or_none()
    if series is None or not series.active:
        raise Error(404, "err.unknown_route", "Unknown route")
    return series


async def _ingest(db: AsyncSession, token: str, payload, query: dict) -> dict:
    series = await _series_to_token(db, token)
    points = series_formats.normalise(payload, query)
    if not points:
        # Kein Fehler: Ein Geraet meldet auch mal seinen Zustand ohne Position, und eine 400
        # wuerde es in eine Wiederholungsschleife schicken.
        return {"accepted": 0, "skipped": 0, "still": 0, "ignored": True}
    result = await service.ingest(db, series, points)
    await db.commit()
    return result


@router.post("/ingest/{token}", status_code=202)
async def ingest_post(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """OwnTracks, Overland, Home Assistant und alles Flache."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - ein leerer Rumpf ist erlaubt (Traccar per POST)
        payload = {}
    return await _ingest(db, token, payload, dict(request.query_params))


@router.get("/ingest/{token}", status_code=202)
async def ingest_get(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Traccar und OsmAnd: alles steht in der Adresse."""
    return await _ingest(db, token, {}, dict(request.query_params))


# ── Reihen ───────────────────────────────────────────────────────────────────

class SeriesIn(BaseModel):
    key: str
    kind: str = "number"
    name: str = ""
    description: str = ""
    color: str = ""
    expected_rows: int = 0
    settings: dict = {}


class SeriesPatch(BaseModel):
    key: str | None = None
    name: str | None = None
    description: str | None = None
    color: str | None = None
    expected_rows: int | None = None
    settings: dict | None = None
    active: bool | None = None


def _is_admin(user: User) -> bool:
    return user.global_role == GlobalRole.admin


def _out(r: Series, *, owner: str = "", own: bool = True) -> dict:
    return {
        "id": r.id, "key": r.key, "kind": r.kind, "name": r.name or r.key,
        "description": r.description, "color": r.color, "settings": r.settings or {},
        "state": r.state or {}, "points": r.points or 0, "active": r.active,
        "expected_rows": r.expected_rows or 0, "store_id": r.store_id,
        "last_at": r.last_at.isoformat() if r.last_at else None,
        "owner_user_id": r.owner_user_id, "own": own, "owner": owner,
        # Das Token selbst steht hier nie — nur, ob eins vergeben ist.
        "has_token": bool(r.token_hash),
    }


async def _my(db: AsyncSession, user: User, key: str) -> Series:
    """Eine Reihe, die dieser Mensch sehen darf — sonst 404 statt 403 (nichts verraten)."""
    series = (await db.execute(select(Series).where(
        Series.key == key, service.visible(user.id, _is_admin(user))))).scalar_one_or_none()
    if series is None:
        raise Error(404, "err.series_not_found", "Series '{reihe}' not found", series=key)
    return series


async def _names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.display_name, User.username)
                             .where(User.id.in_(ids)))).all()
    return {i: (display or user) for i, display, user in rows}


@router.get("/series")
async def list_series(kind: str | None = Query(None), user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    question = select(Series).where(service.visible(user.id, _is_admin(user)))
    if kind:
        question = question.where(Series.kind == kind)
    series = (await db.execute(question.order_by(Series.key))).scalars().all()
    names = await _names(db, {r.owner_user_id for r in series
                              if r.owner_user_id and r.owner_user_id != user.id})
    return [_out(r, own=r.owner_user_id == user.id,
                 owner=names.get(r.owner_user_id or 0, "")) for r in series]


@router.post("/series", status_code=201)
async def create_series(data: SeriesIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    if data.kind not in KINDS:
        raise Error(400, "err.unknown_series_kind", "Unknown kind '{kind}'", kind=data.kind)
    key = data.key.strip()
    if not key:
        raise Error(400, "err.series_key_required", "A key is required")
    duplicate = (await db.execute(select(Series).where(
        Series.owner_user_id == user.id, Series.key == key))).scalar_one_or_none()
    if duplicate is not None:
        raise Error(409, "err.series_exists", "Series '{reihe}' already exists",
                     series=key)

    series = Series(owner_user_id=user.id, key=key, kind=data.kind, name=data.name,
                   description=data.description, color=data.color,
                   expected_rows=data.expected_rows, settings=data.settings or {})
    db.add(series)
    await db.commit()
    await db.refresh(series)
    return _out(series)


@router.put("/series/{key:path}")
async def update_series(key: str, data: SeriesPatch, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    if not await service.may_update(db, series, user.id, _is_admin(user)):
        raise Error(403, "err.series_read_only", "You may only read this series")
    fields = data.model_dump(exclude_unset=True)

    # Umbenennen ist erlaubt, aber es ist kein harmloses Feld: Ablaeufe nennen die Reihe beim
    # Schluessel. Wer umbenennt, muss sie mitziehen — deshalb steht der Hinweis auch in der
    # Oberflaeche. Verhindert wird nur, was die Datenbank ohnehin nicht traegt.
    new = (fields.pop("key", None) or "").strip()
    if new and new != series.key:
        taken = (await db.execute(select(Series).where(
            Series.owner_user_id == series.owner_user_id,
            Series.key == new))).scalar_one_or_none()
        if taken is not None:
            raise Error(409, "err.series_exists", "Series '{reihe}' already exists",
                         series=new)
        series.key = new

    for field, value in fields.items():
        setattr(series, field, value)
    await db.commit()
    await db.refresh(series)
    return _out(series)


@router.post("/series/{key:path}/token")
async def new_token(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Ein frisches Aufnahme-Token. Das alte gilt ab sofort nicht mehr."""
    series = await _my(db, user, key)
    if not await service.may_update(db, series, user.id, _is_admin(user)):
        raise Error(403, "err.series_read_only", "You may only read this series")
    raw = service.new_token(series)
    await db.commit()
    return {"token": raw, "path": f"/api/ingest/{raw}"}


@router.get("/series/{key:path}/token")
async def show_token(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Das vergebene Token noch einmal ansehen.

    Anders als beim MCP-Token bewusst wieder abrufbar: Man muss es in ein Telefon eintragen,
    und "einmal sehen und dann nie wieder" heisst in der Praxis, dass man es jedesmal neu
    vergibt und dabei die alte Einrichtung abraeumt.
    """
    series = await _my(db, user, key)
    if series.owner_user_id != user.id and not _is_admin(user):
        raise Error(403, "err.series_read_only", "You may only read this series")
    if not series.token_enc:
        raise Error(404, "err.no_token", "No token set")
    raw = decrypt_secret(series.token_enc)
    return {"token": raw, "path": f"/api/ingest/{raw}"}


# ── Punkte ───────────────────────────────────────────────────────────────────

def _point_out(p: SeriesPoint, kind: str) -> dict:
    basis = {"id": p.id, "ts": p.ts.isoformat() if p.ts else None, "source": p.source}
    if kind == "location":
        return {**basis, "lat": p.lat, "lon": p.lon, **(p.extra or {})}
    if kind == "text":
        return {**basis, "title": p.title, "body": p.body, "format": p.format}
    return {**basis, "value": p.value}


@router.get("/series/{key:path}/points")
async def list_points(key: str, von: str | None = Query(None), to: str | None = Query(None),
                      limit: int = Query(2000, ge=1, le=50000),
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    question = select(SeriesPoint).where(SeriesPoint.series_id == series.id)
    if von and (a := series_formats.moment(von)):
        question = question.where(SeriesPoint.ts >= a)
    if to and (b := series_formats.moment(to)):
        question = question.where(SeriesPoint.ts <= b)
    # Neueste zuerst holen und dann drehen: Bei einer Begrenzung will man die juengsten
    # Punkte, gezeichnet werden sie aber in der Reihenfolge der Zeit.
    points = (await db.execute(
        question.order_by(SeriesPoint.ts.desc()).limit(limit))).scalars().all()
    return {"series": _out(series, own=series.owner_user_id == user.id),
            "points": [_point_out(p, series.kind) for p in reversed(points)]}


@router.delete("/series/{key:path}/points/{point_id}", status_code=204)
async def delete_point(key: str, point_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    if not await service.may_update(db, series, user.id, _is_admin(user)):
        raise Error(403, "err.series_read_only", "You may only read this series")
    point = await db.get(SeriesPoint, point_id)
    if point is None or point.series_id != series.id:
        raise Error(404, "err.point_not_found", "Point not found")
    await db.delete(point)
    await db.flush()
    series.points = (await db.execute(select(func.count()).select_from(SeriesPoint)
                                     .where(SeriesPoint.series_id == series.id))).scalar() or 0
    await db.commit()


@router.get("/series-live")
async def live(kind: str = Query("location"), user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_session)):
    """Der letzte Stand jeder sichtbaren Reihe — was eine Karte zum Aufmachen braucht.

    Eine eigene Adresse ohne `/series/`-Praefix, weil `{key:path}` sonst auch `live`
    schlucken wuerde und die Reihe mit dem Namen "live" den Endpunkt verdraengte.
    """
    series = (await db.execute(select(Series).where(
        Series.kind == kind, Series.active.is_(True),
        service.visible(user.id, _is_admin(user))).order_by(Series.key))).scalars().all()
    names = await _names(db, {r.owner_user_id for r in series
                              if r.owner_user_id and r.owner_user_id != user.id})
    return [_out(r, own=r.owner_user_id == user.id,
                 owner=names.get(r.owner_user_id or 0, "")) for r in series]


# ── Orte ─────────────────────────────────────────────────────────────────────

class PlaceIn(BaseModel):
    key: str
    name: str = ""
    lat: float
    lon: float
    radius_m: int = 150
    color: str = ""
    notify: bool = True
    series_key: str | None = None


def _place_out(o: SeriesPlace, series_key: str = "") -> dict:
    return {"id": o.id, "key": o.key, "name": o.name or o.key, "lat": o.lat, "lon": o.lon,
            "radius_m": o.radius_m, "color": o.color, "notify": o.notify,
            "series_key": series_key}


@router.get("/places")
async def list_places(user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    places = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == user.id).order_by(SeriesPlace.key))).scalars().all()
    return [_place_out(o) for o in places]


@router.post("/places", status_code=201)
async def create_place(data: PlaceIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    series_id = None
    if data.series_key:
        series_id = (await _my(db, user, data.series_key)).id
    duplicate = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == user.id,
        SeriesPlace.key == data.key))).scalar_one_or_none()
    if duplicate is not None:
        raise Error(409, "err.place_exists", "Place '{ort}' already exists", place=data.key)
    place = SeriesPlace(owner_user_id=user.id, series_id=series_id, key=data.key, name=data.name,
                      lat=data.lat, lon=data.lon, radius_m=data.radius_m, color=data.color,
                      notify=data.notify)
    db.add(place)
    await db.commit()
    await db.refresh(place)
    return _place_out(place)


@router.put("/places/{place_id}")
async def update_place(place_id: int, data: PlaceIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    place = await db.get(SeriesPlace, place_id)
    if place is None or place.owner_user_id != user.id:
        raise Error(404, "err.place_not_found", "Place not found")
    for field in ("key", "name", "lat", "lon", "radius_m", "color", "notify"):
        setattr(place, field, getattr(data, field))
    await db.commit()
    await db.refresh(place)
    return _place_out(place)


@router.delete("/places/{place_id}", status_code=204)
async def delete_place(place_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    place = await db.get(SeriesPlace, place_id)
    if place is None or place.owner_user_id != user.id:
        raise Error(404, "err.place_not_found", "Place not found")
    await db.delete(place)
    await db.commit()


# ── Freigaben ────────────────────────────────────────────────────────────────

class ShareIn(BaseModel):
    user_id: int
    level: str = "view"


@router.get("/series/{key:path}/shares")
async def list_shares(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    rows = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == series.id))).scalars().all()
    names = await _names(db, {s.user_id for s in rows})
    return [{"id": s.id, "user_id": s.user_id, "username": names.get(s.user_id, ""),
             "level": s.level} for s in rows]


@router.post("/series/{key:path}/shares", status_code=201)
async def create_share(key: str, data: ShareIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    # Weiterreichen darf nur, wem sie gehoert: Sonst gaebe ein `manage` das Recht, die Reihe
    # an beliebig viele weitere Menschen zu verteilen.
    if series.owner_user_id != user.id and not _is_admin(user):
        raise Error(403, "err.series_not_yours", "Only the owner may share this series")
    if data.level not in ("view", "manage"):
        raise Error(400, "err.unknown_level", "Unknown level '{level}'", level=data.level)
    if data.user_id == series.owner_user_id:
        raise Error(400, "err.share_to_owner", "The series already belongs to this person")
    if await db.get(User, data.user_id) is None:
        raise Error(404, "err.user_not_found", "User not found")
    existing = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == series.id,
        SeriesShare.user_id == data.user_id))).scalar_one_or_none()
    if existing is not None:
        existing.level = data.level
        await db.commit()
        return {"id": existing.id, "user_id": existing.user_id, "level": existing.level}
    grant = SeriesShare(series_id=series.id, user_id=data.user_id, level=data.level)
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return {"id": grant.id, "user_id": grant.user_id, "level": grant.level}


@router.delete("/series/{key:path}/shares/{share_id}", status_code=204)
async def delete_share(key: str, share_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    if series.owner_user_id != user.id and not _is_admin(user):
        raise Error(403, "err.series_not_yours", "Only the owner may share this series")
    await db.execute(sa_delete(SeriesShare).where(
        SeriesShare.id == share_id, SeriesShare.series_id == series.id))
    await db.commit()


# Ganz zum Schluss, und das mit Absicht: `{key:path}` ist gierig. Stuende dieses DELETE
# weiter oben, faenge es auch `/series/handy/points/5` ab — mit dem Schluessel
# "handy/points/5" und einem 404 als Ergebnis, das nach einem fehlenden Punkt aussieht.
@router.delete("/series/{key:path}", status_code=204)
async def delete_series(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    if series.owner_user_id != user.id and not _is_admin(user):
        raise Error(403, "err.series_read_only", "You may only read this series")
    await db.delete(series)
    await db.commit()
