"""Seed: the system user and the bootstrap admin on the first start."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .core.security import hash_password
from .models.enums import GlobalRole, UserStatus
from .models.user import SYSTEM_USER_ID, User


async def seed(db: AsyncSession) -> None:
    # System user (the first insert, so id=1 on a fresh database)
    system = (await db.execute(select(User).where(User.username == "system"))).scalar_one_or_none()
    if system is None:
        db.add(User(
            email="system@local", username="system", display_name="System",
            password_hash="", global_role=GlobalRole.admin, status=UserStatus.active,
        ))
        await db.commit()

    # Bootstrap admin from the environment, only when no real user exists yet
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        real = (
            await db.execute(
                select(func.count()).select_from(User).where(User.id != SYSTEM_USER_ID)
            )
        ).scalar_one()
        if real == 0:
            db.add(User(
                email=settings.bootstrap_admin_email.lower(),
                username=settings.bootstrap_admin_username or "admin",
                display_name="Admin",
                password_hash=hash_password(settings.bootstrap_admin_password),
                global_role=GlobalRole.admin, status=UserStatus.active,
            ))
            await db.commit()

    # Seed the provider model catalog (default prices for the cost computation)
    from .models.ops import ProviderModel
    prices = [
        ("claude_code", "claude-sonnet-4-5", "Claude Sonnet 4.5", 3.0, 15.0),
        ("claude_code", "claude-opus-4-1", "Claude Opus 4.1", 15.0, 75.0),
        ("claude_code", "claude-haiku-4-5", "Claude Haiku 4.5", 0.8, 4.0),
        ("codex", "gpt-5", "GPT-5 (Codex)", 1.25, 10.0),
        # No openai seed: the provider is mostly an OpenAI-compatible endpoint of our own
        # (LiteLLM and company) with completely different model names, so the catalog comes
        # from POST /providers/models/fetch.
    ]
    for prov, model, name, pin, pout in prices:
        exists = (await db.execute(
            select(ProviderModel).where(ProviderModel.provider == prov, ProviderModel.model == model)
        )).scalar_one_or_none()
        if exists is None:
            db.add(ProviderModel(provider=prov, model=model, display_name=name,
                                 price_input=pin, price_output=pout))
    await db.commit()

    # Default agents for active users without templates (covers the bootstrap admin)
    from .api.agents import seed_default_agents
    from .models.agents import AgentDefinition
    active = (await db.execute(select(User).where(
        User.status == UserStatus.active, User.id != SYSTEM_USER_ID))).scalars().all()
    for u in active:
        has = (await db.execute(select(AgentDefinition).where(
            AgentDefinition.user_id == u.id, AgentDefinition.project_id.is_(None)))).scalars().first()
        if has is None:
            await seed_default_agents(db, u.id)
