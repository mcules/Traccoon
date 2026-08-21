from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Fehler
from ..core.security import hash_password
from ..db import get_session
from ..models.enums import GlobalRole, UserStatus
from ..models.user import SYSTEM_USER_ID, User
from ..schemas.auth import UserOut, _valid_email
from .auth import user_out
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["users"])


class McpServersIn(BaseModel):
    servers: list[str]


class UserUpdateIn(BaseModel):
    email: str | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, min_length=2, max_length=100)
    display_name: str | None = None
    max_runners: int | None = Field(default=None, ge=0, le=20)

    _norm_email = field_validator("email")(lambda v: _valid_email(v) if v is not None else v)


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8)


class UserCreateIn(BaseModel):
    email: str | None = None                       # optional: without an e-mail there is no e-mail login
    username: str = Field(min_length=1, max_length=100)
    display_name: str = ""
    password: str | None = None                    # optional: without a password there is no login
    global_role: str = "user"    # user | admin
    status: str = "active"       # active | pending

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v):
        v = (v or "").strip()
        return _valid_email(v) if v else None

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        if v and len(v) < 8:
            raise ValueError("The password has to have at least 8 characters")
        return v or None


@router.get("/{user_id}/mcp")
async def get_user_mcp(user_id: int, _: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_session)):
    """MCP-Zuteilung eines Users (welche Server erlaubt, ob provisioniert)."""
    u = await _get_manageable(user_id, db)
    return {"servers": u.mcp_servers or [], "group": u.mcp_group,
            "provisioned": bool(u.mcp_group and u.mcp_token_enc)}


@router.put("/{user_id}/mcp-servers")
async def set_user_mcp_servers(user_id: int, data: McpServersIn, _: User = Depends(require_admin),
                               db: AsyncSession = Depends(get_session)):
    """An admin determines which MCP servers a user may use. The actual MCPJungle token
    comes into being over the operator script (scripts/provision_mcp.py)."""
    u = await _get_manageable(user_id, db)
    u.mcp_servers = data.servers
    await db.commit()
    return {"servers": u.mcp_servers, "provisioned": bool(u.mcp_group and u.mcp_token_enc)}


async def _get_manageable(user_id: int, db: AsyncSession) -> User:
    if user_id == SYSTEM_USER_ID:
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.system_users_cannot_managed",
                     "System users cannot be managed")
    u = await db.get(User, user_id)
    if u is None:
        raise Fehler(status.HTTP_404_NOT_FOUND, "err.user_not_found", "User not found")
    return u


