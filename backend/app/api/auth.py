import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.security import create_access_token, hash_password, verify_password
from ..db import get_session
from ..models.enums import GlobalRole, UserStatus
from ..models.user import SYSTEM_USER_ID, User
from ..schemas.auth import (
    LoginIn, PasswordChangeIn, RegisterIn, TokenOut, UserOut,
)
from ..services import passkeys
from .deps import get_current_user

log = logging.getLogger("auth")
router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, email=u.email, username=u.username, display_name=u.display_name,
        avatar_color=u.avatar_color, theme=u.theme, global_role=u.global_role,
        status=u.status, max_runners=u.max_runners, onboarded=u.onboarded_at is not None,
        default_project_view=u.default_project_view, ticket_open_mode=u.ticket_open_mode,
        timezone=u.timezone, mail_last_account_id=u.mail_last_account_id,
        mail_threads=u.mail_threads,
        passkey_declined=u.passkey_declined_at is not None,
        ticket_layout=u.ticket_layout or {}, list_sort=u.list_sort or {},
        pm_chat_style=u.pm_chat_style,
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
        raise Error(status.HTTP_409_CONFLICT, "err.e_mail_user_name_already_taken",
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
    user = await _who(db, data.email)
    if user is None or not verify_password(data.password, user.password_hash):
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.invalid_credentials",
                     "Invalid credentials")
    _may_log_in(user)
    return TokenOut(access_token=create_access_token(user.id))


def _may_log_in(user: User) -> None:
    """Whether this account is one somebody may enter with — after the credentials held.

    Its own function because there are two doors now, and a rule that is written twice is a
    rule that will apply at one of them and not at the other.
    """
    if user.status == UserStatus.pending:
        raise Error(status.HTTP_403_FORBIDDEN, "err.account_waiting_enabled",
                     "The account is waiting to be enabled")
    if user.status == UserStatus.disabled:
        raise Error(status.HTTP_403_FORBIDDEN, "err.account_deactivated",
                     "The account is deactivated")
    if user.status == UserStatus.placeholder:
        raise Error(status.HTTP_403_FORBIDDEN, "err.placeholder_account_has_no_login",
                     "A placeholder account has no login")


async def _who(db: AsyncSession, name: str) -> User | None:
    """The person behind what was typed into the first field: an email address or a username.

    Both, because typing `dennis.eisold@irgendeine-lange-domain.de` at every login is the
    kind of friction that ends in a saved password nobody rotates. The username is the same
    login, just shorter.

    Case does not decide: an email address is stored lowercase anyway, and a username typed
    in the morning with a capital is the same person. `username` is unique per exact string
    though, so two spellings CAN exist side by side — then the exact one wins, and if there
    is none, nobody does. Guessing between two accounts at a login is not a thing to do.

    Returns None for unknown, exactly like a wrong password: the caller answers the same
    401 to both, and a login that says "this user does not exist" is a list of accounts for
    whoever asks patiently.
    """
    typed = (name or "").strip()
    if not typed:
        return None
    rows = (await db.execute(select(User).where(or_(
        User.email == typed.lower(),
        func.lower(User.username) == typed.lower())))).scalars().all()
    if len(rows) == 1:
        return rows[0]
    return next((u for u in rows if u.username == typed), None)


# ── Passkeys ────────────────────────────────────────────────────────────────
# Username first, then the key. Deliberately this way round and not the other: the flow
# names which keys may answer, so an authenticator holding keys for five sites offers the
# one that belongs here instead of a list to pick from.


class PasskeyStart(BaseModel):
    """Who is logging in. Same field name as at the password login, and the same freedom:
    email address or username."""
    email: str


class PasskeyFinish(BaseModel):
    email: str
    # What `navigator.credentials.get()` gave back, as the browser serialises it.
    credential: dict


def _passkey_ready() -> None:
    if not passkeys.configured():
        raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.passkeys_not_configured",
                     "Passkeys are not set up on this installation")


@router.post("/passkey/options")
async def passkey_options(data: PasskeyStart, db: AsyncSession = Depends(get_session)):
    """What the browser is to sign.

    An unknown name and an account without a key get an offer too — one nobody can answer.
    Saying "there is no such account" here would make the form a way to find out which
    accounts exist, and it costs nothing to keep quiet about it: the browser fails on its
    own, the way it does with a key that is not present.
    """
    _passkey_ready()
    user = await _who(db, data.email)
    if user is None or user.status != UserStatus.active:
        return json.loads(passkeys.decoy())
    try:
        return json.loads(await passkeys.offer_login(db, user))
    except passkeys.NoPasskey:
        return json.loads(passkeys.decoy())


@router.post("/passkey/login", response_model=TokenOut)
async def passkey_login(data: PasskeyFinish, db: AsyncSession = Depends(get_session)):
    """The signature, and a token if it holds.

    Everything that can go wrong here answers the same 401: an unknown name, a key of
    somebody else, an expired offer, a bad signature. Which of them it was is exactly what
    an attacker would like to know.
    """
    _passkey_ready()
    user = await _who(db, data.email)
    if user is None:
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.passkey_did_not_work",
                     "The passkey did not work")
    # The signature FIRST, the state of the account after. The other way round, a locked
    # account answers 403 to any old rubbish while an unknown name answers 401 — and that
    # difference is a way to find out which accounts exist. The password login has the same
    # order for the same reason.
    try:
        await passkeys.take_login(db, user, data.credential)
    except passkeys.NoPasskey:
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.passkey_did_not_work",
                     "The passkey did not work") from None
    except Exception as exc:  # noqa: BLE001 — the library raises its own kinds
        log.info("passkey login refused for %s: %s", user.id, exc)
        raise Error(status.HTTP_401_UNAUTHORIZED, "err.passkey_did_not_work",
                     "The passkey did not work") from None
    _may_log_in(user)
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
        raise Error(status.HTTP_400_BAD_REQUEST, "err.old_password_wrong",
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
