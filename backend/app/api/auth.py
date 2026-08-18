import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import create_access_token, hash_password, verify_password
from ..db import get_session
from ..models.enums import GlobalRole, UserStatus
from ..models.user import SYSTEM_USER_ID, User
from ..schemas.auth import (
    LoginIn, PasswordChangeIn, RegisterIn, TokenOut, UserOut,
)
from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, username=u.username, display_name=u.display_name,
        avatar_color=u.avatar_color, theme=u.theme, global_role=u.global_role,
        status=u.status, max_runners=u.max_runners, onboarded=u.onboarded_at is not None,
        default_project_view=u.default_project_view, ticket_open_mode=u.ticket_open_mode,
        ticket_layout=u.ticket_layout or {}, pm_chat_style=u.pm_chat_style,
        workflow_set_id=u.workflow_set_id,
        locale=u.locale, notify_default=u.notify_default, notify_email=u.notify_email,
        telegram_chat_id=u.telegram_chat_id,
        claude_token_set=bool(u.claude_oauth_token_enc), created_at=u.created_at,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, db: AsyncSession = Depends(get_session)):
    email = data.email.lower()
    exists = (
        await db.execute(select(User).where((User.email == email) | (User.username == data.username)))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail oder Benutzername bereits vergeben")

    # Erster echter User (außer System) wird automatisch aktiver Admin.
    real_count = (
        await db.execute(select(func.count()).select_from(User).where(User.id != SYSTEM_USER_ID))
    ).scalar_one()
    is_first = real_count == 0

    user = User(
        email=email,
        username=data.username,
        display_name=data.display_name or data.username,
        password_hash=hash_password(data.password),
        global_role=GlobalRole.admin if is_first else GlobalRole.user,
        status=UserStatus.active if is_first else UserStatus.pending,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if is_first:
        from .agents import seed_default_agents
        await seed_default_agents(db, user.id)
    return user_out(user)


@router.post("/login", response_model=TokenOut)
async def login(data: LoginIn, db: AsyncSession = Depends(get_session)):
    email = data.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültige Anmeldedaten")
    if user.status == UserStatus.pending:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto wartet auf Freischaltung")
    if user.status == UserStatus.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto deaktiviert")
    if user.status == UserStatus.placeholder:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platzhalter-Konto hat keinen Login")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/refresh", response_model=TokenOut)
async def refresh(user: User = Depends(get_current_user)):
    """Verlängert eine **bestehende** Sitzung — und gibt kein neues Recht.

    Warum es den Endpunkt gibt: `jwt_expire_minutes` ist 720, und das Frontend wirft bei 401
    hart auf `/login`. Jeder lange offene Tab (der Kiosk-Wandschirm ist nur der auffälligste)
    ist damit nach spätestens zwölf Stunden ein Anmeldeformular.

    Warum er keine Sicherheitsfläche aufmacht:

    * Er hängt an `get_current_user` — also **denselben** zwei Prüfungen wie jeder andere
      Aufruf: `iat` vor `password_changed_at` fliegt raus (Sitzungs-Invalidierung nach
      Passwortwechsel), `status != active` ebenfalls. Sie hier zu wiederholen wäre eine
      zweite Wahrheit, die beim nächsten Nachziehen einer der beiden Stellen kippt.
    * Ein abgelaufenes Token kommt gar nicht bis hierher: `decode_access_token` wirft, und
      `get_current_user` macht daraus 401. Ein Refresh kann eine tote Sitzung also nicht
      wiederbeleben, nur eine lebende verlängern.
    * Das neue Token enthält exakt dieselben Ansprüche wie ein frisch erlogintes
      (`sub`/`iat`/`exp`, siehe `core/security.py`) — Rollen stehen nicht im Token, sondern
      werden bei jedem Aufruf aus der Datenbank gelesen. Ein Rechtezuwachs ist hier also
      nicht bloß nicht implementiert, er ist strukturell unmöglich.
    """
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChangeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Altes Passwort falsch")
    user.password_hash = hash_password(data.new_password)
    user.password_changed_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()


@router.post("/me/onboarding-complete", response_model=UserOut)
async def onboarding_complete(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    user.onboarded_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user_out(user)
