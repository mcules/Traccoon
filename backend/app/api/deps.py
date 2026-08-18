"""FastAPI dependencies: auth, project access, role and AI right checks."""
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
    # Session invalidation: JWTs from before the last password change are invalid
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
    """SQLAlchemy filter for owner bound objects: one's own plus the global ones (owner NULL).
    Admins see everything (no filter). For jobs, webhooks and so on instead of ad hoc writing."""
    from sqlalchemy import or_
    if user.global_role == GlobalRole.admin:
        return True  # admin: no owner filter
    return or_(column == user.id, column.is_(None))


def is_owner_or_admin(owner_id: int | None, user: User) -> bool:
    """May this user change or delete the owner bound object?
    Global objects (owner NULL) may ONLY be written or deleted by an admin; reading stays free."""
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
    inherited: bool = False  # role inherited from the parent tree instead of a direct membership

    def has_role(self, minimum: ProjectRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]

    @property
    def is_new(self) -> bool:
        """Recently (<= 7 days) added member, for the 'new' marking in the UI."""
        if not self.is_member or self.member_since is None:
            return False
        since = self.member_since
        # Postgres delivers tz-aware; other backends (tests, SQLite) naive, so read as UTC.
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(tz=dt.timezone.utc)
        return (now - since) <= dt.timedelta(days=7)


def _cap_inherited_role(role: ProjectRole) -> ProjectRole:
    """Owner rights are capped on inheritance (no automatic deletion or board rebuilding
    rights in the sub-project); other roles are taken over one to one."""
    return ProjectRole.maintainer if role == ProjectRole.owner else role


async def _find_inherited_membership(
    project: Project, user: User, db: AsyncSession
) -> ProjectMember | None:
    """Walks up the parent_id tree and delivers the first membership of an ancestor project
    found. Stops as soon as a project has inherit_members=False (that project wants no
    inherited rights from above), and on cycles."""
    if not project.inherit_members:
        return None
    seen = {project.id}
    parent_id = project.parent_id
    while parent_id is not None and parent_id not in seen:
        parent = await db.get(Project, parent_id)
        if parent is None:
            break
        seen.add(parent.id)
        member = (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == parent.id, ProjectMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if member is not None:
            return member
        if not parent.inherit_members:
            break
        parent_id = parent.parent_id
    return None


async def build_access(project: Project, user: User, db: AsyncSession) -> Access:
    """Determines the effective access and rights view of a user on a project.

    Order: own membership in the project itself (full) > inherited from the nearest ancestor
    with a membership (owner capped to maintainer) > admin override > 404.
    """
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        return Access(user, project, member.role, member.ai_assign, True, member.created_at)
    inherited = await _find_inherited_membership(project, user, db)
    if inherited is not None:
        # Inherited counts as a membership (is_member=True); `inherited` tells it apart from
        # a direct member, and only the admin override stays is_member=False ("foreign").
        return Access(
            user, project, _cap_inherited_role(inherited.role), inherited.ai_assign, True,
            inherited.created_at, inherited=True,
        )
    # Admin override: a global admin may access even without a membership (foreign project)
    if user.global_role == GlobalRole.admin:
        return Access(user, project, ProjectRole.owner, True, False)
    # Strict isolation: a foreign project is a 404 (not a 403)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


def _find_inherited_membership_bulk(
    project: Project,
    members_by_project: dict[int, ProjectMember],
    projects_by_id: dict[int, Project],
) -> ProjectMember | None:
    """Like `_find_inherited_membership` but without database accesses: walks up the
    parent_id tree using maps loaded in advance (project_id -> Project / ProjectMember)."""
    if not project.inherit_members:
        return None
    seen = {project.id}
    parent_id = project.parent_id
    while parent_id is not None and parent_id not in seen:
        parent = projects_by_id.get(parent_id)
        if parent is None:
            break
        seen.add(parent.id)
        member = members_by_project.get(parent.id)
        if member is not None:
            return member
        if not parent.inherit_members:
            break
        parent_id = parent.parent_id
    return None


def build_access_bulk(
    project: Project,
    user: User,
    members_by_project: dict[int, ProjectMember],
    projects_by_id: dict[int, Project],
) -> Access | None:
    """Like `build_access` but without database round trips: uses maps loaded in advance (in
    one query) for the memberships of the user (project_id -> ProjectMember) and all projects
    (project_id -> Project). For bulk queries (list_projects for instance) to avoid N+1
    queries while walking up the parent_id tree. Returns None instead of a 404 exception so
    that the caller can simply filter out inaccessible projects."""
    member = members_by_project.get(project.id)
    if member is not None:
        return Access(user, project, member.role, member.ai_assign, True, member.created_at)
    inherited = _find_inherited_membership_bulk(project, members_by_project, projects_by_id)
    if inherited is not None:
        return Access(
            user, project, _cap_inherited_role(inherited.role), inherited.ai_assign, True,
            inherited.created_at, inherited=True,
        )
    if user.global_role == GlobalRole.admin:
        return Access(user, project, ProjectRole.owner, True, False)
    return None


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
