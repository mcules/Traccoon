"""FastAPI-Dependencies: Auth, Projekt-Zugriff, Rollen- und KI-Recht-Prüfung."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decode_access_token
from ..db import get_session
from ..models.enums import GlobalRole, ProjectRole, UserStatus
from ..models.project import Project, ProjectMember
from ..models.user import User

ROLE_RANK = {
    ProjectRole.viewer: 0,
    ProjectRole.member: 1,
    ProjectRole.maintainer: 2,
    ProjectRole.owner: 3,
}


def _unauth(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, {"WWW-Authenticate": "Bearer"})


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauth()
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise _unauth("Invalid token")
    user = await db.get(User, int(payload.get("sub", 0)))
    if user is None:
        raise _unauth("Unknown user")
    # Session-Invalidierung: JWTs vor letzter Passwortänderung sind ungültig
    if user.password_changed_at is not None:
        iat = payload.get("iat", 0)
        if iat < int(user.password_changed_at.timestamp()):
            raise _unauth("Token expired by password change")
    if user.status != UserStatus.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account not active")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.global_role != GlobalRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")
    return user


def owned_or_global(column, user: User):
    """SQLAlchemy-Filter für owner-gebundene Objekte: eigene + globale (Owner NULL).
    Admins sehen alles (kein Filter). Für Jobs/Webhooks/… statt ad-hoc-Ausschreiben."""
    from sqlalchemy import or_
    if user.global_role == GlobalRole.admin:
        return True  # Admin: kein Owner-Filter
    return or_(column == user.id, column.is_(None))


def is_owner_or_admin(owner_id: int | None, user: User) -> bool:
    """Darf dieser User das owner-gebundene Objekt ändern/löschen?
    Globale Objekte (owner NULL) darf NUR ein Admin schreiben/löschen — lesen bleibt frei."""
    if user.global_role == GlobalRole.admin:
        return True
    return owner_id is not None and owner_id == user.id


@dataclass
class Access:
    user: User
    project: Project
    role: ProjectRole
    ai_assign: bool
    is_member: bool
    member_since: dt.datetime | None = None

    def has_role(self, minimum: ProjectRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]

    @property
    def is_new(self) -> bool:
        """Kürzlich (≤ 7 Tage) hinzugefügtes Mitglied — für die 'Neu'-Kennzeichnung im UI."""
        if not self.is_member or self.member_since is None:
            return False
        now = dt.datetime.now(tz=dt.timezone.utc)
        return (now - self.member_since) <= dt.timedelta(days=7)


async def build_access(project: Project, user: User, db: AsyncSession) -> Access:
    """Ermittelt die effektive Zugriffs-/Rechte-Sicht eines Users auf ein Projekt."""
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        return Access(user, project, member.role, member.ai_assign, True, member.created_at)
    # Admin-Override: globaler Admin darf auch ohne Mitgliedschaft zugreifen (fremdes Projekt)
    if user.global_role == GlobalRole.admin:
        return Access(user, project, ProjectRole.owner, True, False)
    # Strikte Isolation: fremdes Projekt = 404 (nicht 403)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


async def get_project_access(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> Access:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return await build_access(project, user, db)


def require_role(minimum: ProjectRole):
    async def _dep(access: Access = Depends(get_project_access)) -> Access:
        if not access.has_role(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role {minimum.value}")
        return access
    return _dep


async def require_ai_assign(access: Access = Depends(get_project_access)) -> Access:
    """KI-Recht: Voraussetzung für PM-Chat und Agent-Zuweisung."""
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")
    return access
