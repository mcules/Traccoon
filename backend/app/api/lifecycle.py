import datetime as dt
import json
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import HoldReason, TicketAgentStatus
from ..models.project import Project
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from ..schemas.issue import IssueOut
from ..services.comments import add_system_comment
from ..services.dispatcher import sync_board_status
from .deps import Access
from .issues import get_issue_access

router = APIRouter(tags=["lifecycle"])


def _require_ai(access: Access) -> None:
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")


def _who(access: Access) -> str:
    return access.user.display_name or access.user.username


@router.post("/issues/{key}/plan", response_model=IssueOut)
async def start_planning(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_ai(access)
    if issue.assigned_agent is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Agent zugewiesen")
    issue.agent_status = TicketAgentStatus.planning
    issue.hold_reason = None
    await sync_board_status(db, issue)
    await db.commit()
    await db.refresh(issue)
    return issue


@router.post("/issues/{key}/approve-plan", response_model=IssueOut)
async def approve_plan(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_ai(access)
    if issue.agent_status != TicketAgentStatus.plan_review:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket ist nicht in Plan-Freigabe")
    if not issue.plan:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Plan vorhanden")
    issue.agent_status = TicketAgentStatus.approved
    issue.hold_reason = None
    await add_system_comment(db, issue.id, f"✅ Plan freigegeben von {_who(access)}")
    await sync_board_status(db, issue)
    await db.commit()
    await db.refresh(issue)
    return issue


async def _new_child(db: AsyncSession, umbrella: Issue, project: Project, sub: dict,
                     order: int, exec_agent: str, by_user: int) -> Issue:
    t = (await db.execute(select(IssueType).where(IssueType.project_id == project.id)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == project.id)
                          .order_by(WorkflowStatus.order))).scalars().first()
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == project.id)
                                .with_for_update())).scalar_one()
    counter.last_number += 1
    n = counter.last_number
    child = Issue(
        project_id=project.id, number=n, key=f"{project.key}-{n}", type_id=t.id, status_id=s.id,
        summary=(sub.get("summary") or f"Teil {order + 1}")[:500], description=sub.get("description", ""),
        reporter_id=by_user, rank=f"{n:08d}", parent_ticket_id=umbrella.id, split_order=order,
        plan=sub.get("plan", ""), assigned_agent=exec_agent, assigned_by_user_id=by_user,
        assigned_at=dt.datetime.now(tz=dt.timezone.utc),
        # Kind 0 startet sofort (approved), Rest geparkt bis zur Reihe
        agent_status=(TicketAgentStatus.approved if order == 0 else None),
    )
    db.add(child)
    return child


@router.post("/issues/{key}/approve-split", response_model=list[IssueOut])
async def approve_split(pair: tuple[Issue, Access] = Depends(get_issue_access),
                        db: AsyncSession = Depends(get_session)):
    umbrella, access = pair
    _require_ai(access)
    if umbrella.hold_reason != HoldReason.plan_split:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Split-Vorschlag zur Freigabe")
    m = re.search(r"<subtickets>\s*(\[.*?\])\s*</subtickets>", umbrella.plan or "", re.DOTALL)
    if not m:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein <subtickets>-Block im Plan")
    try:
        subs = json.loads(m.group(1))
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "subtickets-JSON ungültig")
    project = await db.get(Project, umbrella.project_id)
    exec_agent = umbrella.exec_agent or project.exec_agent or "developer"
    children = []
    for i, sub in enumerate(subs):
        children.append(await _new_child(db, umbrella, project, sub, i, exec_agent, access.user.id))
    # Umbrella wartet auf die Kinder (nicht selbst ausgeführt)
    umbrella.agent_status = None
    umbrella.hold_reason = None
    await add_system_comment(db, umbrella.id,
                             f"✅ Aufteilung freigegeben von {_who(access)} — {len(children)} Teilaufgaben")
    for c in children:  # Teil 1 (approved) → „In Arbeit", die geparkten bleiben unangetastet
        await sync_board_status(db, c)
    await db.commit()
    for c in children:
        await db.refresh(c)
    return children


@router.post("/issues/{key}/reject-plan", response_model=IssueOut)
async def reject_plan(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_ai(access)
    if issue.agent_status != TicketAgentStatus.plan_review:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket ist nicht in Plan-Freigabe")
    issue.plan = None
    issue.agent_status = None  # geparkt; /plan startet neu
    issue.hold_reason = None
    await add_system_comment(db, issue.id, f"✖ Plan abgelehnt von {_who(access)}")
    await db.commit()
    await db.refresh(issue)
    return issue


@router.post("/issues/{key}/complete", response_model=IssueOut)
async def complete(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_ai(access)
    # Abnahme auch aus hold (z. B. Review-Befunde offen) — der Mensch hat die Hoheit.
    if issue.agent_status not in (TicketAgentStatus.to_test, TicketAgentStatus.testing,
                                  TicketAgentStatus.hold):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket ist nicht zur Abnahme bereit")
    issue.agent_status = TicketAgentStatus.done
    issue.resolved_at = dt.datetime.now(tz=dt.timezone.utc)
    issue.hold_reason = None
    await sync_board_status(db, issue)
    await db.commit()
    await db.refresh(issue)
    # Testenv abbauen (falls aktiv)
    from ..models.project import Project
    from ..services.testenv import stop_testenv
    project = await db.get(Project, issue.project_id)
    if issue.testenv_status:
        await stop_testenv(db, issue, project.key)
    # Abnahme → Worker: Ticket-Branch nach main mergen (+ ggf. Auto-Deploy). Fire-and-forget.
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "accept", "task_id": f"accept-{issue.key}",
                        "issue_id": issue.id, "project_id": issue.project_id})
    return issue


@router.post("/issues/{key}/stop", response_model=IssueOut)
async def stop_agent(pair: tuple[Issue, Access] = Depends(get_issue_access),
                     db: AsyncSession = Depends(get_session)):
    """Laufenden Agenten-Lauf abbrechen (kill-Kanal) und Ticket auf hold setzen."""
    issue, access = pair
    _require_ai(access)
    from ..core.redis import publish_kill
    await publish_kill(issue.key)
    issue.agent_working = False
    issue.agent_status = TicketAgentStatus.hold
    issue.hold_reason = HoldReason.interrupted
    await sync_board_status(db, issue)
    await db.commit()
    await db.refresh(issue)
    return issue


@router.post("/issues/{key}/testenv/start")
async def testenv_start(pair: tuple[Issue, Access] = Depends(get_issue_access),
                        db: AsyncSession = Depends(get_session)):
    issue, access = pair
    _require_ai(access)
    from ..models.project import Project
    from ..services.testenv import start_testenv
    project = await db.get(Project, issue.project_id)
    return await start_testenv(db, issue, project.key)


@router.post("/issues/{key}/testenv/stop", status_code=204)
async def testenv_stop(pair: tuple[Issue, Access] = Depends(get_issue_access),
                       db: AsyncSession = Depends(get_session)):
    issue, access = pair
    _require_ai(access)
    from ..models.project import Project
    from ..services.testenv import stop_testenv
    project = await db.get(Project, issue.project_id)
    await stop_testenv(db, issue, project.key)
