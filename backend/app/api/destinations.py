"""Ziele verwalten: benannte externe Gegenstellen mit Basis-URL und Anmeldung.

Wer was darf — bewusst wie bei den Prozess-Sätzen:
- **systemweit** (kein Nutzer, kein Projekt): nur Admin,
- **persönlich** (`user_id`): der Eigentümer (Admins zusätzlich),
- **projektbezogen** (`project_id`): Rolle owner|maintainer im Projekt.

Geheimnisse gehen nur HINEIN: kein Endpunkt gibt Passwort, Token, API-Key, HMAC- oder
Client-Secret je zurück — die Antworten melden nur, ob eines hinterlegt ist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import encrypt_secret
from ..db import get_session
from ..models.destination import Destination
from ..models.enums import GlobalRole, ProjectRole
from ..models.project import Project
from ..models.user import User
from ..schemas.destination import (
    DestinationCreate, DestinationOut, DestinationTestIn, DestinationUpdate,
)
from ..services import destinations as svc
from .deps import build_access, get_current_user

router = APIRouter(tags=["destinations"])

# Felder, deren Wert im verschlüsselten `secret_enc` landet — je nach Verfahren ein
# anderes Geheimnis, aber immer dasselbe Feld.
SECRET_FIELDS = ("password", "token", "api_key", "hmac_secret", "client_secret", "secret")


def _out(d: Destination) -> DestinationOut:
    return DestinationOut(
        id=d.id, name=d.name, label=d.label, description=d.description,
        user_id=d.user_id, project_id=d.project_id, base_url=d.base_url,
        auth_type=d.auth_type, username=d.username, has_secret=bool(d.secret_enc),
        api_key_name=d.api_key_name, api_key_in=d.api_key_in,
        hmac_header=d.hmac_header, hmac_algo=d.hmac_algo, hmac_prefix=d.hmac_prefix,
        oauth_token_url=d.oauth_token_url, oauth_client_id=d.oauth_client_id,
        oauth_scope=d.oauth_scope, oauth_audience=d.oauth_audience,
        default_headers=d.default_headers or {}, timeout_sec=d.timeout_sec,
        verify_tls=d.verify_tls, enabled=d.enabled, allow_agents=d.allow_agents,
        scope=("project" if d.project_id else ("user" if d.user_id else "global")),
        last_used_at=d.last_used_at, created_at=d.created_at,
    )


async def _require_write(db: AsyncSession, user: User, *, user_id: int | None,
                         project_id: int | None) -> None:
    if project_id is not None:
        project = await db.get(Project, project_id)
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
        access = await build_access(project, user, db)   # 404 bei Fremdprojekt
        if not access.has_role(ProjectRole.maintainer):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Rolle owner|maintainer erforderlich")
        return
    if user_id is not None:
        if user_id != user.id and user.global_role != GlobalRole.admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Fremdes persönliches Ziel")
        return
    if user.global_role != GlobalRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Systemweite Ziele darf nur ein Admin anlegen")


async def _get(db: AsyncSession, did: int) -> Destination:
    d = await db.get(Destination, did)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ziel nicht gefunden")
    return d


def _apply_secret(d: Destination, data: dict) -> None:
    """Ein gesetztes Geheimnis übernehmen — ein leerer Wert lässt das alte unangetastet."""
    for feld in SECRET_FIELDS:
        wert = data.get(feld)
        if wert:
            d.secret_enc = encrypt_secret(str(wert))
            # Anmeldung geändert → zwischengespeichertes OAuth-Token verwerfen.
            d.oauth_token_enc = ""
            d.oauth_expires_at = None
            return


@router.get("/destinations", response_model=list[DestinationOut])
async def list_destinations(
    project_id: int | None = None, usable: bool = False,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Ziele auflisten. `usable=true` liefert die im Zusammenhang **aufrufbaren** (je Name
    das vorrangige) — das ist die Liste für die Auswahl im Prozess-Editor."""
    if usable:
        rows = await svc.visible(db, project_id=project_id, owner_id=user.id)
        return [_out(d) for d in rows]
    bereiche = [Destination.user_id.is_(None) & Destination.project_id.is_(None),
                (Destination.user_id == user.id) & Destination.project_id.is_(None)]
    if project_id is not None:
        await _require_write(db, user, user_id=None, project_id=project_id)
        bereiche.append(Destination.project_id == project_id)
    q = select(Destination).where(or_(*bereiche))
    if user.global_role == GlobalRole.admin and project_id is None:
        q = select(Destination)
    rows = (await db.execute(q.order_by(Destination.name))).scalars().all()
    return [_out(d) for d in rows]


@router.post("/destinations", response_model=DestinationOut, status_code=201)
async def create_destination(
    data: DestinationCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    await _require_write(db, user, user_id=data.user_id, project_id=data.project_id)
    if data.auth_type not in svc.AUTH_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unbekanntes Verfahren '{data.auth_type}'")
    doppelt = await svc.resolve(db, data.name, project_id=data.project_id,
                                owner_id=data.user_id)
    if doppelt is not None and (doppelt.user_id, doppelt.project_id) == (data.user_id,
                                                                        data.project_id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Ziel '{data.name}' gibt es in diesem Bereich schon")
    werte = data.model_dump(exclude={"user_id", "project_id", *SECRET_FIELDS})
    d = Destination(**werte, user_id=data.user_id, project_id=data.project_id,
                    created_by=user.id)
    _apply_secret(d, data.model_dump())
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _out(d)


@router.put("/destinations/{did}", response_model=DestinationOut)
async def update_destination(
    did: int, data: DestinationUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get(db, did)
    await _require_write(db, user, user_id=d.user_id, project_id=d.project_id)
    werte = data.model_dump(exclude_unset=True, exclude={*SECRET_FIELDS})
    if "auth_type" in werte and werte["auth_type"] not in svc.AUTH_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekanntes Verfahren")
    for feld, wert in werte.items():
        setattr(d, feld, wert)
    if "auth_type" in werte:
        d.oauth_token_enc, d.oauth_expires_at = "", None
    _apply_secret(d, data.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(d)
    return _out(d)


@router.delete("/destinations/{did}", status_code=204)
async def delete_destination(
    did: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get(db, did)
    await _require_write(db, user, user_id=d.user_id, project_id=d.project_id)
    await db.delete(d)
    await db.commit()


@router.post("/destinations/{did}/test")
async def test_destination(
    did: int, data: DestinationTestIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Probeaufruf mit den hinterlegten Zugangsdaten — Standard ist ein harmloses GET.

    Antwortet mit Status und (gekürztem) Inhalt, damit man Anmeldung und Pfad prüfen kann,
    ohne einen Prozess bauen zu müssen.
    """
    d = await _get(db, did)
    await _require_write(db, user, user_id=d.user_id, project_id=d.project_id)
    try:
        return await svc.call(db, d, method=data.method, path=data.path,
                              query=data.query or {}, headers=data.headers or {},
                              body=data.body, timeout=data.timeout_sec)
    except Exception as e:  # noqa: BLE001 — Netz-/Auth-Fehler gehören in die Antwort
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Aufruf fehlgeschlagen: {e}")
    finally:
        await db.commit()   # last_used_at / OAuth-Token-Cache festschreiben
