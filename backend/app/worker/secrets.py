"""Token and secret resolution for the worker from the secret vault (Fernet plus database)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decrypt_secret
from ..models.secrets import ProviderToken, UserSecret
from ..models.user import User


async def _system_secret(db: AsyncSession, name: str) -> str | None:
    row = (
        await db.execute(
            select(UserSecret).where(UserSecret.user_id.is_(None), UserSecret.name == name)
        )
    ).scalar_one_or_none()
    return decrypt_secret(row.value_enc) if row else None


async def resolve_ref(db: AsyncSession, raw: str, owner_id: int | None) -> str:
    """`secret:<name>` becomes the vault value (user scoped, then system scoped); otherwise the value is unchanged."""
    if not raw:
        return ""
    if not raw.startswith("secret:"):
        return decrypt_secret(raw) if raw.startswith("enc:v1:") else raw
    name = raw[len("secret:"):]
    if owner_id:
        row = (
            await db.execute(
                select(UserSecret).where(UserSecret.user_id == owner_id, UserSecret.name == name)
            )
        ).scalar_one_or_none()
        if row:
            return decrypt_secret(row.value_enc)
    return (await _system_secret(db, name)) or ""


async def resolve_claude_token(db: AsyncSession, owner_id: int | None) -> str | None:
    if owner_id:
        user = await db.get(User, owner_id)
        if user and user.claude_oauth_token_enc:
            tok = await resolve_ref(db, user.claude_oauth_token_enc, owner_id)
            if tok:
                return tok
    return await _system_secret(db, "claude")


async def resolve_codex_token(db: AsyncSession, owner_id: int | None) -> str | None:
    if owner_id:
        user = await db.get(User, owner_id)
        if user and user.codex_token_enc:
            tok = await resolve_ref(db, user.codex_token_enc, owner_id)
            if tok:
                return tok
    return await _system_secret(db, "codex")


async def resolve_provider_token(db: AsyncSession, owner_id: int | None, provider: str,
                                 token_name: str = "") -> str | None:
    """LLM token for (user, provider): a named ProviderToken, then the default ProviderToken,
    then the legacy field (claude/codex) respectively the system secret. Backwards compatible."""
    if owner_id:
        q = select(ProviderToken).where(ProviderToken.user_id == owner_id,
                                        ProviderToken.provider == provider)
        if token_name:
            row = (await db.execute(q.where(ProviderToken.name == token_name))).scalar_one_or_none()
        else:
            row = (await db.execute(q.where(ProviderToken.is_default.is_(True)))).scalar_one_or_none()
        if row:
            return decrypt_secret(row.value_enc)
    # No named token, so the existing default (subscriptions stay first class).
    if provider in ("claude_code", "claude", "anthropic"):
        return await resolve_claude_token(db, owner_id)
    if provider == "codex":
        return await resolve_codex_token(db, owner_id)
    # openai or similar without a token: the system secret of the same name.
    return await _system_secret(db, provider)


async def resolve_provider_base_url(db: AsyncSession, owner_id: int | None, provider: str,
                                    token_name: str = "") -> str | None:
    """The own base URL of the ProviderToken row belonging to (user, provider, token_name)
    (named, then default). Only real ProviderToken rows carry a base URL; without a row or
    without a set URL it is None (and the provider uses its default endpoint)."""
    if not owner_id:
        return None
    q = select(ProviderToken).where(ProviderToken.user_id == owner_id,
                                    ProviderToken.provider == provider)
    if token_name:
        row = (await db.execute(q.where(ProviderToken.name == token_name))).scalar_one_or_none()
    else:
        row = (await db.execute(q.where(ProviderToken.is_default.is_(True)))).scalar_one_or_none()
    return (row.base_url or None) if row else None


async def resolve_git_token(db: AsyncSession, project_git_token_enc: str, owner_id: int | None,
                            host: str) -> str | None:
    if project_git_token_enc:
        tok = await resolve_ref(db, project_git_token_enc, owner_id)
        if tok:
            return tok
    # per-user, then the system secret `git:<host>`
    return await resolve_ref(db, f"secret:git:{host}", owner_id) or None
