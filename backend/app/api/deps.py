"""FastAPI dependencies: auth, project access, role and AI right checks."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from fastapi import Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import scopes as scopes_mod
from ..core.error import Error
from ..db import get_session
from ..models.enums import GlobalRole, ProjectRole
from ..models.project import Project, ProjectMember
from ..models.user import User
from ..services import api_tokens

ROLE_RANK = {
    ProjectRole.viewer: 0,
    ProjectRole.member: 1,
    ProjectRole.maintainer: 2,
    ProjectRole.owner: 3,
}


def _bearer(exc: Error) -> Error:
    """Hang `WWW-Authenticate` on a 401. That header is what tells a client it may retry with
    credentials, and `Error` carries no headers of its own (the handler passes through
    whatever stands there).

    Why the `Error` is built at the call site and not in here: the check in
    `frontend/tools/error-texts-check.mjs` reads the key out of the source, and a key handed
    in as a variable is invisible to it. A 401 would then be the one error class whose text
    silently stays English on a German screen.
    """
    exc.headers = {"WWW-Authenticate": "Bearer"}
    return exc


def route_path(request: Request) -> str:
    """The matched route TEMPLATE (`/issues/{key}`), which is what the scope table is keyed
    by. FastAPI hangs the route into the request scope while matching, so this is available
    by the time a dependency runs. The concrete path is the fallback for the cases that have
    no route at all (a 404 never reaches a dependency, but a mounted sub-app might)."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Who is calling. Two kinds of bearer come in here, and only one dependency comes out.

    A second dependency for personal access tokens would be a second truth about who is
    logged in, and every router already hangs off this one. What differs is only the reach:
    a JWT session is unrestricted (`request.state.scopes is None`), a token is measured
    against `core/scopes.py`, deny by default.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _bearer(Error(status.HTTP_401_UNAUTHORIZED, "err.not_authenticated",
                            "Not authenticated"))
    token = authorization.split(" ", 1)[1].strip()
    result = await api_tokens.authenticate(db, token)
    if result.error == api_tokens.BAD_TOKEN:
        # One answer for every way a token can fail. Which of them it was is information an
        # attacker does not need.
        raise _bearer(Error(status.HTTP_401_UNAUTHORIZED, "err.invalid_token",
                            "Invalid token"))
    if result.error == api_tokens.BAD_UNKNOWN_USER:
        raise _bearer(Error(status.HTTP_401_UNAUTHORIZED, "err.unknown_user",
                            "Unknown user"))
    if result.error == api_tokens.BAD_PASSWORD_CHANGED:
        raise _bearer(Error(status.HTTP_401_UNAUTHORIZED,
                            "err.token_expired_by_password_change",
                            "Token expired by password change"))
    if result.error == api_tokens.BAD_INACTIVE or result.user is None:
        raise Error(status.HTTP_403_FORBIDDEN, "err.account_not_active", "Account not active")
    if not scopes_mod.allowed(result.scopes, request.method, route_path(request)):
        raise Error(status.HTTP_403_FORBIDDEN, "err.scope_missing",
                     "This access token does not reach {method} {path}",
                     method=request.method, path=route_path(request))
    # Where the endpoints can see it: None = a JWT session, unrestricted.
    request.state.scopes = result.scopes
    return result.user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.global_role != GlobalRole.admin:
        raise Error(status.HTTP_403_FORBIDDEN, "err.admin_required", "Admin required")
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
    # The name this person chose for THIS project. Empty means the account name applies. An
    # inherited membership brings the alias of the project it is inherited from: whoever is
    # a callsign one floor up is the same person one floor down.
    alias: str = ""

    @property
    def name_here(self) -> str:
        """What to write when this person appears by name in this project."""
        from ..models.project import member_name
        return member_name(self.alias, self.user.display_name, self.user.username)

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
        return Access(user, project, member.role, member.ai_assign, True,
                      member.created_at, alias=member.alias)
    inherited = await _find_inherited_membership(project, user, db)
    if inherited is not None:
        # Inherited counts as a membership (is_member=True); `inherited` tells it apart from
        # a direct member, and only the admin override stays is_member=False ("foreign").
        return Access(
            user, project, _cap_inherited_role(inherited.role), inherited.ai_assign, True,
            inherited.created_at, inherited=True, alias=inherited.alias,
        )
    # Admin override: a global admin may access even without a membership (foreign project)
    if user.global_role == GlobalRole.admin:
        return Access(user, project, ProjectRole.owner, True, False)
    # Strict isolation: a foreign project is a 404 (not a 403)
    raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")


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
        return Access(user, project, member.role, member.ai_assign, True,
                      member.created_at, alias=member.alias)
    inherited = _find_inherited_membership_bulk(project, members_by_project, projects_by_id)
    if inherited is not None:
        return Access(
            user, project, _cap_inherited_role(inherited.role), inherited.ai_assign, True,
            inherited.created_at, inherited=True, alias=inherited.alias,
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
        raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")
    return await build_access(project, user, db)


def require_role(minimum: ProjectRole):
    async def _dep(access: Access = Depends(get_project_access)) -> Access:
        if not access.has_role(minimum):
            raise Error(status.HTTP_403_FORBIDDEN, "err.role_required",
                         "Role {role} is required", role=minimum.value)
        return access
    return _dep


async def require_ai_assign(access: Access = Depends(get_project_access)) -> Access:
    """The AI right: the precondition for the PM chat and for assigning an agent."""
    if not access.ai_assign:
        raise Error(status.HTTP_403_FORBIDDEN, "err.ai_right_ai_assign_required",
                     "The AI right (ai_assign) is required")
    return access
