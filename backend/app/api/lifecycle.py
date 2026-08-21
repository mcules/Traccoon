"""The familiar ticket actions, now as an adapter onto the process engine.

Planning, approving or rejecting a plan, approving a splitting, accepting, stopping: the
URLs are unchanged (frontend, Telegram bot and webhooks hang off them), but the flow behind
them sits in the process "AI ticket lifecycle" and is therefore designable per project.

Concretely that means: `approve-plan` looks for the waiting approval step of the instance and
decides it; which node comes afterwards is determined by the graph, not by this code.
"""
import datetime as dt

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Fehler
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
        raise Fehler(status.HTTP_403_FORBIDDEN, "err.ai_right_ai_assign_required",
                     "The AI right (ai_assign) is required")


def _who(access: Access) -> str:
    return access.user.display_name or access.user.username


async def _waiting_approval(db: AsyncSession, issue: Issue) -> tuple[int, WorkflowStepRun]:
    """Open approval in the lifecycle of the ticket, otherwise a 409 with a clear message."""
    inst = await live_instance(db, issue)
    if inst is None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_process_for_ticket",
                     "No process is running for this ticket, please assign an agent")
    step = (await db.execute(
        select(WorkflowStepRun).where(
            WorkflowStepRun.instance_id == inst.id,
            WorkflowStepRun.status == WorkflowStepStatus.waiting,
            WorkflowStepRun.node_type == WorkflowNodeType.approval,
        ).order_by(WorkflowStepRun.id.desc()))).scalars().first()
    if step is None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_approval_waiting_right_now",
                     "No approval is waiting right now")
    return inst.id, step


async def _decide(db: AsyncSession, issue: Issue, decision: str, reason: str | None = None):
    """Decide a waiting approval and advance the process."""
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
    """Start the planning (anew): aborts a running process and begins from the front."""
    issue, access = pair
    _require_ai(access)
    if issue.assigned_agent is None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_agent_assigned", "No agent assigned")
    from ..services.artifacts import set_ticket_status
    await set_ticket_status(db, issue, TicketAgentStatus.planning)
    await db.commit()
    inst = await start_lifecycle(db, issue, access.user.id, entry="plan", restart=True)
    if inst is None:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_published_lifecycle_process_project",
                     "No published lifecycle process for this project")
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
        raise Fehler(status.HTTP_409_CONFLICT, "err.ticket_not_plan_approval",
                     "The ticket is not in plan approval")
    if not issue.plan:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_plan_present", "No plan present")
    await add_system_comment(db, issue.id, f"✅ Plan freigegeben von {_who(access)}")
    await _decide(db, issue, "approved")
    return issue


@router.post("/issues/{key}/approve-split", response_model=list[IssueOut])
async def approve_split(pair: tuple[Issue, Access] = Depends(get_issue_access),
                        db: AsyncSession = Depends(get_session)):
    """Approve the splitting. The sub-tasks are created by the process (`split_tickets`)."""
    issue, access = pair
    _require_ai(access)
    if issue.hold_reason != HoldReason.plan_split:
        raise Fehler(status.HTTP_409_CONFLICT, "err.no_splitting_proposal_approve",
                     "No splitting proposal to approve")
    await add_system_comment(db, issue.id, f"✅ Aufteilung freigegeben von {_who(access)}")
    await _decide(db, issue, "approved")
    children = (await db.execute(
        select(Issue).where(Issue.parent_ticket_id == issue.id).order_by(Issue.split_order)
    )).scalars().all()
    if not children:
        raise Fehler(status.HTTP_409_CONFLICT, "err.process_created_no_sub_tasks_check_plan",
                     "The process created no sub-tasks, check the plan")
    return list(children)


@router.post("/issues/{key}/reject-plan", response_model=IssueOut)
async def reject_plan(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_ai(access)
    if issue.agent_status != TicketAgentStatus.plan_review:
        raise Fehler(status.HTTP_409_CONFLICT, "err.ticket_not_plan_approval",
                     "The ticket is not in plan approval")
    issue.plan = None
    await add_system_comment(db, issue.id, f"✖ Plan abgelehnt von {_who(access)}")
    await _decide(db, issue, "rejected")
    return issue


@router.post("/issues/{key}/complete", response_model=IssueOut)
async def complete(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    """Acceptance: decides the waiting acceptance approval of the process.

    What happens afterwards (clearing the test environment, merging respectively opening a PR,
    deploying) stands in the process "acceptance and delivery", where it can be adjusted.
    """
    issue, access = pair
    _require_ai(access)
    if issue.agent_status not in (TicketAgentStatus.to_test, TicketAgentStatus.testing,
                                  TicketAgentStatus.hold):
        raise Fehler(status.HTTP_409_CONFLICT, "err.ticket_not_ready_acceptance",
                     "The ticket is not ready for acceptance")
    await add_system_comment(db, issue.id, f"✅ Abnahme durch {_who(access)}")
    await _decide(db, issue, "approved")
    return issue


@router.post("/issues/{key}/stop", response_model=IssueOut)
async def stop_agent(pair: tuple[Issue, Access] = Depends(get_issue_access),
                     db: AsyncSession = Depends(get_session)):
    """Abort a running agent run (kill channel) and put the ticket on hold.

    The process stays and waits; a comment or "start planning" picks it up again.
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
