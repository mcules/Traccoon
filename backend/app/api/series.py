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

from ..core.fehler import Fehler
from ..core.security import decrypt_secret
from ..db import get_session
from ..models.enums import GlobalRole
from ..models.series import ARTEN, Series, SeriesPlace, SeriesPoint, SeriesShare
from ..models.user import User
from ..services import series as dienst
from ..services import series_formats
from .deps import get_current_user

router = APIRouter(tags=["series"])


# ── Aufnahme ─────────────────────────────────────────────────────────────────

async def _reihe_zum_token(db: AsyncSession, token: str) -> Series:
    reihe = (await db.execute(select(Series).where(
        Series.token_hash == dienst.token_hash(token)))).scalar_one_or_none()
    if reihe is None or not reihe.active:
        raise Fehler(404, "err.unknown_route", "Unknown route")
    return reihe


async def _aufnehmen(db: AsyncSession, token: str, nutzlast, query: dict) -> dict:
    reihe = await _reihe_zum_token(db, token)
    punkte = series_formats.normalisiere(nutzlast, query)
    if not punkte:
        # Kein Fehler: Ein Geraet meldet auch mal seinen Zustand ohne Position, und eine 400
        # wuerde es in eine Wiederholungsschleife schicken.
        return {"accepted": 0, "skipped": 0, "still": 0, "ignored": True}
    ergebnis = await dienst.aufnehmen(db, reihe, punkte)
    await db.commit()
    return ergebnis


