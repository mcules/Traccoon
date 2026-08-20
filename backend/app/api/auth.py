import datetime as dt

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
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
        timezone=u.timezone, mail_last_account_id=u.mail_last_account_id,
        ticket_layout=u.ticket_layout or {}, pm_chat_style=u.pm_chat_style,
        workflow_set_id=u.workflow_set_id,
        locale=u.locale, notify_default=u.notify_default, notify_email=u.notify_email,
        telegram_chat_id=u.telegram_chat_id,
        notify_destination_id=u.notify_destination_id,
        claude_token_set=bool(u.claude_oauth_token_enc), created_at=u.created_at,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, db: AsyncSession = Depends(get_session)):
    email = data.email.lower()
    exists = (
        await db.execute(select(User).where((User.email == email) | (User.username == data.username)))
    ).scalar_one_or_none()
    if exists is not None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.e_mail_user_name_already_taken",
                     "E-mail or user name already taken")

    # The first real user (except the system) automatically becomes an active admin.
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
        raise Fehler(status.HTTP_401_UNAUTHORIZED, "err.invalid_credentials",
                     "Invalid credentials")
    if user.status == UserStatus.pending:
        raise Fehler(status.HTTP_403_FORBIDDEN, "err.account_waiting_enabled",
                     "The account is waiting to be enabled")
    if user.status == UserStatus.disabled:
        raise Fehler(status.HTTP_403_FORBIDDEN, "err.account_deactivated",
                     "The account is deactivated")
    if user.status == UserStatus.placeholder:
        raise Fehler(status.HTTP_403_FORBIDDEN, "err.placeholder_account_has_no_login",
                     "A placeholder account has no login")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/refresh", response_model=TokenOut)
async def refresh(user: User = Depends(get_current_user)):
    """Extends an **existing** session and gives no new right.

    Why the endpoint exists: `jwt_expire_minutes` is 720, and the frontend throws hard to
    `/login` on a 401. Every long open tab (the kiosk wall screen is only the most
    conspicuous one) is therefore a login form after twelve hours at the latest.

    Why it opens no security surface:

    * It hangs off `get_current_user`, so off the **same** two checks as every other call:
      `iat` before `password_changed_at` flies out (session invalidation after a password
      change), `status != active` as well. Repeating them here would be a second truth that
      tips over the next time one of the two places is updated.
    * An expired token does not even get here: `decode_access_token` raises, and
      `get_current_user` turns that into a 401. A refresh therefore cannot revive a dead
      session, only extend a living one.
    * The new token contains exactly the same claims as a freshly logged-in one
      (`sub`/`iat`/`exp`, see `core/security.py`): roles do not stand in the token but are
      read from the database on every call. An increase of rights is therefore not merely
      unimplemented here, it is structurally impossible.
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
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.old_password_wrong",
                     "The old password is wrong")
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
