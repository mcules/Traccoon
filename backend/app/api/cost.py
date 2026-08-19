import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.agents import CostEntry
from ..core.security import decrypt_secret
from ..models.ops import ProviderModel
from ..models.secrets import ProviderToken
from ..models.ticket import Issue
from ..models.user import User
from ..worker.secrets import resolve_provider_token
from .deps import Access, get_current_user, get_project_access, require_admin
from .issues import get_issue_access

router = APIRouter(tags=["cost"])


@router.get("/projects/{project_id}/costs")
async def project_costs(access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session)):
    pid = access.project.id
    # Bevorzugt direkt ueber CostEntry.project_id filtern (ueberlebt Issue-Loeschung).
    # Old rows without a project_id (before the denormalisation) keep counting over the issue join.
    cond = or_(
        CostEntry.project_id == pid,
        and_(CostEntry.project_id.is_(None), Issue.project_id == pid),
    )
    total = (
        await db.execute(
            select(func.coalesce(func.sum(CostEntry.cost_usd), 0.0),
                   func.coalesce(func.sum(CostEntry.input_tokens), 0),
                   func.coalesce(func.sum(CostEntry.output_tokens), 0))
            .outerjoin(Issue, Issue.id == CostEntry.issue_id)
            .where(cond)
        )
    ).one()
    by_agent = (
        await db.execute(
            select(CostEntry.agent, func.sum(CostEntry.cost_usd), func.count())
            .outerjoin(Issue, Issue.id == CostEntry.issue_id)
            .where(cond).group_by(CostEntry.agent)
        )
    ).all()
    by_model = (
        await db.execute(
            select(CostEntry.provider, CostEntry.model,
                   func.sum(CostEntry.cost_usd), func.sum(CostEntry.input_tokens),
                   func.sum(CostEntry.output_tokens), func.count())
            .outerjoin(Issue, Issue.id == CostEntry.issue_id)
            .where(cond).group_by(CostEntry.provider, CostEntry.model)
        )
    ).all()
    return {"total_usd": round(total[0], 4), "input_tokens": total[1], "output_tokens": total[2],
            "by_agent": [{"agent": a, "usd": round(c, 4), "calls": n} for a, c, n in by_agent],
            "by_model": [{"provider": p, "model": m, "usd": round(c, 4),
                          "input_tokens": it, "output_tokens": ot, "calls": n}
                         for p, m, c, it, ot, n in by_model]}