@router.post("/ingest/{token}", status_code=202)
async def ingest_post(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """OwnTracks, Overland, Home Assistant und alles Flache."""
    try:
        nutzlast = await request.json()
    except Exception:  # noqa: BLE001 - ein leerer Rumpf ist erlaubt (Traccar per POST)
        nutzlast = {}
    return await _aufnehmen(db, token, nutzlast, dict(request.query_params))


@router.get("/ingest/{token}", status_code=202)
async def ingest_get(token: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Traccar und OsmAnd: alles steht in der Adresse."""
    return await _aufnehmen(db, token, {}, dict(request.query_params))


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
    name: str | None = None
    description: str | None = None
    color: str | None = None
    expected_rows: int | None = None
    settings: dict | None = None
    active: bool | None = None


def _ist_admin(user: User) -> bool:
    return user.global_role == GlobalRole.admin


def _out(r: Series, *, besitzer: str = "", eigen: bool = True) -> dict:
    return {
        "id": r.id, "key": r.key, "kind": r.kind, "name": r.name or r.key,
        "description": r.description, "color": r.color, "settings": r.settings or {},
        "state": r.state or {}, "points": r.points or 0, "active": r.active,
        "expected_rows": r.expected_rows or 0, "store_id": r.store_id,
        "last_at": r.last_at.isoformat() if r.last_at else None,
        "owner_user_id": r.owner_user_id, "own": eigen, "owner": besitzer,
        # Das Token selbst steht hier nie — nur, ob eins vergeben ist.
        "has_token": bool(r.token_hash),
    }


async def _meine(db: AsyncSession, user: User, key: str) -> Series:
    """Eine Reihe, die dieser Mensch sehen darf — sonst 404 statt 403 (nichts verraten)."""
    reihe = (await db.execute(select(Series).where(
        Series.key == key, dienst.sichtbar(user.id, _ist_admin(user))))).scalar_one_or_none()
    if reihe is None:
        raise Fehler(404, "err.series_not_found", "Series '{reihe}' not found", reihe=key)
    return reihe


async def _namen(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.display_name, User.username)
                             .where(User.id.in_(ids)))).all()
    return {i: (anzeige or nutzer) for i, anzeige, nutzer in rows}


@router.get("/series")
async def list_series(kind: str | None = Query(None), user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    frage = select(Series).where(dienst.sichtbar(user.id, _ist_admin(user)))
    if kind:
        frage = frage.where(Series.kind == kind)
    reihen = (await db.execute(frage.order_by(Series.key))).scalars().all()
    namen = await _namen(db, {r.owner_user_id for r in reihen
                              if r.owner_user_id and r.owner_user_id != user.id})
    return [_out(r, eigen=r.owner_user_id == user.id,
                 besitzer=namen.get(r.owner_user_id or 0, "")) for r in reihen]


@router.post("/series", status_code=201)
async def create_series(data: SeriesIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    if data.kind not in ARTEN:
        raise Fehler(400, "err.unknown_series_kind", "Unknown kind '{kind}'", kind=data.kind)
    schluessel = data.key.strip()
    if not schluessel:
        raise Fehler(400, "err.series_key_required", "A key is required")
    doppelt = (await db.execute(select(Series).where(
        Series.owner_user_id == user.id, Series.key == schluessel))).scalar_one_or_none()
    if doppelt is not None:
        raise Fehler(409, "err.series_exists", "Series '{reihe}' already exists",
                     reihe=schluessel)

    reihe = Series(owner_user_id=user.id, key=schluessel, kind=data.kind, name=data.name,
                   description=data.description, color=data.color,
                   expected_rows=data.expected_rows, settings=data.settings or {})
    db.add(reihe)
    await db.commit()
    await db.refresh(reihe)
    return _out(reihe)


@router.put("/series/{key:path}")
async def update_series(key: str, data: SeriesPatch, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    if not await dienst.darf_aendern(db, reihe, user.id, _ist_admin(user)):
        raise Fehler(403, "err.series_read_only", "You may only read this series")
    for feld, wert in data.model_dump(exclude_unset=True).items():
        setattr(reihe, feld, wert)
    await db.commit()
    await db.refresh(reihe)
    return _out(reihe)


@router.post("/series/{key:path}/token")
async def neues_token(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Ein frisches Aufnahme-Token. Das alte gilt ab sofort nicht mehr."""
    reihe = await _meine(db, user, key)
    if not await dienst.darf_aendern(db, reihe, user.id, _ist_admin(user)):
        raise Fehler(403, "err.series_read_only", "You may only read this series")
    roh = dienst.neuer_token(reihe)
    await db.commit()
    return {"token": roh, "path": f"/api/ingest/{roh}"}


@router.get("/series/{key:path}/token")
async def zeige_token(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Das vergebene Token noch einmal ansehen.

    Anders als beim MCP-Token bewusst wieder abrufbar: Man muss es in ein Telefon eintragen,
    und "einmal sehen und dann nie wieder" heisst in der Praxis, dass man es jedesmal neu
    vergibt und dabei die alte Einrichtung abraeumt.
    """
    reihe = await _meine(db, user, key)
    if reihe.owner_user_id != user.id and not _ist_admin(user):
        raise Fehler(403, "err.series_read_only", "You may only read this series")
    if not reihe.token_enc:
        raise Fehler(404, "err.no_token", "No token set")
    roh = decrypt_secret(reihe.token_enc)
    return {"token": roh, "path": f"/api/ingest/{roh}"}


# ── Punkte ───────────────────────────────────────────────────────────────────

def _punkt_out(p: SeriesPoint, kind: str) -> dict:
    basis = {"id": p.id, "ts": p.ts.isoformat() if p.ts else None, "source": p.source}
    if kind == "location":
        return {**basis, "lat": p.lat, "lon": p.lon, **(p.extra or {})}
    if kind == "text":
        return {**basis, "title": p.title, "body": p.body, "format": p.format}
    return {**basis, "value": p.value}


@router.get("/series/{key:path}/points")
async def list_points(key: str, von: str | None = Query(None), bis: str | None = Query(None),
                      limit: int = Query(2000, ge=1, le=50000),
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    frage = select(SeriesPoint).where(SeriesPoint.series_id == reihe.id)
    if von and (a := series_formats.zeitpunkt(von)):
        frage = frage.where(SeriesPoint.ts >= a)
    if bis and (b := series_formats.zeitpunkt(bis)):
        frage = frage.where(SeriesPoint.ts <= b)
    # Neueste zuerst holen und dann drehen: Bei einer Begrenzung will man die juengsten
    # Punkte, gezeichnet werden sie aber in der Reihenfolge der Zeit.
    punkte = (await db.execute(
        frage.order_by(SeriesPoint.ts.desc()).limit(limit))).scalars().all()
    return {"series": _out(reihe, eigen=reihe.owner_user_id == user.id),
            "points": [_punkt_out(p, reihe.kind) for p in reversed(punkte)]}


@router.delete("/series/{key:path}/points/{punkt_id}", status_code=204)
async def delete_point(key: str, punkt_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    if not await dienst.darf_aendern(db, reihe, user.id, _ist_admin(user)):
        raise Fehler(403, "err.series_read_only", "You may only read this series")
    punkt = await db.get(SeriesPoint, punkt_id)
    if punkt is None or punkt.series_id != reihe.id:
        raise Fehler(404, "err.point_not_found", "Point not found")
    await db.delete(punkt)
    await db.flush()
    reihe.points = (await db.execute(select(func.count()).select_from(SeriesPoint)
                                     .where(SeriesPoint.series_id == reihe.id))).scalar() or 0
    await db.commit()


@router.get("/series-live")
async def live(kind: str = Query("location"), user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_session)):
    """Der letzte Stand jeder sichtbaren Reihe — was eine Karte zum Aufmachen braucht.

    Eine eigene Adresse ohne `/series/`-Praefix, weil `{key:path}` sonst auch `live`
    schlucken wuerde und die Reihe mit dem Namen "live" den Endpunkt verdraengte.
    """
    reihen = (await db.execute(select(Series).where(
        Series.kind == kind, Series.active.is_(True),
        dienst.sichtbar(user.id, _ist_admin(user))).order_by(Series.key))).scalars().all()
    namen = await _namen(db, {r.owner_user_id for r in reihen
                              if r.owner_user_id and r.owner_user_id != user.id})
    return [_out(r, eigen=r.owner_user_id == user.id,
                 besitzer=namen.get(r.owner_user_id or 0, "")) for r in reihen]


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


def _ort_out(o: SeriesPlace, reihe_key: str = "") -> dict:
    return {"id": o.id, "key": o.key, "name": o.name or o.key, "lat": o.lat, "lon": o.lon,
            "radius_m": o.radius_m, "color": o.color, "notify": o.notify,
            "series_key": reihe_key}


@router.get("/places")
async def list_places(user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    orte = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == user.id).order_by(SeriesPlace.key))).scalars().all()
    return [_ort_out(o) for o in orte]


@router.post("/places", status_code=201)
async def create_place(data: PlaceIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    reihe_id = None
    if data.series_key:
        reihe_id = (await _meine(db, user, data.series_key)).id
    doppelt = (await db.execute(select(SeriesPlace).where(
        SeriesPlace.owner_user_id == user.id,
        SeriesPlace.key == data.key))).scalar_one_or_none()
    if doppelt is not None:
        raise Fehler(409, "err.place_exists", "Place '{ort}' already exists", ort=data.key)
    ort = SeriesPlace(owner_user_id=user.id, series_id=reihe_id, key=data.key, name=data.name,
                      lat=data.lat, lon=data.lon, radius_m=data.radius_m, color=data.color,
                      notify=data.notify)
    db.add(ort)
    await db.commit()
    await db.refresh(ort)
    return _ort_out(ort)


@router.put("/places/{place_id}")
async def update_place(place_id: int, data: PlaceIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    ort = await db.get(SeriesPlace, place_id)
    if ort is None or ort.owner_user_id != user.id:
        raise Fehler(404, "err.place_not_found", "Place not found")
    for feld in ("key", "name", "lat", "lon", "radius_m", "color", "notify"):
        setattr(ort, feld, getattr(data, feld))
    await db.commit()
    await db.refresh(ort)
    return _ort_out(ort)


@router.delete("/places/{place_id}", status_code=204)
async def delete_place(place_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    ort = await db.get(SeriesPlace, place_id)
    if ort is None or ort.owner_user_id != user.id:
        raise Fehler(404, "err.place_not_found", "Place not found")
    await db.delete(ort)
    await db.commit()


# ── Freigaben ────────────────────────────────────────────────────────────────

class ShareIn(BaseModel):
    user_id: int
    level: str = "view"


@router.get("/series/{key:path}/shares")
async def list_shares(key: str, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    rows = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == reihe.id))).scalars().all()
    namen = await _namen(db, {s.user_id for s in rows})
    return [{"id": s.id, "user_id": s.user_id, "username": namen.get(s.user_id, ""),
             "level": s.level} for s in rows]


@router.post("/series/{key:path}/shares", status_code=201)
async def create_share(key: str, data: ShareIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    # Weiterreichen darf nur, wem sie gehoert: Sonst gaebe ein `manage` das Recht, die Reihe
    # an beliebig viele weitere Menschen zu verteilen.
    if reihe.owner_user_id != user.id and not _ist_admin(user):
        raise Fehler(403, "err.series_not_yours", "Only the owner may share this series")
    if data.level not in ("view", "manage"):
        raise Fehler(400, "err.unknown_level", "Unknown level '{level}'", level=data.level)
    if data.user_id == reihe.owner_user_id:
        raise Fehler(400, "err.share_to_owner", "The series already belongs to this person")
    if await db.get(User, data.user_id) is None:
        raise Fehler(404, "err.user_not_found", "User not found")
    vorhanden = (await db.execute(select(SeriesShare).where(
        SeriesShare.series_id == reihe.id,
        SeriesShare.user_id == data.user_id))).scalar_one_or_none()
    if vorhanden is not None:
        vorhanden.level = data.level
        await db.commit()
        return {"id": vorhanden.id, "user_id": vorhanden.user_id, "level": vorhanden.level}
    freigabe = SeriesShare(series_id=reihe.id, user_id=data.user_id, level=data.level)
    db.add(freigabe)
    await db.commit()
    await db.refresh(freigabe)
    return {"id": freigabe.id, "user_id": freigabe.user_id, "level": freigabe.level}


@router.delete("/series/{key:path}/shares/{share_id}", status_code=204)
async def delete_share(key: str, share_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    if reihe.owner_user_id != user.id and not _ist_admin(user):
        raise Fehler(403, "err.series_not_yours", "Only the owner may share this series")
    await db.execute(sa_delete(SeriesShare).where(
        SeriesShare.id == share_id, SeriesShare.series_id == reihe.id))
    await db.commit()


# Ganz zum Schluss, und das mit Absicht: `{key:path}` ist gierig. Stuende dieses DELETE
# weiter oben, faenge es auch `/series/handy/points/5` ab — mit dem Schluessel
# "handy/points/5" und einem 404 als Ergebnis, das nach einem fehlenden Punkt aussieht.
@router.delete("/series/{key:path}", status_code=204)
async def delete_series(key: str, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    reihe = await _meine(db, user, key)
    if reihe.owner_user_id != user.id and not _ist_admin(user):
        raise Fehler(403, "err.series_read_only", "You may only read this series")
    await db.delete(reihe)
    await db.commit()
