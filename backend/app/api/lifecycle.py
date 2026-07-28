"""Die vertrauten Ticket-Aktionen — jetzt als Adapter auf die Prozess-Engine.

Planen, Plan freigeben/ablehnen, Aufteilung freigeben, abnehmen, stoppen: die URLs sind
unverändert (Frontend, Telegram-Bot und Webhooks hängen daran), der Ablauf dahinter steckt
aber im Prozess „KI-Ticket-Lebenszyklus" und ist damit pro Projekt gestaltbar.

Konkret heißt das: `approve-plan` sucht den wartenden Genehmigungs-Schritt der Instanz und
entscheidet ihn — welcher Knoten danach kommt, bestimmt der Graph, nicht dieser Code.
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import (
    HoldReason, TicketAgentStatus, WorkflowNodeType, WorkflowStepStatus,
)
from ..models.ticket import Issue
from ..models.workflow import WorkflowStepRun
from ..schemas.issue import IssueOut
from ..services.comments import add_system_comment
from ..services.lifecycle_flow import live_instance, start_lifecycle
from .deps import Access
from .issues import get_issue_access

router = APIRouter(tags=["lifecycle"])


def _require_ai(access: Access) -> None:
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")


def _who(access: Access) -> str:
    return access.user.display_name or access.user.username


async def _waiting_approval(db: AsyncSession, issue: Issue) -> tuple[int, WorkflowStepRun]:
    """Offene Genehmigung im Lebenszyklus des Tickets — sonst 409 mit klarer Ansage."""
    inst = await live_instance(db, issue)
    if inst is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Für dieses Ticket läuft kein Prozess — bitte Agent zuweisen")
    step = (await db.execute(
        select(WorkflowStepRun).where(
            WorkflowStepRun.instance_id == inst.id,
            WorkflowStepRun.status == WorkflowStepStatus.waiting,
            WorkflowStepRun.node_type == WorkflowNodeType.approval,
        ).order_by(WorkflowStepRun.id.desc()))).scalars().first()
    if step is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Es wartet gerade keine Freigabe")
    return inst.id, step


async def _decide(db: AsyncSession, issue: Issue, decision: str, reason: str | None = None):
    """Wartende Genehmigung entscheiden und den Prozess weiterschalten."""
    from ..services import workflow_engine as engine
    from ..models.enums import WorkflowInstanceStatus, WorkflowTokenState
    from ..models.workflow import WorkflowInstance, WorkflowToken

    inst_id, step = await _waiting_approval(db, issue)
    step.status = WorkflowStepStatus.done
    step.decision = decision
    step.result = {"reason": reason} if reason else None
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    token = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == inst_id,
        WorkflowToken.state == WorkflowTokenState.waiting).with_for_update())).scalars().first()
    if token is not None:
        token.state = WorkflowTokenState.active
        token.waiting_for = None
    inst = await db.get(WorkflowInstance, inst_id)
    if inst is not None:
        inst.status = WorkflowInstanceStatus.running
    await db.commit()
    await engine.advance(inst_id)
    await db.refresh(issue)


@router.post("/issues/{key}/plan", response_model=IssueOut)
async def start_planning(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    """Planung (neu) starten — bricht einen laufenden Prozess ab und beginnt von vorn."""
    issue, access = pair
    _require_ai(access)
    if issue.assigned_agent is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Agent zugewiesen")
    from ..services.artifacts import set_ticket_status
    await set_ticket_status(db, issue, TicketAgentStatus.planning)
    await db.commit()
    inst = await start_lifecycle(db, issue, access.user.id, entry="plan", restart=True)
    if inst is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Kein veröffentlichter Lebenszyklus-Prozess für dieses Projekt")
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
    await add_system_comment(db, issue.id, f"✅ Plan freigegeben von {_who(access)}")
    await _decide(db, issue, "approved")
    return issue


@router.post("/issues/{key}/approve-split", response_model=list[IssueOut])
async def approve_split(pair: tuple[Issue, Access] = Depends(get_issue_access),
                        db: AsyncSession = Depends(get_session)):
    """Aufteilung freigeben. Die Teilaufgaben legt der Prozess an (`split_tickets`)."""
    issue, access = pair
    _require_ai(access)
    if issue.hold_reason != HoldReason.plan_split:
        raise HTTPException(status.HTTP_409_CONFLICT, "Kein Aufteilungs-Vorschlag zur Freigabe")
    await add_system_comment(db, issue.id, f"✅ Aufteilung freigegeben von {_who(access)}")
    await _decide(db, issue, "approved")
    children = (await db.execute(
        select(Issue).where(Issue.parent_ticket_id == issue.id).order_by(Issue.split_order)
    )).scalars().all()
    if not children:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Der Prozess hat keine Teilaufgaben angelegt — Plan prüfen")
    return list(children)


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
    await add_system_comment(db, issue.id, f"✖ Plan abgelehnt von {_who(access)}")
    await _decide(db, issue, "rejected")
    return issue


@router.post("/issues/{key}/complete", response_model=IssueOut)
async def complete(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    """Abnahme: entscheidet die wartende Abnahme-Genehmigung des Prozesses.

    Was danach passiert (Testumgebung abräumen, mergen bzw. PR öffnen, deployen), steht im
    Prozess „Abnahme & Auslieferung" — dort ist es auch anpassbar.
    """
    issue, access = pair
    _require_ai(access)
    if issue.agent_status not in (TicketAgentStatus.to_test, TicketAgentStatus.testing,
                                  TicketAgentStatus.hold):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket ist nicht zur Abnahme bereit")
    await add_system_comment(db, issue.id, f"✅ Abnahme durch {_who(access)}")
    await _decide(db, issue, "approved")
    return issue


@router.post("/issues/{key}/stop", response_model=IssueOut)
async def stop_agent(pair: tuple[Issue, Access] = Depends(get_issue_access),
                     db: AsyncSession = Depends(get_session)):
    """Laufenden Agenten-Lauf abbrechen (kill-Kanal) und Ticket auf hold setzen.

    Der Prozess bleibt bestehen und wartet — ein Kommentar oder „Planung starten" nimmt ihn
    wieder auf.
    """
    issue, access = pair
    _require_ai(access)
    from ..core.redis import publish_kill
    await publish_kill(issue.key)
    issue.agent_working = False
    from ..services.artifacts import set_ticket_status
    await set_ticket_status(db, issue, TicketAgentStatus.hold,
                            reason=HoldReason.interrupted)
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
