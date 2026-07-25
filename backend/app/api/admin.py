"""Admin: Wartungsprojekt-Einstellung + Update-Flow (drain → Self-Deploy via Sidecar)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import get_flag, set_flag
from ..db import get_session
from ..models.project import Project
from ..models.ticket import Issue
from ..models.user import User
from ..services.appsettings import get_setting, set_setting
from ..services.mail import get_mail_config, set_mail_config
from .deps import get_current_user, require_admin

router = APIRouter(tags=["admin"])

MAINT_KEY = "maintenance_project_id"


async def _running_agents(db: AsyncSession) -> int:
    return (await db.execute(
        select(func.count()).select_from(Issue).where(Issue.agent_working.is_(True)))).scalar() or 0


async def _status(db: AsyncSession) -> dict:
    mp = await get_setting(db, MAINT_KEY, "")
    mp_id = int(mp) if mp.isdigit() else None
    mp_key = None
    if mp_id:
        proj = await db.get(Project, mp_id)
        mp_key = proj.key if proj else None
    return {
        "running_agents": await _running_agents(db),
        "update_pending": await get_flag("update_pending"),
        "update_in_progress": await get_flag("update_in_progress"),
        "last_update_completed_at": await get_setting(db, "last_update_completed_at", "") or None,
        "maintenance_project_id": mp_id,
        "maintenance_project_key": mp_key,
    }


@router.get("/admin/status")
async def admin_status(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # Für das Kopfzeilen-Icon (Agenten-Zähler) — jeder eingeloggte Nutzer.
    return await _status(db)


class MaintenanceIn(BaseModel):
    project_id: int | None = None


@router.put("/admin/maintenance")
async def set_maintenance(data: MaintenanceIn, _: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_session)):
    if data.project_id is not None:
        proj = await db.get(Project, data.project_id)
        if proj is None:
            raise HTTPException(404, "Projekt nicht gefunden")
    await set_setting(db, MAINT_KEY, str(data.project_id) if data.project_id else "")
    return await _status(db)


@router.post("/admin/update")
async def request_update(user: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Update einreihen: keine neuen Agenten mehr; wenn der letzte fertig ist, self-deployt
    der Dispatcher das Wartungsprojekt über den Deployer-Sidecar."""
    mp = await get_setting(db, MAINT_KEY, "")
    if not mp.isdigit():
        raise HTTPException(409, "Kein Wartungsprojekt gesetzt (Admin → Wartung).")
    if await get_flag("update_in_progress"):
        raise HTTPException(409, "Ein Update läuft bereits.")
    await set_flag("update_pending", True)
    return await _status(db)


@router.post("/admin/update/cancel")
async def cancel_update(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    await set_flag("update_pending", False)
    await set_flag("update_in_progress", False)
    return await _status(db)


class RunRetentionIn(BaseModel):
    days: int = Field(ge=0, le=3650)  # 0 = nie löschen


@router.get("/admin/run-retention")
async def get_run_retention(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Aufbewahrung archivierter Agentenläufe in Tagen (TRA-29)."""
    from ..services.scheduler import RUN_RETENTION_DEFAULT, RUN_RETENTION_KEY
    raw = await get_setting(db, RUN_RETENTION_KEY, str(RUN_RETENTION_DEFAULT))
    return {"days": int(raw) if raw.isdigit() else RUN_RETENTION_DEFAULT}


@router.put("/admin/run-retention")
async def put_run_retention(
    data: RunRetentionIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)
):
    from ..services.scheduler import RUN_RETENTION_KEY
    await set_setting(db, RUN_RETENTION_KEY, str(data.days))
    return {"days": data.days}


class SmtpConfigIn(BaseModel):
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = Field(default=None, max_length=255)
    smtp_use_tls: bool | None = None


@router.get("/admin/mail-config")
async def get_mail_settings(_: User = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """SMTP-Konfiguration für den Mailversand (z. B. Projekt-Einladungen)."""
    cfg = await get_mail_config(db)
    cfg["smtp_password_set"] = bool(cfg.pop("smtp_password", ""))
    return cfg


@router.put("/admin/mail-config")
async def put_mail_settings(
    data: SmtpConfigIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_session)
):
    await set_mail_config(db, data.model_dump(exclude_unset=True))
    cfg = await get_mail_config(db)
    cfg["smtp_password_set"] = bool(cfg.pop("smtp_password", ""))
    return cfg
