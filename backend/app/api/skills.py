import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decrypt_secret, encrypt_secret
from ..db import get_session
from ..models.agents import AgentDefinition
from ..models.plugins import McpServer, Skill
from ..models.user import User
from .deps import get_current_user, is_owner_or_admin

router = APIRouter(tags=["skills-mcp"])


# ---------------- Skills (versioniert) ----------------

class SkillIn(BaseModel):
    key: str = ""          # optional: empty means derived automatically from the name
    name: str = ""
    description: str = ""
    body: str
    scope: str = "global"
    project_id: int | None = None
    autostart: bool = False


def _slug(text: str) -> str:
    """Name → Skill-Key: klein, nur a-z0-9 und Bindestriche."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "skill"


@router.get("/skills")
async def list_skills(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # neueste aktive Version je key
    rows = (await db.execute(select(Skill).where(Skill.active.is_(True)).order_by(Skill.key, Skill.version.desc()))).scalars().all()
    seen, out = set(), []
    for s in rows:
        if s.key in seen:
            continue
        seen.add(s.key)
        out.append({"id": s.id, "key": s.key, "name": s.name, "description": s.description,
                    "version": s.version, "scope": s.scope, "autostart": s.autostart})
    return out


@router.post("/skills", status_code=201)
async def create_skill(data: SkillIn, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # The key is optional: empty means derive it from the name (otherwise there is no name, which is an error).
    key = data.key.strip() or _slug(data.name)
    if not data.name.strip() and not data.key.strip():
        raise HTTPException(400, "A name (or key) is required")
    maxv = (await db.execute(select(func.max(Skill.version)).where(Skill.key == key))).scalar_one()
    s = Skill(key=key, name=data.name or key, description=data.description, body=data.body,
              scope=data.scope, project_id=data.project_id, autostart=data.autostart, version=(maxv or 0) + 1)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"id": s.id, "key": s.key, "version": s.version}


@router.get("/skills/{key}/versions")
async def skill_versions(key: str, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Skill).where(Skill.key == key).order_by(Skill.version.desc()))).scalars().all()
    return [{"id": s.id, "version": s.version, "created_at": s.created_at} for s in rows]


@router.delete("/skills/{sid}", status_code=204)
async def delete_skill(sid: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    s = await db.get(Skill, sid)
    if s:
        s.active = False
        await db.commit()


# ---------------- MCP-Registry ----------------

class McpIn(BaseModel):
    name: str
    display_name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list = []
    env: dict = {}
    url: str = ""
    headers: dict = {}
    variables: list = []   # [{key,label,secret,required}]: slots for instances
    enabled: bool = False


@router.get("/mcp-servers")
async def list_mcp(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(McpServer).where((McpServer.user_id == user.id) | (McpServer.user_id.is_(None))))).scalars().all()
    return [{"id": m.id, "name": m.name, "display_name": m.display_name, "transport": m.transport,
             "url": m.url, "variables": m.variables or [], "enabled": m.enabled, "tools": m.tools}
            for m in rows]


@router.post("/mcp-servers", status_code=201)
async def create_mcp(data: McpIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    import json
    m = McpServer(user_id=user.id, name=data.name, display_name=data.display_name or data.name,
                  transport=data.transport, command=data.command, args=data.args,
                  env_enc=encrypt_secret(json.dumps(data.env)) if data.env else "",
                  url=data.url, headers=data.headers, variables=data.variables, enabled=data.enabled)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return {"id": m.id}


@router.put("/mcp-servers/{mid}", status_code=204)
async def update_mcp(mid: int, data: McpIn, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    import json
    m = await db.get(McpServer, mid)
    if m is None or (m.user_id != user.id and user.global_role.value != "admin"):
        raise HTTPException(404, "MCP server not found")
    m.name, m.display_name = data.name, data.display_name or data.name
    m.transport, m.command, m.args = data.transport, data.command, data.args
    m.url, m.headers, m.variables, m.enabled = data.url, data.headers, data.variables, data.enabled
    if data.env:  # an empty env leaves existing server-fixed secrets untouched
        m.env_enc = encrypt_secret(json.dumps(data.env))
    await db.commit()


@router.delete("/mcp-servers/{mid}", status_code=204)
async def delete_mcp(mid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    m = await db.get(McpServer, mid)
    if m and (m.user_id == user.id or user.global_role.value == "admin"):
        await db.delete(m)
        await db.commit()


# ---------------- MCP-Instanzen (agent-eigen) ----------------

class McpInstanceIn(BaseModel):
    server_id: int
    name: str = ""
    values: dict = {}   # {var_key: value}


async def _agent_owned(db: AsyncSession, agent_id: int, user: User) -> AgentDefinition:
    a = await db.get(AgentDefinition, agent_id)
    if a is None or not is_owner_or_admin(a.user_id, user):
        raise HTTPException(404, "Agent not found")
    return a


def _check_required_vars(srv: McpServer, values: dict) -> None:
    """Checks that all variables of the server marked as `required` are set non-empty in `values`."""
    missing = []
    for var in (srv.variables or []):
        if not var.get("required"):
            continue
        val = (values or {}).get(var.get("key"))
        if isinstance(val, str):
            val = val.strip()
        if not val:
            missing.append(var.get("label") or var.get("key"))
    if missing:
        raise HTTPException(400, f"A required variable is missing: {', '.join(missing)}")


@router.get("/agents/{agent_id}/mcp-instances")
async def list_instances(agent_id: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    from ..models.plugins import McpInstance
    await _agent_owned(db, agent_id, user)
    rows = (await db.execute(select(McpInstance).where(McpInstance.agent_id == agent_id))).scalars().all()
    out = []
    for i in rows:
        keys = list(json.loads(decrypt_secret(i.values_enc)).keys()) if i.values_enc else []
        out.append({"id": i.id, "server_id": i.server_id, "name": i.name, "set_keys": keys})
    return out


@router.post("/agents/{agent_id}/mcp-instances", status_code=201)
async def add_instance(agent_id: int, data: McpInstanceIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    from ..models.plugins import McpInstance
    a = await _agent_owned(db, agent_id, user)
    srv = await db.get(McpServer, data.server_id)
    if srv is None:
        raise HTTPException(404, "MCP server not found")
    _check_required_vars(srv, data.values)
    inst = McpInstance(agent_id=agent_id, server_id=data.server_id,
                       name=data.name or srv.name,
                       values_enc=encrypt_secret(json.dumps(data.values)) if data.values else "")
    db.add(inst)
    if a.origin_agent_id is not None:  # changing a linked copy marks it as edited
        a.customized = True
    await db.commit()
    await db.refresh(inst)
    return {"id": inst.id}


@router.put("/agents/{agent_id}/mcp-instances/{iid}", status_code=204)
async def update_instance(agent_id: int, iid: int, data: McpInstanceIn,
                          user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    from ..models.plugins import McpInstance
    a = await _agent_owned(db, agent_id, user)
    inst = await db.get(McpInstance, iid)
    if inst is None or inst.agent_id != agent_id:
        raise HTTPException(404, "Instance not found")
    inst.name = data.name or inst.name
    if data.values:
        srv = await db.get(McpServer, inst.server_id)
        if srv is not None:
            _check_required_vars(srv, data.values)
        inst.values_enc = encrypt_secret(json.dumps(data.values))
    if a.origin_agent_id is not None:
        a.customized = True
    await db.commit()


@router.delete("/agents/{agent_id}/mcp-instances/{iid}", status_code=204)
async def del_instance(agent_id: int, iid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    from ..models.plugins import McpInstance
    a = await _agent_owned(db, agent_id, user)
    inst = await db.get(McpInstance, iid)
    if inst and inst.agent_id == agent_id:
        await db.delete(inst)
        if a.origin_agent_id is not None:
            a.customized = True
        await db.commit()


async def load_skill_bodies(db: AsyncSession, keys: list[str]) -> str:
    """Active, newest versions of the named skills as text (for the agent prompt)."""
    if not keys:
        return ""
    parts = []
    for key in keys:
        s = (await db.execute(select(Skill).where(Skill.key == key, Skill.active.is_(True))
                              .order_by(Skill.version.desc()))).scalars().first()
        if s:
            head = f"## AKTIVER MODUS: {s.name}" if s.autostart else f"## Skill: {s.name}"
            parts.append(f"{head}\n{s.body}")
    return "\n\n".join(parts)
