"""Projekt-Einladungen per E-Mail: Einladen, Vorschau (öffentlich), Annehmen."""
from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models.enums import ProjectRole
from ..models.invitation import InvitationStatus, ProjectInvitation
from ..models.project import Project, ProjectMember, default_ai_assign
from ..models.user import User
from ..schemas.project import (
    InvitationCreate, InvitationOut, InvitationPreview, MemberOut,
)
from ..services.mail import send_mail
from .deps import ROLE_RANK, Access, get_current_user, require_role
from .projects import project_out

router = APIRouter(tags=["invitations"])

INVITE_TTL_DAYS = 7


def _base_url() -> str:
    return (settings.app_base_url or "").rstrip("/")


def _invite_link(token: str) -> str:
    base = _base_url()
    path = f"/accept-invite?token={token}"
    return f"{base}{path}" if base else path


async def _send_invite_mail(db: AsyncSession, inv: ProjectInvitation, project: Project) -> None:
    link = _invite_link(inv.token)
    subject = f"Einladung zum Projekt „{project.name}“ auf Traccoon"
    text = (
        f"Du wurdest zum Projekt „{project.name}“ ({project.key}) auf Traccoon eingeladen.\n\n"
        f"Klicke auf folgenden Link, um beizutreten (anmelden oder registrieren):\n{link}\n\n"
        f"Der Link ist {INVITE_TTL_DAYS} Tage gültig."
    )
    html = (
        f"<p>Du wurdest zum Projekt <b>{project.name}</b> ({project.key}) auf "
        f"🦝 Traccoon eingeladen.</p>"
        f"<p><a href=\"{link}\">Jetzt beitreten</a> (anmelden oder registrieren).</p>"
        f"<p style=\"color:#888;font-size:12px\">Der Link ist {INVITE_TTL_DAYS} Tage gültig.</p>"
    )
    await send_mail(db, inv.email, subject, html, text)


def _inv_out(inv: ProjectInvitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id, project_id=inv.project_id, email=inv.email, role=inv.role,
        status=inv.status, created_at=inv.created_at, expires_at=inv.expires_at,
    )


# ---------- Projektbezogen (Maintainer+) ----------

@router.get("/projects/{project_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    rows = (
        await db.execute(
            select(ProjectInvitation)
            .where(ProjectInvitation.project_id == access.project.id)
            .order_by(ProjectInvitation.id.desc())
        )
    ).scalars().all()
    return [_inv_out(i) for i in rows]


@router.post("/projects/{project_id}/invitations", status_code=status.HTTP_201_CREATED)
async def invite_member(
    data: InvitationCreate,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Lädt einen User per E-Mail ein. Existiert der User bereits (z. B. aus einem
    anderen Projekt bekannt), wird er DIREKT zugeordnet — ohne Mail-Umweg."""
    if ROLE_RANK[data.role] > ROLE_RANK[access.role]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Kann keine Rolle vergeben, die höher als die eigene ist",
        )
    email = data.email.strip().lower()
    project = access.project

    target = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if target is not None:
        dup = (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id, ProjectMember.user_id == target.id
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Bereits Mitglied")
        m = ProjectMember(
            project_id=project.id, user_id=target.id, role=data.role,
            ai_assign=default_ai_assign(data.role),
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return {
            "status": "added",
            "member": MemberOut(id=m.id, user_id=target.id, username=target.username,
                                display_name=target.display_name, role=m.role, ai_assign=m.ai_assign),
        }

    # Kein bestehender User → Einladung per Mail. Bestehende pending-Einladung erneuern.
    existing = (
        await db.execute(
            select(ProjectInvitation).where(
                ProjectInvitation.project_id == project.id,
                ProjectInvitation.email == email,
                ProjectInvitation.status == InvitationStatus.pending,
            )
        )
    ).scalar_one_or_none()
    now = dt.datetime.now(tz=dt.timezone.utc)
    if existing is not None:
        existing.role = data.role
        existing.token = secrets.token_urlsafe(32)
        existing.expires_at = now + dt.timedelta(days=INVITE_TTL_DAYS)
        existing.invited_by_user_id = access.user.id
        inv = existing
    else:
        inv = ProjectInvitation(
            project_id=project.id, email=email, role=data.role,
            token=secrets.token_urlsafe(32), invited_by_user_id=access.user.id,
            status=InvitationStatus.pending, expires_at=now + dt.timedelta(days=INVITE_TTL_DAYS),
        )
        db.add(inv)
    await db.commit()
    await db.refresh(inv)
    await _send_invite_mail(db, inv, project)
    return {"status": "invited", "invitation": _inv_out(inv)}


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: int,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    inv = await db.get(ProjectInvitation, invitation_id)
    if inv is None or inv.project_id != access.project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Einladung nicht gefunden")
    inv.status = InvitationStatus.revoked
    await db.commit()


# ---------- Öffentlich (Token-basiert, ohne Projekt-Zugriff) ----------

async def _load_valid(db: AsyncSession, token: str) -> tuple[ProjectInvitation | None, Project | None, str | None]:
    inv = (await db.execute(select(ProjectInvitation).where(ProjectInvitation.token == token))).scalar_one_or_none()
    if inv is None:
        return None, None, "Einladung nicht gefunden"
    project = await db.get(Project, inv.project_id)
    if project is None:
        return inv, None, "Projekt nicht gefunden"
    if inv.status != InvitationStatus.pending:
        return inv, project, "Einladung bereits verwendet oder widerrufen"
    if inv.expires_at and inv.expires_at < dt.datetime.now(tz=dt.timezone.utc):
        return inv, project, "Einladung abgelaufen"
    return inv, project, None


@router.get("/invitations/by-token/{token}", response_model=InvitationPreview)
async def preview_invitation(token: str, db: AsyncSession = Depends(get_session)):
    inv, project, reason = await _load_valid(db, token)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Einladung nicht gefunden")
    return InvitationPreview(
        project_key=project.key if project else "?",
        project_name=project.name if project else "?",
        email=inv.email, role=inv.role, valid=reason is None, reason=reason,
    )


@router.post("/invitations/by-token/{token}/accept")
async def accept_invitation(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    inv, project, reason = await _load_valid(db, token)
    if inv is None or project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Einladung nicht gefunden")
    if reason is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    if inv.email != user.email.lower():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Diese Einladung ist an eine andere E-Mail-Adresse gerichtet",
        )
    dup = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if dup is None:
        db.add(ProjectMember(
            project_id=project.id, user_id=user.id, role=inv.role,
            ai_assign=default_ai_assign(inv.role),
        ))
    inv.status = InvitationStatus.accepted
    inv.accepted_user_id = user.id
    inv.accepted_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()

    access = Access(user, project, inv.role, default_ai_assign(inv.role), True, dt.datetime.now(tz=dt.timezone.utc))
    return project_out(project, access)
