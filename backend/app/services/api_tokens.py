"""Minting and checking personal access tokens, plus the ONE bearer verification.

`authenticate` is deliberately the only place that turns a `Authorization: Bearer …` string
(or a `?token=` query parameter) into a user. `deps.get_current_user`, the office socket and
the two sockets in `api/ws.py` all come through here, so there is one truth about who is
logged in instead of four copies that drift apart. The three callers differ only in how they
render the outcome: an HTTP status, or a websocket close code.
"""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import scopes as scopes_mod
from ..core.security import decode_access_token, hash_password, verify_password
from ..models.api_token import ApiToken
from ..models.enums import UserStatus
from ..models.user import User

# What marks a personal access token apart from a JWT at first glance. A JWT is base64 with
# dots and can never start with this, so the two paths cannot be confused.
MARK = "trc_"
PREFIX_LEN = 12
SECRET_LEN = 32

# How often `last_used_at` is written at most. Without it every read would be a write, and
# the value is only ever read by a human deciding whether a token is still in use.
LAST_USED_GAP = dt.timedelta(minutes=1)

# Why the outcome is a string and not an exception: the HTTP caller wants a 401 with a text,
# the socket wants a close code, and neither should have to catch the other's exception.
OK = ""
BAD_TOKEN = "token"            # unreadable, unknown, revoked, expired, wrong secret
BAD_UNKNOWN_USER = "unknown"   # JWT for a user that no longer exists
BAD_PASSWORD_CHANGED = "pw"    # JWT older than the last password change
BAD_INACTIVE = "inactive"      # the account behind a JWT is not active


@dataclass
class Auth:
    """Who is calling, and with what reach.

    `scopes is None` means a JWT session: a logged-in human, unrestricted. A set means a
    personal access token and is measured against `core/scopes.py`.
    """

    user: User | None
    scopes: set[str] | None = None
    error: str = OK


def _alphabet_random(length: int) -> str:
    """`secrets.token_urlsafe` in a fixed length. Slicing keeps the randomness (every
    character of the base64url alphabet is equally likely), it only cuts the string."""
    # token_urlsafe(n) yields about 1.33*n characters, so ask for more than needed.
    return secrets.token_urlsafe(length * 2)[:length]


def mint() -> tuple[str, str, str]:
    """A fresh token: (the full string, its prefix, its secret half)."""
    prefix = _alphabet_random(PREFIX_LEN)
    secret = _alphabet_random(SECRET_LEN)
    return f"{MARK}{prefix}_{secret}", prefix, secret


def hash_secret(secret: str) -> str:
    return hash_password(secret)


def split(token: str) -> tuple[str, str] | None:
    """`trc_<prefix>_<secret>` taken apart, by position and not by splitting on `_`.

    The url-safe alphabet contains `_` itself, so both halves may hold one and
    `token.split("_")` would be ambiguous. The prefix has a fixed length, which makes the
    separator's place known.
    """
    if not token.startswith(MARK):
        return None
    rest = token[len(MARK):]
    if len(rest) < PREFIX_LEN + 2 or rest[PREFIX_LEN] != "_":
        return None
    return rest[:PREFIX_LEN], rest[PREFIX_LEN + 1:]


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Postgres gives tz-aware timestamps back, SQLite (tests) naive ones. Read a naive one
    as UTC so a comparison does not blow up on the database in use."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=dt.timezone.utc)


def expired(row: ApiToken, now: dt.datetime | None = None) -> bool:
    ends = _aware(row.expires_at)
    if ends is None:
        return False
    return ends <= (now or dt.datetime.now(tz=dt.timezone.utc))


def revoked(row: ApiToken) -> bool:
    return row.revoked_at is not None


async def touch(db: AsyncSession, row: ApiToken, now: dt.datetime | None = None) -> bool:
    """Note the use, at most once a minute. Says whether it actually wrote."""
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    last = _aware(row.last_used_at)
    if last is not None and (now - last) < LAST_USED_GAP:
        return False
    row.last_used_at = now
    await db.commit()
    return True


async def by_token(db: AsyncSession, token: str) -> ApiToken | None:
    """The row behind a token string, or None. Verifies the secret; checks nothing else."""
    halves = split(token)
    if halves is None:
        return None
    prefix, secret = halves
    row = (await db.execute(
        select(ApiToken).where(ApiToken.prefix == prefix))).scalar_one_or_none()
    if row is None:
        return None
    if not verify_password(secret, row.token_hash):
        return None
    return row


async def authenticate(db: AsyncSession, token: str) -> Auth:
    """A bearer string turned into (user, scopes). Handles both kinds.

    A personal access token fails in five ways: no row, revoked, expired, wrong secret, owner
    not active. All five give the same answer, because which of them it was is information an
    attacker does not need.
    """
    if not token:
        return Auth(None, error=BAD_TOKEN)
    if token.startswith(MARK):
        row = await by_token(db, token)
        if row is None or revoked(row) or expired(row):
            return Auth(None, error=BAD_TOKEN)
        user = await db.get(User, row.owner_user_id)
        if user is None or user.status != UserStatus.active:
            return Auth(None, error=BAD_TOKEN)
        # NOTE: `password_changed_at` is deliberately NOT consulted here. Surviving a
        # password change is the entire reason these tokens exist: a client that has to be
        # re-pasted whenever the human changes their password is a JWT with extra steps.
        # Whoever wants a token gone revokes that one token (`DELETE /me/tokens/{id}`), which
        # is exactly the individual take-back a password change cannot give.
        await touch(db, row)
        return Auth(user, scopes_mod.parse(row.scopes))

    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return Auth(None, error=BAD_TOKEN)
    user = await db.get(User, int(payload.get("sub", 0) or 0))
    if user is None:
        return Auth(None, error=BAD_UNKNOWN_USER)
    # Session invalidation: JWTs from before the last password change are invalid. This is
    # the check the access token above skips on purpose.
    changed = _aware(user.password_changed_at)
    if changed is not None and int(payload.get("iat", 0) or 0) < int(changed.timestamp()):
        return Auth(None, error=BAD_PASSWORD_CHANGED)
    if user.status != UserStatus.active:
        return Auth(None, error=BAD_INACTIVE)
    # None, not an empty set: a logged-in human is unrestricted, and an empty set would mean
    # the opposite.
    return Auth(user, None)
