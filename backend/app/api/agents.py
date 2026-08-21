from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Fehler
from ..db import get_session
from ..models.agents import AgentDefinition
from ..models.user import User
from .deps import get_current_user, is_owner_or_admin

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentIn(BaseModel):
    role: str
    display_name: str = ""
    system_prompt: str = ""
    provider: str = "claude_code"
    model: str = ""
    token_name: str = ""
    fallback: str | None = None
    fallback_model: str = ""
    fallback_token_name: str = ""
    effort: str = ""
    temperature: float = 0.3
    max_tokens: int = 8192
    max_context_tokens: int | None = None
    max_turns_planning: int = 10
    max_turns_execution: int = 80
    can_code: bool = False
    can_read_code: bool = False
    can_delegate: bool = False
    web_search: bool = False
    learns: bool = True
    allowed_tools: list = []
    allowed_skills: list = []
    autoload_skills: list = []
    delegate_to: list = []
    active: bool = True
    project_id: int | None = None


class AgentOut(AgentIn):
    id: int
    # Where the definition comes from: NULL = shipped system wide, otherwise the owner.
    # Together with project_id that makes it distinguishable in the interface which of
    # several roles of the same name actually applies.
    user_id: int | None = None
    origin_agent_id: int | None = None
    customized: bool = False
    model_config = {"from_attributes": True}


