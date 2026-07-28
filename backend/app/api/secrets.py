from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import encrypt_secret
from ..db import get_session
from ..models.agents import AgentDefinition
from ..models.enums import ProjectRole
from ..models.secrets import ProviderToken, UserSecret
from ..models.user import User
from .deps import Access, get_current_user, require_role

router = APIRouter(tags=["secrets"])

PROVIDERS = ("claude_code", "codex", "openai")


class ProviderTokenIn(BaseModel):
    provider: str
    name: str = ""          # optional — leer = „Standard"
    token: str = ""
    is_default: bool = False
    base_url: str | None = None   # optional — nur openai: eigener OpenAI-kompatibler Endpoint


@router.get("/me/provider-tokens")
async def list_provider_tokens(user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_session)):
    """Benannte Tokens des Users je Provider — nur Metadaten, nie der Wert."""
    rows = (await db.execute(select(ProviderToken).where(ProviderToken.user_id == user.id)
                             .order_by(ProviderToken.provider, ProviderToken.name))).scalars().all()
    return [{"id": t.id, "provider": t.provider, "name": t.name, "is_default": t.is_default,
             "base_url": t.base_url}
            for t in rows]


@router.post("/me/provider-tokens", status_code=201)
async def add_provider_token(data: ProviderTokenIn, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    if data.provider not in PROVIDERS:
        raise HTTPException(400, f"Unbekannter Provider (erlaubt: {', '.join(PROVIDERS)})")
    if not data.token.strip():
        raise HTTPException(400, "Token erforderlich")
    name = data.name.strip() or "Standard"   # Name ist optional
    provider_rows = (await db.execute(select(ProviderToken).where(
        ProviderToken.user_id == user.id, ProviderToken.provider == data.provider))).scalars().all()
    existing = next((t for t in provider_rows if t.name == name), None)
    row = existing or ProviderToken(user_id=user.id, provider=data.provider, name=name, value_enc="")
    row.value_enc = encrypt_secret(data.token.strip())
    # Eigene Base-URL nur für die OpenAI-Familie sinnvoll; sonst ignorieren/leeren.
    row.base_url = (data.base_url or "").strip() or None if data.provider == "openai" else None
    # Erster Token eines Providers wird automatisch Default; sonst nur wenn angehakt.
    make_default = data.is_default or not provider_rows
    if make_default:
        for other in provider_rows:
            other.is_default = False
        row.is_default = True
    if existing is None:
        db.add(row)
    await db.commit()
    return {"id": row.id}


@router.post("/me/provider-tokens/{tid}/default", status_code=204)
async def set_default_provider_token(tid: int, user: User = Depends(get_current_user),
                                     db: AsyncSession = Depends(get_session)):
    """Diesen Token zum Standard seines Providers machen (die anderen verlieren den Status)."""
    row = await db.get(ProviderToken, tid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Token nicht gefunden")
    for other in (await db.execute(select(ProviderToken).where(
            ProviderToken.user_id == user.id,
            ProviderToken.provider == row.provider))).scalars().all():
        other.is_default = (other.id == tid)
    await db.commit()


class ProviderTokenPatch(BaseModel):
    token: str | None = None       # nur setzen, wenn nicht-leer (sonst Wert unverändert)
    base_url: str | None = None    # nur openai; "" → zurücksetzen auf Provider-Default
    is_default: bool | None = None


@router.patch("/me/provider-tokens/{tid}", status_code=204)
async def update_provider_token(tid: int, data: ProviderTokenPatch,
                                user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_session)):
    """Bestehenden Key bearbeiten OHNE Token-Zwang: Base-URL/Default ändern; Token nur, wenn
    ein neuer Wert mitgegeben wird (Werte werden nie zurückgeliefert)."""
    row = await db.get(ProviderToken, tid)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Token nicht gefunden")
    if data.token is not None and data.token.strip():
        row.value_enc = encrypt_secret(data.token.strip())
    if data.base_url is not None:
        row.base_url = ((data.base_url.strip() or None) if row.provider == "openai" else None)
    if data.is_default:
        for other in (await db.execute(select(ProviderToken).where(
                ProviderToken.user_id == user.id,
                ProviderToken.provider == row.provider))).scalars().all():
            other.is_default = (other.id == tid)
    await db.commit()


@router.delete("/me/provider-tokens/{tid}", status_code=204)
async def del_provider_token(tid: int, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    row = await db.get(ProviderToken, tid)
    if row and row.user_id == user.id:
        was_default = row.is_default
        provider = row.provider
        await db.delete(row)
        await db.flush()
        # War es der Standard, rückt der nächste verbliebene Token des Providers nach.
        if was_default:
            nxt = (await db.execute(select(ProviderToken).where(
                ProviderToken.user_id == user.id, ProviderToken.provider == provider)
                .order_by(ProviderToken.id))).scalars().first()
            if nxt:
                nxt.is_default = True
        await db.commit()


class TokenIn(BaseModel):
    token: str = ""


class SecretIn(BaseModel):
    value: str = ""
    description: str = ""


@router.put("/me/secrets/claude", status_code=204)
async def set_claude(data: TokenIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    user.claude_oauth_token_enc = encrypt_secret(data.token.strip()) if data.token.strip() else ""
    await db.commit()


@router.put("/me/secrets/codex", status_code=204)
async def set_codex(data: TokenIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    user.codex_token_enc = encrypt_secret(data.token.strip()) if data.token.strip() else ""
    await db.commit()


@router.get("/me/secrets")
async def list_secrets(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(UserSecret).where(UserSecret.user_id == user.id))).scalars().all()
    return {
        "claude_set": bool(user.claude_oauth_token_enc),
        "codex_set": bool(user.codex_token_enc),
        "vault": [{"name": s.name, "description": s.description} for s in rows],
    }


@router.put("/me/secrets/{name}", status_code=204)
async def set_named(name: str, data: SecretIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    row = (
        await db.execute(select(UserSecret).where(UserSecret.user_id == user.id, UserSecret.name == name))
    ).scalar_one_or_none()
    if not data.value.strip():
        if row:
            await db.delete(row)
            await db.commit()
        return
    if row is None:
        row = UserSecret(user_id=user.id, name=name, value_enc="")
        db.add(row)
    row.value_enc = encrypt_secret(data.value.strip())
    row.description = data.description
    await db.commit()


@router.put("/projects/{project_id}/git-token", status_code=204)
async def set_git_token(data: TokenIn, access: Access = Depends(require_role(ProjectRole.maintainer)),
                        db: AsyncSession = Depends(get_session)):
    access.project.git_token_enc = encrypt_secret(data.token.strip()) if data.token.strip() else ""
    await db.commit()


@router.get("/projects/{project_id}/agent-requirements")
async def agent_requirements(access: Access = Depends(require_role(ProjectRole.member)),
                             db: AsyncSession = Depends(get_session)):
    """Meldet fehlende Secrets vor einem Lauf (Claude/Codex-Token, Git-Token)."""
    user = access.user
    missing: list[str] = []
    # welche Provider nutzen die Projekt-Agenten?
    defs = (
        await db.execute(
            select(AgentDefinition).where(
                (AgentDefinition.project_id == access.project.id) | (AgentDefinition.user_id == user.id)
            )
        )
    ).scalars().all()
    providers = {d.provider for d in defs} or {"claude_code"}
    sys_claude = (await db.execute(
        select(UserSecret).where(UserSecret.user_id.is_(None), UserSecret.name == "claude")
    )).scalar_one_or_none()
    if "claude_code" in providers and not user.claude_oauth_token_enc and not sys_claude:
        missing.append("claude")
    if "codex" in providers and not user.codex_token_enc:
        missing.append("codex")
    if access.project.git_enabled and not access.project.git_token_enc and access.project.github_repo:
        missing.append("git-token")
    return {"missing": missing, "providers": list(providers)}
