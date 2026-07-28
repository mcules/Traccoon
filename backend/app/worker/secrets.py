"""Token-/Secret-Auflösung für den Worker aus dem Secret-Tresor (Fernet+DB)."""
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
    """`secret:<name>` → Tresor-Wert (User- dann System-scoped); sonst Wert unverändert."""
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
    """LLM-Token für (User, Provider): benannter ProviderToken → Default-ProviderToken →
    Alt-Feld (claude/codex) bzw. System-Secret. Rückwärtskompatibel."""
    if owner_id:
        q = select(ProviderToken).where(ProviderToken.user_id == owner_id,
                                        ProviderToken.provider == provider)
        if token_name:
            row = (await db.execute(q.where(ProviderToken.name == token_name))).scalar_one_or_none()
        else:
            row = (await db.execute(q.where(ProviderToken.is_default.is_(True)))).scalar_one_or_none()
        if row:
            return decrypt_secret(row.value_enc)
    # Kein benannter Token → Bestands-Default (Subscriptions bleiben erstklassig).
    if provider in ("claude_code", "claude", "anthropic"):
        return await resolve_claude_token(db, owner_id)
    if provider == "codex":
        return await resolve_codex_token(db, owner_id)
    # openai o. Ä. ohne Token → System-Secret gleichen Namens.
    return await _system_secret(db, provider)


async def resolve_provider_base_url(db: AsyncSession, owner_id: int | None, provider: str,
                                    token_name: str = "") -> str | None:
    """Eigene Base-URL des zu (User, Provider, token_name) gehörenden ProviderToken-Rows
    (benannt → Default). Nur echte ProviderToken-Rows tragen eine Base-URL; ohne Row bzw.
    ohne gesetzte URL → None (Provider nutzt seinen Default-Endpoint)."""
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
    # per-User- dann System-Secret `git:<host>`
    return await resolve_ref(db, f"secret:git:{host}", owner_id) or None
