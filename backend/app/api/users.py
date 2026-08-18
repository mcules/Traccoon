from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    email: str | None = None                       # optional — ohne E-Mail kein E-Mail-Login
    username: str = Field(min_length=1, max_length=100)
    display_name: str = ""
    password: str | None = None                    # optional — ohne Passwort kein Login
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
            raise ValueError("Passwort muss mindestens 8 Zeichen haben")
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
    """Admin legt fest, welche MCP-Server ein User nutzen darf. Der eigentliche
    MCPJungle-Token entsteht per Operator-Skript (scripts/provision_mcp.py)."""
    u = await _get_manageable(user_id, db)
    u.mcp_servers = data.servers
    await db.commit()
    return {"servers": u.mcp_servers, "provisioned": bool(u.mcp_group and u.mcp_token_enc)}


async def _get_manageable(user_id: int, db: AsyncSession) -> User:
    if user_id == SYSTEM_USER_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "System-User nicht verwaltbar")
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return u


@router.get("/search")
async def search_users(q: str = "", _: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Nutzer nach Benutzername ODER Anzeigename suchen (für Mitglied-Hinzufügen).
    Minimaldaten, jedem eingeloggten Nutzer zugänglich."""
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
    """Personen, die dieser Mensch sehen darf — Auswahl als Empfänger.

    Sichtbar sind: er selbst, alle Mitglieder seiner Projekte und Platzhalter-Konten
    (die existieren nur, um jemanden zu benennen). Ein Admin sieht alle. Damit lässt sich
    in einem Ablauf ein Empfänger benennen, ohne dass der Ablauf zu einem Projekt gehören
    muss — vorher stand dort die Mitgliederliste des Projekts, bei projektlosen Abläufen
    also nichts.
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
             # Wie diese Person erreicht wird, gehört in die Auswahl: sonst wählt man
             # jemanden und erfährt nie, dass bei ihm gar kein Weg hinterlegt ist.
             "notify_default": u.notify_default,
             "kanaele": [k for k in ("telegram", "email")
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
    """Admin legt direkt einen Nutzer an. E-Mail und Passwort sind optional — ohne Passwort ist
    das Konto login-los (leerer Hash → Login schlägt fehl). Standard-Agenten werden nur bei einem
    aktiven Konto MIT Passwort geseedet (login-lose Konten brauchen keine)."""
    email = data.email  # bereits normalisiert/validiert (oder None)
    # Kollisionsprüfung: Benutzername immer, E-Mail nur wenn gesetzt.
    cond = User.username == data.username
    if email is not None:
        cond = cond | (User.email == email)
    exists = (await db.execute(select(User).where(cond))).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail oder Benutzername bereits vergeben")
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nicht sich selbst deaktivieren")
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
            raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits vergeben")
    if data.username is not None:
        conflict = (
            await db.execute(
                select(User).where(User.id != user_id, User.username == data.username)
            )
        ).scalars().first()
        if conflict is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Benutzername bereits vergeben")
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nicht sich selbst degradieren")
    u = await _get_manageable(user_id, db)
    u.global_role = role
    await db.commit()
    await db.refresh(u)
    return user_out(u)