@router.get("/search")
async def search_users(q: str = "", _: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Search users by user name OR display name (for adding a member).
    Minimal data, accessible to every logged-in user."""
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = (await db.execute(
        select(User).where(
            User.id != SYSTEM_USER_ID,
            User.username.ilike(like) | User.display_name.ilike(like),
        ).order_by(User.username).limit(20)
    )).scalars().all()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name,
             "status": u.status.value} for u in rows]


@router.get("/visible")
async def visible_users(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """People this human may see: the selection as recipients.

    Visible are: themselves, all members of their projects and placeholder accounts (which
    exist only in order to name somebody). An admin sees everybody. That lets a recipient be
    named in a flow without the flow having to belong to a project; before, the member list of
    the project stood there, which meant nothing with project-less flows.
    """
    from ..models.project import ProjectMember

    q = select(User).where(User.id != SYSTEM_USER_ID,
                           User.status != UserStatus.disabled)
    if user.global_role != GlobalRole.admin:
        meine = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        mit_mir = select(ProjectMember.user_id).where(ProjectMember.project_id.in_(meine))
        q = q.where(or_(User.id == user.id, User.id.in_(mit_mir),
                        User.status == UserStatus.placeholder))
    rows = (await db.execute(q.order_by(User.display_name, User.username))).scalars().all()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name,
             "status": u.status.value,
             # How this person is reached belongs in the selection: otherwise one chooses
             # somebody and never learns that no channel is stored for them at all.
             "notify_default": u.notify_default,
             "channels": [k for k in ("telegram", "email")
                         if (u.telegram_chat_id if k == "telegram"
                             else (u.notify_email or u.email))]}
            for u in rows]


@router.get("", response_model=list[UserOut])
async def list_users(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    rows = (
        await db.execute(select(User).where(User.id != SYSTEM_USER_ID).order_by(User.id))
    ).scalars().all()
    return [user_out(u) for u in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserCreateIn, _: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_session)):
    """An admin creates a user directly. E-mail and password are optional; without a password
    the account is login-less (an empty hash makes the login fail). Default agents are seeded
    only with an active account WITH a password (login-less accounts need none)."""
    email = data.email  # already normalised and validated (or None)
    # Collision check: the user name always, the e-mail only when set.
    cond = User.username == data.username
    if email is not None:
        cond = cond | (User.email == email)
    exists = (await db.execute(select(User).where(cond))).scalar_one_or_none()
    if exists is not None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.e_mail_user_name_already_taken",
                     "E-mail or user name already taken")
    try:
        role = GlobalRole(data.global_role)
    except ValueError:
        role = GlobalRole.user
    try:
        st = UserStatus(data.status)
    except ValueError:
        st = UserStatus.active
    if st not in (UserStatus.active, UserStatus.pending):
        st = UserStatus.active
    user = User(
        email=email, username=data.username, display_name=data.display_name or data.username,
        password_hash=hash_password(data.password) if data.password else "",
        global_role=role, status=st,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if st == UserStatus.active and data.password:
        from .agents import seed_default_agents
        await seed_default_agents(db, user.id)
    return user_out(user)


@router.post("/{user_id}/approve", response_model=UserOut)
async def approve(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    u = await _get_manageable(user_id, db)
    u.status = UserStatus.active
    await db.commit()
    from .agents import seed_default_agents
    await seed_default_agents(db, u.id)
    await db.refresh(u)
    return user_out(u)


@router.post("/{user_id}/disable", response_model=UserOut)
async def disable(
    user_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_session)
):
    if user_id == admin.id:
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.do_not_deactivate_yourself",
                     "Do not deactivate yourself")
    u = await _get_manageable(user_id, db)
    u.status = UserStatus.disabled
    await db.commit()
    await db.refresh(u)
    return user_out(u)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    data: UserUpdateIn,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    u = await _get_manageable(user_id, db)
    if data.email is not None:
        conflict = (
            await db.execute(
                select(User).where(User.id != user_id, User.email == data.email)
            )
        ).scalars().first()
        if conflict is not None:
            raise Fehler(status.HTTP_409_CONFLICT, "err.e_mail_already_taken",
                         "E-mail already taken")
    if data.username is not None:
        conflict = (
            await db.execute(
                select(User).where(User.id != user_id, User.username == data.username)
            )
        ).scalars().first()
        if conflict is not None:
            raise Fehler(status.HTTP_409_CONFLICT, "err.user_name_already_taken",
                         "User name already taken")
    if data.email is not None:
        u.email = data.email
    if data.username is not None:
        u.username = data.username
    if data.display_name is not None:
        u.display_name = data.display_name
    if data.max_runners is not None:
        u.max_runners = data.max_runners
    await db.commit()
    await db.refresh(u)
    return user_out(u)


@router.post("/{user_id}/reset-password", response_model=UserOut)
async def reset_password(
    user_id: int,
    data: PasswordResetIn,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    import datetime as dt

    u = await _get_manageable(user_id, db)
    u.password_hash = hash_password(data.new_password)
    u.password_changed_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    await db.refresh(u)
    return user_out(u)


@router.post("/{user_id}/role", response_model=UserOut)
async def set_role(
    user_id: int,
    role: GlobalRole,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    if user_id == admin.id and role != GlobalRole.admin:
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.do_not_demote_yourself",
                     "Do not demote yourself")
    u = await _get_manageable(user_id, db)
    u.global_role = role
    await db.commit()
    await db.refresh(u)
    return user_out(u)