@router.get("", response_model=list[AgentOut])
async def list_agents(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
                      project_id: int | None = None):
    q = select(AgentDefinition).where(
        (AgentDefinition.user_id == user.id) | (AgentDefinition.user_id.is_(None)))
    if project_id is not None:
        q = q.where((AgentDefinition.project_id == project_id) | (AgentDefinition.project_id.is_(None)))
    else:
        q = q.where(AgentDefinition.project_id.is_(None))
    rows = (await db.execute(q.order_by(AgentDefinition.role))).scalars().all()
    return list(rows)


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(data: AgentIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    a = AgentDefinition(user_id=user.id, **data.model_dump())
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: int, data: AgentIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    a = await db.get(AgentDefinition, agent_id)
    if a is None or not is_owner_or_admin(a.user_id, user):
        raise Fehler(status.HTTP_404_NOT_FOUND, "err.agent_not_found", "Agent not found")
    for field, value in data.model_dump().items():
        setattr(a, field, value)
    # Editing a linked copy marks it as "edited", so no more syncing.
    if a.origin_agent_id is not None:
        a.customized = True
    await db.commit()
    await db.refresh(a)
    return a


# Config fields taken over on copying and syncing (not id/role/user/project/origin).
_COPY_FIELDS = (
    "display_name", "system_prompt", "provider", "model", "token_name", "fallback",
    "fallback_model", "fallback_token_name", "effort", "temperature", "max_tokens",
    "max_context_tokens", "max_turns_planning", "max_turns_execution", "can_code",
    "can_read_code", "can_delegate", "web_search", "learns", "allowed_tools", "allowed_skills",
    "autoload_skills", "delegate_to", "active",
)


async def _copy_instances(db: AsyncSession, src_agent_id: int, dst_agent_id: int) -> None:
    """Copy the MCP instances of one agent into another (values taken over)."""
    from ..models.plugins import McpInstance
    from sqlalchemy import select as _sel
    for inst in (await db.execute(_sel(McpInstance).where(
            McpInstance.agent_id == src_agent_id))).scalars().all():
        db.add(McpInstance(agent_id=dst_agent_id, server_id=inst.server_id,
                           name=inst.name, values_enc=inst.values_enc))


class CopyToProjectIn(BaseModel):
    project_id: int


@router.post("/{agent_id}/copy-to-project", response_model=AgentOut, status_code=201)
async def copy_to_project(agent_id: int, data: CopyToProjectIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Load a global agent into a project as a linked copy (a snapshot, but syncable)."""
    src = await db.get(AgentDefinition, agent_id)
    if src is None or src.user_id not in (None, user.id):
        raise Fehler(404, "err.agent_not_found", "Agent not found")
    # The target project has to exist and the user has to be at least a maintainer there.
    from ..models.project import Project
    proj = await db.get(Project, data.project_id)
    if proj is None:
        raise Fehler(404, "err.target_project_not_found", "Target project not found")
    from .deps import build_access
    from ..models.enums import ProjectRole
    if not (await build_access(proj, user, db)).has_role(ProjectRole.maintainer):
        raise Fehler(403, "err.maintainer_rights_target_project",
                     "Maintainer rights in the target project are required")
    copy = AgentDefinition(user_id=user.id, role=src.role, project_id=data.project_id,
                           origin_agent_id=src.id, customized=False,
                           **{f: getattr(src, f) for f in _COPY_FIELDS})
    db.add(copy)
    await db.flush()
    await _copy_instances(db, src.id, copy.id)
    await db.commit()
    await db.refresh(copy)
    return copy


@router.post("/{agent_id}/sync-linked")
async def sync_linked(agent_id: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Bring all unedited linked copies of this agent up to date."""
    from ..models.plugins import McpInstance
    src = await db.get(AgentDefinition, agent_id)
    if src is None or src.user_id not in (None, user.id):
        raise Fehler(404, "err.agent_not_found", "Agent not found")
    # Update only ONE'S OWN unedited copies, never those of other users.
    copies = (await db.execute(select(AgentDefinition).where(
        AgentDefinition.origin_agent_id == agent_id,
        AgentDefinition.customized.is_(False),
        AgentDefinition.user_id == user.id))).scalars().all()
    for c in copies:
        for f in _COPY_FIELDS:
            setattr(c, f, getattr(src, f))
        # Take the instances over freshly
        for old in (await db.execute(select(McpInstance).where(
                McpInstance.agent_id == c.id))).scalars().all():
            await db.delete(old)
        await db.flush()
        await _copy_instances(db, src.id, c.id)
    await db.commit()
    return {"synced": len(copies)}


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    a = await db.get(AgentDefinition, agent_id)
    if a is not None and a.user_id in (None, user.id):
        await db.delete(a)
        await db.commit()


DEFAULT_ROLES = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"]
_CAPS = {
    # The PM plans and splits code tickets, so it needs read access to the code (but does not write).
    # Planners output the complete plan as ONE tool argument (submit_plan), hence a high
    # max_tokens; otherwise the answer is truncated and the tool arguments are incomplete.
    "project_manager": dict(can_delegate=True, can_read_code=True, max_turns_execution=40,
                            max_tokens=16384, max_turns_planning=20),
    "architect": dict(can_read_code=True, max_tokens=16384, max_turns_planning=20),
    "developer": dict(can_code=True),
    # The reviewer reads a lot and writes little (`<review-ok/>` or a list of findings).
    # On sonnet-5/opus-5 the thinking shares `max_tokens` with the answer, and with the
    # default level it ate the budget and the run died truncated (run 744).
    "code_reviewer": dict(can_read_code=True, effort="medium"),
    "tester": dict(can_code=True),
    "devops": dict(can_code=True),
}


async def seed_default_agents(db: AsyncSession, user_id: int) -> None:
    """Creates default agent templates for a user (idempotent)."""
    for role in DEFAULT_ROLES:
        exists = (await db.execute(select(AgentDefinition).where(
            AgentDefinition.user_id == user_id, AgentDefinition.role == role,
            AgentDefinition.project_id.is_(None)))).scalar_one_or_none()
        if exists is None:
            db.add(AgentDefinition(user_id=user_id, role=role, display_name=role,
                                   provider="claude_code", **_CAPS.get(role, {})))
    await db.commit()


@router.post("/seed-defaults", response_model=list[AgentOut])
async def seed_defaults(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    await seed_default_agents(db, user.id)
    rows = (await db.execute(select(AgentDefinition).where(
        AgentDefinition.user_id == user.id, AgentDefinition.project_id.is_(None))
        .order_by(AgentDefinition.role))).scalars().all()
    return list(rows)
