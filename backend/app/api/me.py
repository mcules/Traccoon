"""Personal settings (/me/*) plus Redis flags (layer C) plus admin toggles."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.redis import get_flag, get_user_flag, set_flag, set_user_flag
from ..db import get_session
from ..models.user import User
from ..schemas.auth import _valid_email
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


ASSISTANT_NOTIFY = ("needed", "always", "errors", "never")


@router.put("/me/assistant-notify", status_code=204)
async def set_assistant_notify(d: StrIn, u: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_session)):
    """When the personal assistant speaks up. The default `needed`: only when it explicitly
    reports something, on errors and in the chat; finished filing stays silent."""
    u.assistant_notify = d.value if d.value in ASSISTANT_NOTIFY else "needed"
    await db.commit()


@router.put("/me/timezone", status_code=204)
async def set_timezone(d: StrIn, u: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Timezone of this person (IANA). It decides what "8 o'clock" means — in the UI,
    im Nachtfenster und im Zeitplan ihrer Jobs."""
    from zoneinfo import ZoneInfo, available_timezones

    name = (d.value or "").strip()
    if name not in available_timezones():
        raise Error(400, "err.unknown_timezone", "Unknown time zone '{name}'", name=name)
    ZoneInfo(name)   # loads it once, so a broken state of the data stands out here
    u.timezone = name
    await db.commit()


@router.get("/timezones")
async def list_timezones(_: User = Depends(get_current_user)) -> list[str]:
    """A choice for the UI: the zones this server really knows."""
    from zoneinfo import available_timezones

    return sorted(available_timezones())


@router.put("/me/vault-memory-path", status_code=204)
async def set_vault_memory_path(d: StrIn, u: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_session)):
    """Memory folder in the Obsidian vault (ABC-30). The agents file their learned rules
    below it and read them again on every run. Empty = no memory."""
    u.vault_memory_path = (d.value or "").strip().strip("/")[:500]
    await db.commit()


