"""Data series: create, read, share — and the path points come in on.

The ingest path `POST /ingest/{token}` is the only one here without a login and the fourth
auth pattern in the house (next to JWT, webhook GUID and MCP token). The reason is the
volume: the way through a webhook would create a flow instance including step rows per
report, and a phone reports 375 times a day. The webhook stays for everything event-shaped; a
location point is not one — only entering a place is.
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
from ..services import series_formats, series_health
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
        # No error: a device reports its state without a position now and then, and a 400
        # wuerde es in eine Wiederholungsschleife schicken.
        return {"accepted": 0, "skipped": 0, "still": 0, "ignored": True}
    result = await service.ingest(db, series, points)
    await db.commit()
    return result


@router.post("/ingest/{token}", status_code=202)
async def ingest_post(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """The common tracker payloads plus everything with a flat lat/lon pair."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - an empty body is allowed (Traccar over POST)
        payload = {}
    return await _ingest(db, token, payload, dict(request.query_params))


@router.get("/ingest/{token}", status_code=202)
async def ingest_get(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Traccar and OsmAnd: everything stands in the address."""
    return await _ingest(db, token, {}, dict(request.query_params))


@router.post("/ingest", status_code=202)
async def ingest_many(request: Request, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Many series in one delivery, authenticated by a personal access token.

    The door next to it (`/ingest/{token}`) binds one token to one series, which is right for
    a tracker and wrong for a phone that mirrors a watch: it carries twenty kinds of reading
    and would need twenty secrets in its settings. Here the payload names the series and the
    bearer names the person, so one token that can be revoked covers all of them.

    Series under `health.` are created on the way in when they are missing. Anything else is
    dropped: the sender may fill its own corner, not sow the list full.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a broken body is a dropped delivery, not a stack trace
        payload = {}
    if not series_health.looks_like(payload):
        return {"accepted": 0, "skipped": 0, "duplicate": 0, "series": {}, "ignored": True}

    total = {"accepted": 0, "skipped": 0, "duplicate": 0}
    per_series: dict[str, dict] = {}
    for key, points in series_health.normalise(payload).items():
        kind = series_health.kind_of(key, points[0])
        row = await service.series(db, user.id, key, kind=kind, create=True,
                                   name=series_health.name_of(key))
        if not row.settings:
            # Only on a fresh series: whoever has adjusted the limits by hand keeps them, and
            # the unit of the first point must not overwrite the one that was chosen.
            unit = next((str(p["unit"]) for p in points if p.get("unit")), "")
            row.settings = series_health.settings_for(key, unit)
        got = await service.ingest(db, row, points, source="health")
        per_series[key] = {"accepted": got["accepted"], "skipped": got["skipped"],
                           "duplicate": got.get("duplicate", 0)}
        for field in total:
            total[field] += per_series[key][field]

    await db.commit()
    return {**total, "series": per_series}


# ── Reihen ───────────────────────────────────────────────────────────────────

class SeriesIn(BaseModel):
    key: str
    kind: str = "number"
    name: str = ""
    description: str = ""
    color: str = ""
    settings: dict = {}


class SeriesPatch(BaseModel):
    key: str | None = None
    name: str | None = None
    description: str | None = None
    color: str | None = None
    settings: dict | None = None
    active: bool | None = None


def _is_admin(user: User) -> bool:
    return user.global_role == GlobalRole.admin


def _out(r: Series, *, owner: str = "", own: bool = True) -> dict:
    return {
        "id": r.id, "key": r.key, "kind": r.kind, "name": r.name or r.key,
        "description": r.description, "color": r.color, "settings": r.settings or {},
        "state": r.state or {}, "points": r.points or 0, "active": r.active,
        "last_at": r.last_at.isoformat() if r.last_at else None,
        "owner_user_id": r.owner_user_id, "own": own, "owner": owner,
        # The token itself never stands here — only whether one has been issued.
        "has_token": bool(r.token_hash),
    }


async def _my(db: AsyncSession, user: User, key: str) -> Series:
    """A series this person may see — otherwise 404 instead of 403 (reveal nothing)."""
    series = (await db.execute(select(Series).where(
        Series.key == key, service.visible(user.id, _is_admin(user))))).scalar_one_or_none()
    if series is None:
        raise Error(404, "err.series_not_found", "Series '{series}' not found", series=key)
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
        raise Error(409, "err.series_exists", "Series '{series}' already exists",
                     series=key)

    series = Series(owner_user_id=user.id, key=key, kind=data.kind, name=data.name,
                   description=data.description, color=data.color,
                   settings=data.settings or {})
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

    # Renaming is allowed, but it is no harmless field: flows name the series by its key.
    # Whoever renames has to pull them along — which is why the hint stands in the UI as well.
    # Prevented is only what the database would not carry anyway.
    new = (fields.pop("key", None) or "").strip()
    if new and new != series.key:
        taken = (await db.execute(select(Series).where(
            Series.owner_user_id == series.owner_user_id,
            Series.key == new))).scalar_one_or_none()
        if taken is not None:
            raise Error(409, "err.series_exists", "Series '{series}' already exists",
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
    """A fresh ingest token. The old one stops working immediately."""
    series = await _my(db, user, key)
    if not await service.may_update(db, series, user.id, _is_admin(user)):
        raise Error(403, "err.series_read_only", "You may only read this series")
    raw = service.new_token(series)
    await db.commit()
    return {"token": raw, "path": f"/api/ingest/{raw}"}


@router.get("/series/{key:path}/token")
async def show_token(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Look at the issued token again.

    Unlike the MCP token deliberately retrievable: one has to type it into a phone, and "see
    it once and then never again" means in practice that one issues a new one every time and
    clears away the old setup in the process.
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
async def list_points(key: str,
                      # `from` is a keyword in Python, so the parameter carries a trailing
                      # underscore and the alias. `von`/`bis` were the names before the house
                      # became English; a plugin built back then keeps working.
                      from_: str | None = Query(None, alias="from"),
                      to: str | None = Query(None),
                      von: str | None = Query(None, include_in_schema=False),
                      bis: str | None = Query(None, include_in_schema=False),
                      limit: int = Query(2000, ge=1, le=50000),
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    question = select(SeriesPoint).where(SeriesPoint.series_id == series.id)
    start, end = from_ or von, to or bis
    if start and (a := series_formats.moment(start)):
        question = question.where(SeriesPoint.ts >= a)
    if end and (b := series_formats.moment(end)):
        question = question.where(SeriesPoint.ts <= b)
    # Fetch the newest first and turn them around afterwards: with a limit one wants the
    # youngest points, but they are drawn in the order of time.
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
    """The latest state of every visible series — what a map needs to open.

    An address of its own without the `/series/` prefix, because `{key:path}` would otherwise
    swallow `live` too and the series named "live" would displace the endpoint.
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
        raise Error(409, "err.place_exists", "Place '{place}' already exists", place=data.key)
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
    # Only the owner may pass it on: otherwise a `manage` would grant the right to
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


# Right at the end, and on purpose: `{key:path}` is greedy. If this DELETE stood further up it
# would catch `/series/handy/points/5` as well — with the key "handy/points/5" and a 404 as
# the result, which looks like a missing point.
@router.delete("/series/{key:path}", status_code=204)
async def delete_series(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    series = await _my(db, user, key)
    if series.owner_user_id != user.id and not _is_admin(user):
        raise Error(403, "err.series_read_only", "You may only read this series")
    await db.delete(series)
    await db.commit()
