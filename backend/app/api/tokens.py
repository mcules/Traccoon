"""Personal access tokens of a person: create, list, revoke.

The secret half is returned exactly once, in the answer to the create call, and exists
nowhere on the server afterwards (only its Argon2 hash). That is the same rule the
destinations follow with `has_secret`: whoever loses the string makes a new token, they do
not get the old one back.

Revoking stamps `revoked_at` and leaves the row standing. A deleted row is a lost record of
what once had access, and that is precisely the question one asks afterwards.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import scopes as scopes_mod
from ..core.error import Error
from ..db import get_session
from ..models.api_token import ApiToken
from ..models.enums import GlobalRole
from ..models.user import User
from ..services import api_tokens
from .deps import get_current_user, is_owner_or_admin

router = APIRouter(tags=["tokens"])

# A year is long for a credential and short for a client somebody set up once. Both ends are
# only guards against a typo (`expires_in_days=0` or a number that overflows a date).
MAX_DAYS = 3650


def _out(row: ApiToken) -> dict:
    """Everything about a token except the one thing that is not here any more."""
    return {
        "id": row.id, "owner_user_id": row.owner_user_id, "name": row.name,
        "prefix": row.prefix, "scopes": sorted(scopes_mod.parse(row.scopes)),
        "created_at": row.created_at, "last_used_at": row.last_used_at,
        "expires_at": row.expires_at, "revoked_at": row.revoked_at,
        # Computed here so the interface does not have to know the clock of the server: a
        # browser in another zone would grey out the wrong rows.
        "expired": api_tokens.expired(row),
    }


class TokenIn(BaseModel):
    name: str = Field(default="", max_length=120)
    scopes: list[str] = Field(default_factory=list)
    # None = no expiry. A token nobody dated is not a mistake: the take-back that matters is
    # `Revoke`, and an expiry that surprises a client at three in the morning is worse than
    # none at all.
    expires_in_days: int | None = None


@router.get("/me/tokens")
async def list_tokens(user_id: int | None = None,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)) -> list[dict]:
    """One's own tokens, newest first.

    An admin may look at somebody else's rows (`?user_id=`) but never at a secret, because
    there is none stored. Anybody else asking for a foreign list gets their own instead of an
    error: the question "whose tokens are these" is not one a non-admin should be able to
    probe.
    """
    owner = user.id
    if user_id is not None and user.global_role == GlobalRole.admin:
        owner = int(user_id)
    rows = (await db.execute(
        select(ApiToken).where(ApiToken.owner_user_id == owner)
        .order_by(ApiToken.id.desc()))).scalars().all()
    return [_out(r) for r in rows]


@router.post("/me/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(data: TokenIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)) -> dict:
    """Mint a token. The ONLY answer that carries the secret."""
    name = (data.name or "").strip()
    if not name:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.token_name_missing",
                     "The token needs a name")
    wanted = scopes_mod.clean(data.scopes)
    if not wanted:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.token_scope_missing",
                     "Choose at least one scope, possible: {possible}",
                     possible=", ".join(scopes_mod.ALL_SCOPES))
    expires_at = None
    if data.expires_in_days is not None:
        days = int(data.expires_in_days)
        if not 1 <= days <= MAX_DAYS:
            raise Error(status.HTTP_400_BAD_REQUEST, "err.token_expiry_out_of_range",
                         "The lifetime has to lie between 1 and {max} days", max=MAX_DAYS)
        expires_at = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=days)

    full, prefix, secret = api_tokens.mint()
    row = ApiToken(owner_user_id=user.id, name=name[:120], prefix=prefix,
                   token_hash=api_tokens.hash_secret(secret), scopes=",".join(wanted),
                   expires_at=expires_at)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {**_out(row), "token": full}


@router.delete("/me/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Take one token back, and only that one. The row stays."""
    row = await db.get(ApiToken, token_id)
    if row is None or not is_owner_or_admin(row.owner_user_id, user):
        raise Error(status.HTTP_404_NOT_FOUND, "err.token_not_found", "Token not found")
    if row.revoked_at is None:
        row.revoked_at = dt.datetime.now(tz=dt.timezone.utc)
        await db.commit()