@router.put("/me/telegram-chat", status_code=204)
async def set_telegram(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.telegram_chat_id = d.value or None
    await db.commit()


class NotifyIn(BaseModel):
    """How this person wants to be reached. Empty fields stay unchanged."""
    notify_default: str | None = None       # telegram | email | ziel
    notify_email: str | None = None         # leer = Anmelde-Adresse benutzen
    telegram_chat_id: str | None = None
    # Kanal „ziel“: welches Ziel aufgerufen wird (0 = keins).
    notify_destination_id: int | None = None


@router.put("/me/notify", status_code=204)
async def set_notify(d: NotifyIn, u: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    """Manage the notification channels in the profile.

    The default decides where a message goes when the sender names no channel, and that is
    the normal case: a flow often knows its recipient only at runtime and knows nothing about
    their habits.
    """
    from ..services.notify import CHANNELS
    if d.notify_default is not None:
        if d.notify_default not in CHANNELS:
            raise Error(status.HTTP_400_BAD_REQUEST, "err.unknown_channel_possible",
                         "Unknown channel, possible: {moeglich}", possible=', '.join(CHANNELS))
        u.notify_default = d.notify_default
    if d.notify_email is not None:
        raw = d.notify_email.strip()
        u.notify_email = _valid_email(raw) if raw else None
    if d.telegram_chat_id is not None:
        u.telegram_chat_id = d.telegram_chat_id.strip() or None
    if d.notify_destination_id is not None:
        # Only a destination this person may call as well — otherwise the channel would be a way
        # an fremde Anmeldedaten.
        from ..services.destinations import visible
        target_id = int(d.notify_destination_id) or None
        if target_id is not None:
            allowed = {z.id for z in await visible(db, owner_id=u.id)}
            if target_id not in allowed:
                raise Error(status.HTTP_400_BAD_REQUEST, "err.unknown_destination",
                             "Unknown destination")
        u.notify_destination_id = target_id
    await db.commit()


@router.put("/me/locale", status_code=204)
async def set_locale(d: StrIn, u: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    """UI language of this person. Unknown values fall back to the source language."""
    value = (d.value or "de").strip().lower().replace("_", "-")[:10]
    u.locale = value if value and value.replace("-", "").isalnum() else "de"
    await db.commit()


@router.put("/me/theme", status_code=204)
async def set_theme(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.theme = d.value if d.value in ("light", "dark") else "dark"
    await db.commit()


@router.put("/me/email", status_code=204)
async def set_email(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Change one's own e-mail (self-service). Empty = remove the e-mail (then no e-mail login)."""
    raw = (d.value or "").strip()
    if not raw:
        u.email = None
        await db.commit()
        return
    email = _valid_email(raw)  # raises on an invalid format
    other = (await db.execute(
        select(User).where(User.email == email, User.id != u.id))).scalar_one_or_none()
    if other is not None:
        raise Error(status.HTTP_409_CONFLICT, "err.e_mail_already_taken", "E-mail already taken")
    u.email = email
    await db.commit()


@router.put("/me/default-view", status_code=204)
async def set_default_view(d: StrIn, u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    u.default_project_view = d.value if d.value in ("board", "chat") else "board"
    await db.commit()


@router.put("/me/ticket-open-mode", status_code=204)
async def set_ticket_open_mode(d: StrIn, u: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_session)):
    """How a ticket opens on a left click: popup (drawer) or page (full page)."""
    u.ticket_open_mode = d.value if d.value in ("popup", "page") else "popup"
    await db.commit()


@router.put("/me/pm-chat-style", status_code=204)
async def set_pm_chat_style(d: StrIn, u: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)):
    """Presentation of the PM chat: bubbles or cli (terminal look)."""
    u.pm_chat_style = d.value if d.value in ("bubbles", "cli") else "bubbles"
    await db.commit()


class TicketLayoutIn(BaseModel):
    left: list[str] = []
    right: list[str] = []


@router.put("/me/ticket-layout", status_code=204)
async def set_ticket_layout(d: TicketLayoutIn, u: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)):
    """Save the user specific block arrangement of the full ticket page."""
    u.ticket_layout = {"left": d.left[:40], "right": d.right[:40]}
    await db.commit()


class McpReachIn(BaseModel):
    servers: list[str]


@router.get("/me/mcp")
async def my_mcp(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """One's own MCP reach (MCPJungle group) plus selectable servers. Configurable self-service."""
    from ..services.mcp_provision import list_available_servers
    available: list[str] = []
    try:
        available = await list_available_servers()
    except Exception:  # noqa: BLE001  (MCPJungle unreachable or no admin token)
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


@router.post("/me/mcp/import")
async def import_mcp(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Take over the MCPJungle servers as real McpServer registry entries (editable like
    manual ones). Switches the gateway group off (the registry replaces it)."""
    from ..services.mcp_provision import McpProvisionError, import_registry_from_jungle
    try:
        return await import_registry_from_jungle(db, u)
    except McpProvisionError as exc:
        from fastapi import HTTPException
        raise HTTPException(503, str(exc))


@router.get("/me/onboarding")
async def onboarding(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """What is still missing for real agent runs to be possible: checked, not guessed."""
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
    # An LLM token is present = a named provider token OR the legacy field (subscription).
    has_token = bool((await db.execute(select(ProviderToken.id).where(
        ProviderToken.user_id == u.id).limit(1))).first()) or bool(
        u.claude_oauth_token_enc or u.codex_token_enc)

    # Label and hint come from the server catalog, in the language of the reader: the list is
    # the first thing somebody sees after logging in.
    from ..services.i18n import tr
    done = {"claude_token": has_token, "runner": await runner_connected(),
              "project": bool(can_assign), "git": bool(git_ready),
              "verify": bool(verify_ready), "telegram": bool(u.telegram_chat_id)}
    required = {"claude_token", "runner", "project"}
    steps = [
        {"key": k,
         "title": await tr(db, f"server.onboarding.{k}", u.locale),
         "hint": await tr(db, f"server.onboarding.{k}_hinweis", u.locale),
         "done": done[k], "required": k in required}
        for k in ("claude_token", "runner", "project", "git", "verify", "telegram")
    ]
    open_ones = [s for s in steps if not s["done"] and s["required"]]
    return {"steps": steps, "ready": not open_ones, "projects": len(projects),
            "dismissed": u.onboarded_at is not None}


@router.post("/me/onboarding/dismiss", status_code=204)
async def dismiss_onboarding(u: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    u.onboarded_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()


# Ticket agent states that require a human reaction (the work list).
_WAIT_STATES = ["plan_review", "to_test", "hold", "failed"]


@router.get("/me/dashboard")
async def my_dashboard(u: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Personal start dashboard: my open and waiting tickets across projects plus key figures.
    Everything is aggregated at query time, with no stored gadgets.

    Zwei Listen:
    - `action`: tickets that need MY interaction (the agent waits for me: plan approval, test
      acceptance, question or hold, error). "Mine" means I assigned the agent
      (`assigned_by_user_id`) OR the ticket is assigned to me as a person (`assignee_user_id`).
    - `assigned`: tickets assigned to me that are still open and NOT already in `action`.
    """
    from sqlalchemy import func, or_, select

    from ..models.notification import Notification
    from ..models.project import Project, ProjectMember
    from ..models.ticket import Issue, WorkflowStatus

    mine = or_(Issue.assigned_by_user_id == u.id, Issue.assignee_user_id == u.id)

    def _serialize(issue: Issue, proj: Project, cat) -> dict:
        return {
            "key": issue.key, "summary": issue.summary, "priority": issue.priority,
            "agent_status": issue.agent_status, "hold_reason": issue.hold_reason,
            "assigned_agent": issue.assigned_agent, "agent_working": issue.agent_working,
            "category": cat.value if hasattr(cat, "value") else str(cat),
            "updated_at": issue.updated_at,
            "project_id": proj.id, "project_key": proj.key, "project_name": proj.name,
        }

    base = (select(Issue, Project, WorkflowStatus.category)
            .join(Project, Project.id == Issue.project_id)
            .join(WorkflowStatus, WorkflowStatus.id == Issue.status_id)
            .where(Issue.archived.is_(False)))

    # Needs my interaction: the agent waits for me.
    action_rows = (await db.execute(
        base.where(mine, Issue.agent_status.in_(_WAIT_STATES))
        .order_by(Issue.updated_at.desc()))).all()
    action = [_serialize(i, p, c) for i, p, c in action_rows]
    action_keys = {a["key"] for a in action}

    # Assigned to me and still open (not finished), without the ones listed above.
    assigned_rows = (await db.execute(
        base.where(Issue.assignee_user_id == u.id, Issue.resolved_at.is_(None),
                   WorkflowStatus.category != "done")
        .order_by(Issue.updated_at.desc()))).all()
    assigned = [_serialize(i, p, c) for i, p, c in assigned_rows if i.key not in action_keys]

    # Eckdaten
    projects = (await db.execute(
        select(func.count()).select_from(ProjectMember)
        .where(ProjectMember.user_id == u.id))).scalar_one()
    working = (await db.execute(
        select(func.count()).select_from(Issue)
        .where(mine, Issue.agent_working.is_(True)))).scalar_one()
    unread = (await db.execute(
        select(func.count()).select_from(Notification)
        .where(or_(Notification.user_id == u.id, Notification.user_id.is_(None)),
               Notification.read_at.is_(None)))).scalar_one()
    since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=7)
    done_recent = (await db.execute(
        select(func.count()).select_from(Issue)
        .where(mine, Issue.resolved_at.isnot(None), Issue.resolved_at >= since))).scalar_one()

    return {
        "action": action, "assigned": assigned,
        "stats": {"projects": projects, "action": len(action), "assigned": len(assigned),
                  "working": working, "unread": unread, "done_7d": done_recent},
    }


@router.get("/me/flags")
async def my_flags(u: User = Depends(get_current_user)):
    out = {f: await get_user_flag(f, u.id) for f in USER_FLAGS}
    out.update({f: await get_flag(f) for f in GLOBAL_FLAGS})
    out.update(night_start_hour=u.night_start_hour, night_end_hour=u.night_end_hour,
               night_days=u.night_days, night_override=u.night_override, max_runners=u.max_runners,
               telegram_chat_id=u.telegram_chat_id, assistant_notify=u.assistant_notify,
               vault_memory_path=u.vault_memory_path, timezone=u.timezone)
    return out


# Per-user flag toggles: POST = on, DELETE = off
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
