from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.agents import CostEntry, Run
from ..models.predecessor import ProviderModel
from ..models.ticket import Issue
from ..models.user import User
from .deps import Access, get_current_user, get_project_access, require_admin

router = APIRouter(tags=["cost"])

# Default-Modellpreise (USD / 1M Tokens) — per PATCH anpassbar.
_DEFAULT_PRICES = [
    ("claude_code", "claude-sonnet-4-5", "Claude Sonnet 4.5", 3.0, 15.0),
    ("claude_code", "claude-opus-4-1", "Claude Opus 4.1", 15.0, 75.0),
    ("claude_code", "claude-haiku-4-5", "Claude Haiku 4.5", 0.8, 4.0),
    ("codex", "gpt-5", "GPT-5 (Codex)", 1.25, 10.0),
    ("openai", "gpt-4o", "GPT-4o", 2.5, 10.0),
    ("openai", "gpt-4o-mini", "GPT-4o mini", 0.15, 0.6),
    ("openai", "o3", "o3", 2.0, 8.0),
]


@router.get("/projects/{project_id}/costs")
async def project_costs(access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session)):
    pid = access.project.id
    # Bevorzugt direkt ueber CostEntry.project_id filtern (ueberlebt Issue-Loeschung).
    # Alt-Zeilen ohne project_id (vor der Denormalisierung) weiter ueber den Issue-Join zaehlen.
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
    return {"total_usd": round(total[0], 4), "input_tokens": total[1], "output_tokens": total[2],
            "by_agent": [{"agent": a, "usd": round(c, 4), "calls": n} for a, c, n in by_agent]}


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


@router.get("/providers/models")
async def list_models(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # Katalog ist nicht sensibel und speist die Modell-Dropdowns im Agent-Editor (jeder User).
    rows = (await db.execute(select(ProviderModel).order_by(ProviderModel.provider, ProviderModel.model))).scalars().all()
    return [{"id": m.id, "provider": m.provider, "model": m.model, "display_name": m.display_name,
             "price_input": m.price_input, "price_output": m.price_output, "enabled": m.enabled} for m in rows]


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
    await db.commit()
    return {"ok": True}


@router.post("/providers/refresh")
async def refresh_catalog(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Seedet/aktualisiert den Modellkatalog mit Default-Preisen (falls noch nicht vorhanden)."""
    added = 0
    for prov, model, name, pin, pout in _DEFAULT_PRICES:
        exists = (await db.execute(select(ProviderModel).where(
            ProviderModel.provider == prov, ProviderModel.model == model))).scalar_one_or_none()
        if exists is None:
            db.add(ProviderModel(provider=prov, model=model, display_name=name,
                                 price_input=pin, price_output=pout))
            added += 1
    await db.commit()
    return {"added": added}