@router.get("/issues/{key}/costs")
async def issue_costs(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, _access = pair
    total = (
        await db.execute(
            select(func.coalesce(func.sum(CostEntry.cost_usd), 0.0),
                   func.coalesce(func.sum(CostEntry.input_tokens), 0),
                   func.coalesce(func.sum(CostEntry.output_tokens), 0))
            .where(CostEntry.issue_id == issue.id)
        )
    ).one()
    by_model = (
        await db.execute(
            select(CostEntry.provider, CostEntry.model,
                   func.sum(CostEntry.cost_usd), func.sum(CostEntry.input_tokens),
                   func.sum(CostEntry.output_tokens), func.count())
            .where(CostEntry.issue_id == issue.id)
            .group_by(CostEntry.provider, CostEntry.model)
        )
    ).all()
    return {"total_usd": round(total[0], 4), "input_tokens": total[1], "output_tokens": total[2],
            "by_model": [{"provider": p, "model": m, "usd": round(c, 4),
                          "input_tokens": it, "output_tokens": ot, "calls": n}
                         for p, m, c, it, ot, n in by_model]}


@router.get("/costs/global")
async def global_costs(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    total = (await db.execute(select(func.coalesce(func.sum(CostEntry.cost_usd), 0.0)))).scalar_one()
    by_model = (
        await db.execute(select(CostEntry.model, func.sum(CostEntry.cost_usd), func.count())
                         .group_by(CostEntry.model))
    ).all()
    return {"total_usd": round(total, 4),
            "by_model": [{"model": m, "usd": round(c, 4), "calls": n} for m, c, n in by_model]}


class PriceIn(BaseModel):
    provider: str
    model: str
    display_name: str = ""
    price_input: float = 0.0
    price_output: float = 0.0
    price_cache_read: float = 0.0
    context_tokens: int | None = None
    speed_tps: float | None = None
    enabled: bool = True


@router.get("/providers/models")
async def list_models(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # The catalog is not sensitive and feeds the model dropdowns in the agent editor (every user).
    rows = (await db.execute(select(ProviderModel).order_by(ProviderModel.provider, ProviderModel.model))).scalars().all()
    return [{"id": m.id, "provider": m.provider, "model": m.model, "display_name": m.display_name,
             "price_input": m.price_input, "price_output": m.price_output,
             "price_cache_read": m.price_cache_read, "enabled": m.enabled,
             "context_tokens": m.context_tokens, "speed_tps": m.speed_tps,
             "updated_at": m.updated_at} for m in rows]


@router.put("/providers/models")
async def upsert_model(data: PriceIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    m = (await db.execute(select(ProviderModel).where(ProviderModel.provider == data.provider,
                                                      ProviderModel.model == data.model))).scalar_one_or_none()
    if m is None:
        m = ProviderModel(provider=data.provider, model=data.model)
        db.add(m)
    m.display_name = data.display_name or data.model
    m.price_input = data.price_input
    m.price_output = data.price_output
    m.price_cache_read = data.price_cache_read
    m.context_tokens = data.context_tokens
    m.speed_tps = data.speed_tps
    m.enabled = data.enabled
    await db.commit()
    return {"ok": True}


@router.delete("/providers/models/{model_id}")
async def delete_model(model_id: int, _: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_session)):
    """Remove a catalog entry. Already billed runs stay untouched: CostEntry stores the
    finished amount, not the catalog reference."""
    m = await db.get(ProviderModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    await db.delete(m)
    await db.commit()
    return {"ok": True}


_ANTHROPIC_BETAS = "oauth-2025-04-20,claude-code-20250219"

# Price source: the provider APIs themselves deliver no prices (neither Anthropic nor
# OpenAI); models.dev is an open catalog without auth in exactly our units (USD per 1M tokens).
_MODELSDEV_URL = "https://models.dev/api.json"
# Our provider names mapped to the provider ids at models.dev. `openai` is often an
# OpenAI-compatible proxy here; local model names simply do not stand there and stay
# untouched then.
_PRICE_SOURCE = {"claude_code": "anthropic", "claude": "anthropic", "anthropic": "anthropic",
                 "codex": "openai", "openai": "openai"}


def _modelsdev_entry(catalog: dict, provider: str, model: str) -> dict | None:
    """Catalog entry for (provider, model id): exact, otherwise without the date suffix
    (claude-opus-4-6-20260101 to claude-opus-4-6)."""
    src = _PRICE_SOURCE.get(provider)
    if not src:
        return None
    models = (catalog.get(src) or {}).get("models") or {}
    entry = models.get(model)
    if entry is None:
        entry = models.get(re.sub(r"-\d{8}$", "", model))
    return entry if isinstance(entry, dict) else None


@router.post("/providers/models/prices")
async def fetch_prices(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Take prices from models.dev into the catalog (input, output, cache read, USD per 1M).

    Only set, never delete: models without a hit (local models behind an endpoint of their
    own) keep their existing prices."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(_MODELSDEV_URL)
        r.raise_for_status()
        catalog = r.json()
    rows = (await db.execute(select(ProviderModel).order_by(ProviderModel.provider,
                                                            ProviderModel.model))).scalars().all()
    changed: list[dict] = []
    unknown: list[str] = []
    unchanged = kontexte = 0
    for row in rows:
        entry = _modelsdev_entry(catalog, row.provider, row.model)
        cost = (entry or {}).get("cost")
        if not isinstance(cost, dict):
            unknown.append(f"{row.provider}/{row.model}")
            continue
        neu = (float(cost.get("input") or 0.0), float(cost.get("output") or 0.0),
               float(cost.get("cache_read") or 0.0))
        alt = (row.price_input, row.price_output, row.price_cache_read)
        if not row.display_name and entry.get("name"):
            row.display_name = str(entry["name"])[:150]
        # Only set the context window when it is still missing or has changed: a value
        # maintained by hand (a local model with a smaller window) is not overwritten, as
        # long as models.dev does not know the model at all (then we never get here).
        kontext = ((entry.get("limit") or {}).get("context")) if isinstance(entry.get("limit"), dict) else None
        if kontext and row.context_tokens != int(kontext):
            row.context_tokens = int(kontext)
            kontexte += 1
        if alt == neu:
            unchanged += 1
            continue
        row.price_input, row.price_output, row.price_cache_read = neu
        changed.append({"provider": row.provider, "model": row.model,
                        "from": {"input": alt[0], "output": alt[1], "cache_read": alt[2]},
                        "to": {"input": neu[0], "output": neu[1], "cache_read": neu[2]}})
    await db.commit()
    return {"source": "models.dev", "updated": changed, "unchanged": unchanged,
            "context_set": kontexte, "unknown": unknown}


# Non-chat models that OpenAI-compatible endpoints (LiteLLM, Ollama, vLLM …) deliver as well.
_NON_CHAT_HINTS = ("embed", "bge-", "gte-", "e5-", "rerank", "whisper", "tts", "dall-e",
                   "moderation", "stable-diffusion", "flux", "clip")


def _is_chat_model(model_id: str) -> bool:
    low = model_id.lower()
    return not any(h in low for h in _NON_CHAT_HINTS)


async def _fetch_provider_models(provider: str, token: str,
                                 base_url: str | None = None) -> list[tuple[str, str]]:
    """Query the available models live at the endpoint, giving [(model_id, display_name)].

    `base_url` is the endpoint URL of the token itself (an OpenAI-compatible proxy like
    LiteLLM); without it the provider default counts. With an own endpoint there is NO
    filtering on OpenAI names (gpt/o1/o3 …): there the models are called qwen3.6-…, Gemma3-4b and so on."""
    async with httpx.AsyncClient(timeout=30) as c:
        if provider in ("claude_code", "claude", "anthropic"):
            base = (base_url or "https://api.anthropic.com/v1").rstrip("/")
            r = await c.get(f"{base}/models?limit=100",
                            headers={"Authorization": f"Bearer {token}",
                                     "anthropic-version": "2023-06-01",
                                     "anthropic-beta": _ANTHROPIC_BETAS,
                                     "user-agent": "claude-cli/2.1.74 (external, cli)", "x-app": "cli"})
            r.raise_for_status()
            return [(m["id"], m.get("display_name") or m["id"]) for m in r.json().get("data", [])]
        if provider == "openai":
            base = (base_url or "https://api.openai.com/v1").rstrip("/")
            r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
            if base_url:
                # Own endpoint: take everything except embedding, rerank, audio and image.
                return [(i, i) for i in ids if _is_chat_model(i)]
            # Real OpenAI: only chat families (the catalog there is full of non-chat models).
            return [(i, i) for i in ids if i.startswith(("gpt", "o1", "o3", "o4", "chatgpt"))]
    return []


async def _model_endpoints(db: AsyncSession, user_id: int) -> list[tuple[str, str, str, str | None]]:
    """All queryable endpoints of the user, giving [(label, provider, token, base_url)].

    A user can have several named tokens per provider (agents choose over `token_name`),
    with OpenAI each with its own base URL. Without an own token the existing default
    (subscription or system secret) on the provider endpoint applies."""
    out: list[tuple[str, str, str, str | None]] = []
    seen: set[str] = set()
    rows = (await db.execute(
        select(ProviderToken).where(ProviderToken.user_id == user_id,
                                    ProviderToken.provider.in_(("claude_code", "openai")))
        .order_by(ProviderToken.provider, ProviderToken.name))).scalars().all()
    for row in rows:
        token = decrypt_secret(row.value_enc)
        if not token:
            continue
        seen.add(row.provider)
        out.append((f"{row.provider}:{row.name}", row.provider, token, row.base_url or None))
    for provider in ("claude_code", "openai"):
        if provider in seen:
            continue
        token = await resolve_provider_token(db, user_id, provider)
        if token:
            out.append((provider, provider, token, None))
    return out


@router.post("/providers/models/fetch")
async def fetch_models(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Fetch the available models live at the endpoints of the user and take them into the
    catalog. Prices of existing entries are kept; new ones start at 0. Models no endpoint of
    the provider knows any more are deactivated (not deleted, because old runs and costs
    still reference them)."""
    results: dict[str, dict] = {}
    live: dict[str, set[str]] = {}     # provider to all model ids reported live
    broken: set[str] = set()           # providers with at least one dead endpoint
    for label, provider, token, base_url in await _model_endpoints(db, user.id):
        try:
            models = await _fetch_provider_models(provider, token, base_url)
        except Exception as exc:  # noqa: BLE001
            results[label] = {"error": str(exc)[:200]}
            broken.add(provider)
            continue
        added = updated = 0
        for mid, name in models:
            row = (await db.execute(select(ProviderModel).where(
                ProviderModel.provider == provider, ProviderModel.model == mid))).scalar_one_or_none()
            if row is None:
                db.add(ProviderModel(provider=provider, model=mid, display_name=name))
                added += 1
            else:
                # Do NOT overwrite a name given by hand: OpenAI-compatible endpoints return
                # only the model id as the "name", so every fetch would have turned
                # "Qwen3.6 35B q8 (local)" back into "qwen3.6-35b-q8".
                if name and row.display_name in ("", row.model) and row.display_name != name:
                    row.display_name = name
                    updated += 1
                if not row.enabled:      # wieder aufgetaucht → wieder nutzbar
                    row.enabled = True
                    updated += 1
        live.setdefault(provider, set()).update(mid for mid, _ in models)
        results[label] = {"total": len(models), "added": added, "updated": updated}
    # Only clean up providers whose endpoints ALL answered without an error; otherwise models
    # of a momentarily dead endpoint would be deactivated wrongly.
    for provider, ids in live.items():
        if not ids or provider in broken:
            continue
        stale = (await db.execute(select(ProviderModel).where(
            ProviderModel.provider == provider, ProviderModel.model.notin_(ids),
            ProviderModel.enabled.is_(True)))).scalars().all()
        # Undated aliases (claude-sonnet-4-5 to …-20250929) do not stand in /v1/models but
        # work anyway; otherwise the default model name of all things would fly out.
        stale = [r for r in stale if not any(i.startswith(r.model + "-") for i in ids)]
        for row in stale:
            row.enabled = False
        if stale:
            results.setdefault(provider, {})["disabled"] = len(stale)
    await db.commit()
    return results
