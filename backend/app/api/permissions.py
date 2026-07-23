import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import PurchaseStatus, TicketAgentStatus  # noqa: F401
from ..models.ops import PermAction, PermGrant, Permission, PermRequest
from ..models.ticket import Blocker, Comment, Issue
from .deps import Access, get_current_user, get_project_access
from .issues import get_issue_access

router = APIRouter(tags=["permissions"])


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


class DecisionIn(BaseModel):
    decision: str  # once | always | never


class AnswerIn(BaseModel):
    answer: str


@router.get("/projects/{project_id}/permission-requests")
async def list_perm_requests(access: Access = Depends(get_project_access),
                             db: AsyncSession = Depends(get_session)):
    rows = (
        await db.execute(
            select(PermRequest, Issue).join(Issue, Issue.id == PermRequest.issue_id)
            .where(Issue.project_id == access.project.id, PermRequest.status == "pending")
            .order_by(PermRequest.created_at)
        )
    ).all()
    return [{"id": pr.id, "issue_key": iss.key, "tool": pr.tool, "resource": pr.resource,
             "created_at": pr.created_at} for pr, iss in rows]


@router.post("/permission-requests/{req_id}/decide")
async def decide(req_id: int, data: DecisionIn, user=Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    pr = await db.get(PermRequest, req_id)
    if pr is None or pr.status != "pending":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anfrage nicht gefunden")
    issue = await db.get(Issue, pr.issue_id)
    # Zugriff prüfen (Mitglied + ai_assign)
    from ..models.project import Project
    project = await db.get(Project, issue.project_id)
    from .deps import build_access
    access = await build_access(project, user, db)
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht erforderlich")

    dec = data.decision
    if dec == "once":
        db.add(PermGrant(issue_id=pr.issue_id, tool=pr.tool, resource=pr.resource))
    elif dec == "always":
        db.add(Permission(project_id=issue.project_id, tool=pr.tool, resource="*", action=PermAction.allow))
    elif dec == "never":
        db.add(Permission(project_id=issue.project_id, tool=pr.tool, resource="*", action=PermAction.deny))
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision muss once|always|never sein")

    pr.status = "decided"
    pr.decision = dec
    pr.decided_at = _now()
    pr.decided_by = user.id
    # Resume: zurück in Ausführung (nächster Dispatcher-Tick nimmt es als Continuation)
    if issue.agent_status == TicketAgentStatus.hold:
        issue.agent_status = TicketAgentStatus.approved
        issue.hold_reason = None
        issue.continuation_count += 1
        from ..services.dispatcher import sync_board_status
        await sync_board_status(db, issue)   # Resume → „In Arbeit"
    await db.commit()
    return {"ok": True, "resumed": dec != "never"}


@router.post("/issues/{key}/blocker/answer")
async def answer_blocker(data: AnswerIn, pair: tuple[Issue, Access] = Depends(get_issue_access),
                         db: AsyncSession = Depends(get_session)):
    issue, access = pair
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht erforderlich")
    blocker = (
        await db.execute(
            select(Blocker).where(Blocker.issue_id == issue.id, Blocker.answer.is_(None))
            .order_by(Blocker.id.desc())
        )
    ).scalars().first()
    if blocker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keine offene Rückfrage")
    blocker.answer = data.answer
    blocker.answered_at = _now()
    blocker.answered_by = access.user.id
    # Antwort als User-Kommentar (fließt in den nächsten Lauf ein)
    db.add(Comment(issue_id=issue.id, author_id=access.user.id,
                   author_label=access.user.display_name or access.user.username,
                   body=data.answer, kind="agent"))
    # Resume
    issue.agent_status = TicketAgentStatus.approved
    issue.hold_reason = None
    issue.continuation_count += 1
    from ..services.dispatcher import sync_board_status
    await sync_board_status(db, issue)   # Rückfrage beantwortet → „In Arbeit"
    await db.commit()
    return {"ok": True}
