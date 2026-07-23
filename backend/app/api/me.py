"""Persönliche Settings (/me/*) + Redis-Flags (Layer C) + Admin-Toggles."""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import get_flag, get_user_flag, set_flag, set_user_flag
from ..db import get_session
from ..models.user import User
from .deps import get_current_user, require_admin

router = APIRouter(tags=["me"])

# Per-User-Flags (Layer C)
USER_FLAGS = ["shift_end", "sonnet_max", "show_token_prices", "ticket_notify"]
# Globale Admin-Flags
GLOBAL_FLAGS = ["global_pause", "strict_success"]


class NightWindow(BaseModel):
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=23)
    days: list[int] = [0, 1, 2, 3, 4, 5, 6]


class BoolIn(BaseModel):
    active: bool


class IntIn(BaseModel):
    value: int


class StrIn(BaseModel):
    value: str


@router.put("/me/night-window", status_code=204)
async def set_night_window(d: NightWindow, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.night_start_hour, u.night_end_hour = d.start_hour, d.end_hour
    u.night_days = sorted({x for x in d.days if 0 <= x <= 6})
    await db.commit()


@router.put("/me/night-override", status_code=204)
async def set_night_override(d: BoolIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.night_override = d.active
    await db.commit()


@router.put("/me/runner-limit", status_code=204)
async def set_runner_limit(d: IntIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.max_runners = max(1, min(d.value, 20))
    await db.commit()


@router.put("/me/telegram-chat", status_code=204)
async def set_telegram(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.telegram_chat_id = d.value or None
    await db.commit()


@router.put("/me/theme", status_code=204)
async def set_theme(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.theme = d.value if d.value in ("light", "dark") else "dark"
    await db.commit()


@router.put("/me/default-view", status_code=204)
async def set_default_view(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.default_project_view = d.value if d.value in ("board", "chat") else "board"
    await db.commit()


class McpReachIn(BaseModel):
    servers: list[str]


@router.get("/me/mcp")
async def my_mcp(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Eigene MCP-Reichweite (MCPJungle-Gruppe) + wählbare Server. Self-Service konfigurierbar."""
    from ..services.mcp_provision import list_available_servers
    available: list[str] = []
    try:
        available = await list_available_servers()
    except Exception:  # noqa: BLE001  (MCPJungle nicht erreichbar / kein Admin-Token)
        pass
    return {"group": u.mcp_group or "", "servers": list(u.mcp_servers or []),
            "provisioned": bool(u.mcp_group and u.mcp_token_enc), "available": available}


@router.put("/me/mcp")
async def set_my_mcp(d: McpReachIn, u: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    """MCP-Reichweite selbst setzen: Gruppe + gescopeten Token (neu) provisionieren."""
    from ..services.mcp_provision import McpProvisionError, provision_user_mcp
    try:
        await provision_user_mcp(db, u, d.servers)
    except McpProvisionError as exc:
        from fastapi import HTTPException
        raise HTTPException(503, str(exc))
    return {"group": u.mcp_group or "", "servers": list(u.mcp_servers or []),
            "provisioned": bool(u.mcp_group and u.mcp_token_enc)}


@router.get("/me/onboarding")
async def onboarding(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Was fehlt noch, damit echte Agentenläufe möglich sind — geprüft, nicht geraten."""
    from sqlalchemy import select

    from ..core.redis import runner_connected
    from ..models.project import Project, ProjectMember
    from ..models.secrets import ProviderToken

    memberships = (await db.execute(
        select(ProjectMember, Project).join(Project, Project.id == ProjectMember.project_id)
        .where(ProjectMember.user_id == u.id))).all()
    projects = [p for _m, p in memberships]
    can_assign = [p for m, p in memberships if m.ai_assign]
    git_ready = [p for p in can_assign if p.git_enabled and p.github_repo and p.git_token_enc]
    verify_ready = [p for p in can_assign if p.verify_command]
    # LLM-Token vorhanden = benannter Provider-Token ODER Alt-Feld (Subscription).
    has_token = bool((await db.execute(select(ProviderToken.id).where(
        ProviderToken.user_id == u.id).limit(1))).first()) or bool(
        u.claude_oauth_token_enc or u.codex_token_enc)

    steps = [
        {"key": "claude_token", "title": "LLM-Token hinterlegen",
         "hint": "Provider wählen + Key eingeben unter Einstellungen → Secret-Tresor.",
         "done": has_token, "required": True},
        {"key": "runner", "title": "Worker erreichbar",
         "hint": "Der Worker-Container muss laufen, sonst bleibt jede Zuweisung liegen.",
         "done": await runner_connected(), "required": True},
        {"key": "project", "title": "Projekt anlegen",
         "hint": "Mindestens ein Projekt, in dem du das KI-Recht hast.",
         "done": bool(can_assign), "required": True},
        {"key": "git", "title": "Git verbinden",
         "hint": "Repository + Token im Projekt-Tab „Einstellungen“ — ohne Git kann kein Code entstehen.",
         "done": bool(git_ready), "required": False},
        {"key": "verify", "title": "Prüfbefehl setzen",
         "hint": "z. B. „npm run build“ — sonst kann niemand belegen, dass die Arbeit grün ist.",
         "done": bool(verify_ready), "required": False},
        {"key": "telegram", "title": "Telegram verbinden (optional)",
         "hint": "Benachrichtigungen und Freigaben per Chat.",
         "done": bool(u.telegram_chat_id), "required": False},
    ]
    offen = [s for s in steps if not s["done"] and s["required"]]
    return {"steps": steps, "ready": not offen, "projects": len(projects),
            "dismissed": u.onboarded_at is not None}


@router.post("/me/onboarding/dismiss", status_code=204)
async def dismiss_onboarding(u: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    u.onboarded_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()


@router.get("/me/flags")
async def my_flags(u: User = Depends(get_current_user)):
    out = {f: await get_user_flag(f, u.id) for f in USER_FLAGS}
    out.update({f: await get_flag(f) for f in GLOBAL_FLAGS})
    out.update(night_start_hour=u.night_start_hour, night_end_hour=u.night_end_hour,
               night_days=u.night_days, night_override=u.night_override, max_runners=u.max_runners,
               telegram_chat_id=u.telegram_chat_id)
    return out


# Per-User-Flag-Toggles: POST = an, DELETE = aus
def _mk_user_flag(name: str):
    async def on(u: User = Depends(get_current_user)):
        await set_user_flag(name, u.id, True)
        return {name: True}

    async def off(u: User = Depends(get_current_user)):
        await set_user_flag(name, u.id, False)
        return {name: False}
    return on, off


for _f in USER_FLAGS:
    _on, _off = _mk_user_flag(_f)
    router.add_api_route(f"/me/{_f.replace('_', '-')}", _on, methods=["POST"])
    router.add_api_route(f"/me/{_f.replace('_', '-')}", _off, methods=["DELETE"])


# Admin-Toggles
def _mk_global_flag(name: str):
    async def on(_: User = Depends(require_admin)):
        await set_flag(name, True)
        return {name: True}

    async def off(_: User = Depends(require_admin)):
        await set_flag(name, False)
        return {name: False}
    return on, off


_gp_on, _gp_off = _mk_global_flag("global_pause")
router.add_api_route("/runner/global-pause", _gp_on, methods=["POST"], tags=["admin"])
router.add_api_route("/runner/global-pause", _gp_off, methods=["DELETE"], tags=["admin"])
_ss_on, _ss_off = _mk_global_flag("strict_success")
router.add_api_route("/strict-success", _ss_on, methods=["POST"], tags=["admin"])
router.add_api_route("/strict-success", _ss_off, methods=["DELETE"], tags=["admin"])
