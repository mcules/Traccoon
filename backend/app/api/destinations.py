"""Manage destinations: named external counterparts with a base URL and a login.

Who may do what, deliberately as with the process sets:
- **system wide** (no user, no project): admins only,
- **personal** (`user_id`): the owner (plus admins),
- **project bound** (`project_id`): role owner or maintainer in the project.

Secrets only go IN: no endpoint ever returns the password, token, API key, HMAC secret or
client secret; the answers only report whether one is stored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
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

# Fields whose value lands in the encrypted `secret_enc`: a different secret depending on
# the method, but always the same field.
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
            raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")
        access = await build_access(project, user, db)   # 404 on a foreign project
        if not access.has_role(ProjectRole.maintainer):
            raise Error(status.HTTP_403_FORBIDDEN, "err.role_owner_maintainer_required",
                         "Role owner|maintainer is required")
        return
    if user_id is not None:
        if user_id != user.id and user.global_role != GlobalRole.admin:
            raise Error(status.HTTP_403_FORBIDDEN, "err.foreign_personal_destination",
                         "Foreign personal destination")
        return
    if user.global_role != GlobalRole.admin:
        raise Error(status.HTTP_403_FORBIDDEN, "err.only_admin_may_create_system_wide",
                     "Only an admin may create system-wide destinations")


async def _get(db: AsyncSession, did: int) -> Destination:
    d = await db.get(Destination, did)
    if d is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.destination_not_found",
                     "Destination not found")
    return d


def _apply_secret(d: Destination, data: dict) -> None:
    """Take over a set secret; an empty value leaves the old one untouched."""
    for field in SECRET_FIELDS:
        value = data.get(field)
        if value:
            d.secret_enc = encrypt_secret(str(value))
            # The login changed, so discard the cached OAuth token.
            d.oauth_token_enc = ""
            d.oauth_expires_at = None
            return


@router.get("/destinations", response_model=list[DestinationOut])
async def list_destinations(
    project_id: int | None = None, usable: bool = False,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """List destinations. `usable=true` delivers the ones **callable** in the context (the
    primary one per name), which is the list for the selection in the process editor."""
    if usable:
        rows = await svc.visible(db, project_id=project_id, owner_id=user.id)
        return [_out(d) for d in rows]
    areas = [Destination.user_id.is_(None) & Destination.project_id.is_(None),
                (Destination.user_id == user.id) & Destination.project_id.is_(None)]
    if project_id is not None:
        await _require_write(db, user, user_id=None, project_id=project_id)
        areas.append(Destination.project_id == project_id)
    q = select(Destination).where(or_(*areas))
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
        raise Error(status.HTTP_400_BAD_REQUEST, "err.unknown_method_named",
                     "Unknown method '{name}'", name=data.auth_type)
    duplicate = await svc.resolve(db, data.name, project_id=data.project_id,
                                owner_id=data.user_id)
    if duplicate is not None and (duplicate.user_id, duplicate.project_id) == (data.user_id,
                                                                        data.project_id):
        raise Error(status.HTTP_409_CONFLICT, "err.destination_already_exists_scope",
                     "The destination '{name}' already exists in this scope", name=data.name)
    values = data.model_dump(exclude={"user_id", "project_id", *SECRET_FIELDS})
    d = Destination(**values, user_id=data.user_id, project_id=data.project_id,
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
    values = data.model_dump(exclude_unset=True, exclude={*SECRET_FIELDS})
    if "auth_type" in values and values["auth_type"] not in svc.AUTH_TYPES:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.unknown_method", "Unknown method")
    for field, value in values.items():
        setattr(d, field, value)
    if "auth_type" in values:
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
    """Test call with the stored credentials; the default is a harmless GET.

    Answers with the status and the (truncated) content, so that login and path can be
    checked without having to build a process.
    """
    d = await _get(db, did)
    await _require_write(db, user, user_id=d.user_id, project_id=d.project_id)
    try:
        return await svc.call(db, d, method=data.method, path=data.path,
                              query=data.query or {}, headers=data.headers or {},
                              body=data.body, timeout=data.timeout_sec)
    except Exception as e:  # noqa: BLE001 - network and auth errors belong in the answer
        raise Error(status.HTTP_502_BAD_GATEWAY, "err.call_failed",
                     "The call failed: {reason}", reason=e)
    finally:
        await db.commit()   # last_used_at / OAuth-Token-Cache festschreiben
