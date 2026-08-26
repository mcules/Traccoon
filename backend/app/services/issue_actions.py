"""What can be done to a ticket, once, so that a bulk action cannot drift from a single one.

Every one of these actions existed already, each inside its own endpoint, together with its
rules: who may do it, what it forbids, what it drags along. A bulk action that repeated those
rules would be a second truth about the same matter, and the first ticket that behaves
differently in the list than in the drawer proves it. That is the same argument the board
reconciliation was built on: a rule that hangs off every one of its call sites individually is
not a rule.

So the endpoints in `api/issues.py` are the door and these functions are the matter. Each one
takes the ticket and the access of whoever is asking, checks the rights itself and raises
`Error` when it refuses. **None of them commits**: the caller decides whether a failed ticket
takes the others down with it. The single endpoints commit right away, the bulk one commits
per ticket so that number seven going wrong does not undo the first six.
"""
from __future__ import annotations

import datetime as dt

from fastapi import status
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..models.enums import Priority, ProjectRole, StatusCategory, TicketAgentStatus
from ..models.project import Project, ProjectMember
from ..models.ticket import Issue, WorkflowStatus
from ..models.user import User

# Distance between two neighbours in a column. Whole numbers with room in between, so that a
# single move does not have to renumber the whole column.
RANK_STEP = 1000


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def require_write(access) -> None:
    if not access.has_role(ProjectRole.member):
        raise Error(status.HTTP_403_FORBIDDEN, "err.write_rights_member_required",
                     "Write rights (member) are required")


def _require_maintainer(access) -> None:
    if not access.has_role(ProjectRole.maintainer):
        raise Error(status.HTTP_403_FORBIDDEN, "err.deleting_requires_maintainer",
                     "Deleting requires maintainer")


def _require_ai(access) -> None:
    if not access.ai_assign:
        raise Error(status.HTTP_403_FORBIDDEN, "err.ai_right_ai_assign_required",
                     "The AI right (ai_assign) is required")


# ── Status ──────────────────────────────────────────────────────────────────

async def _guard_done_transition(issue: Issue, target: WorkflowStatus, db: AsyncSession) -> None:
    """In the test environment flow, "done" may ONLY be set over POST /issues/{key}/complete
    (stop, merge, done). A direct move there would skip the merge and show a silently
    unmerged ticket as finished."""
    if target.category != StatusCategory.done:
        return
    if issue.agent_status not in (TicketAgentStatus.to_test, TicketAgentStatus.testing):
        return
    project = await db.get(Project, issue.project_id)
    if project is None or not project.testenv_enabled:
        return
    raise Error(status.HTTP_409_CONFLICT, "err.direct_jump_to_done",
                 'On to "done" only over "set to done", which stops the test environment and '
                 "merges the branch.")


async def move(db: AsyncSession, issue: Issue, access, *, status_id: int,
               position: int | None = None) -> None:
    """Into another column, at `position`, or at the end when none is named.

    The bulk path names none: dropping thirty tickets on the same position would make their
    order depend on the sequence they happen to be worked through in, and nobody chose that.
    """
    require_write(access)
    target = await db.get(WorkflowStatus, status_id)
    if target is None or target.project_id != issue.project_id:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.status_does_not_belong_project",
                     "The status does not belong to the project")
    await _guard_done_transition(issue, target, db)
    issue.status_id = status_id
    await db.flush()
    others = (await db.execute(
        select(Issue).where(Issue.project_id == issue.project_id, Issue.status_id == status_id,
                            Issue.id != issue.id).order_by(Issue.rank, Issue.number)
    )).scalars().all()
    pos = len(others) if position is None else max(0, min(position, len(others)))
    for i, it in enumerate(others[:pos] + [issue] + others[pos:]):
        it.rank = f"{(i + 1) * RANK_STEP:012d}"


# ── The plain fields ────────────────────────────────────────────────────────

async def set_priority(db: AsyncSession, issue: Issue, access, *, priority: Priority) -> None:
    require_write(access)
    issue.priority = priority


async def set_assignee(db: AsyncSession, issue: Issue, access, *, user_id: int) -> None:
    """Only an existing member of this project.

    Otherwise every member could set foreign or guessed user ids as the assignee and thereby
    reach past the invitation, and a 200 against a 404 would give away which ids exist.
    """
    require_write(access)
    target = (await db.execute(
        select(User).join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == issue.project_id, User.id == user_id)
    )).scalar_one_or_none()
    if target is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.person_not_found", "Person not found")
    issue.assignee_user_id = target.id


async def clear_assignee(db: AsyncSession, issue: Issue, access) -> None:
    require_write(access)
    issue.assignee_user_id = None


async def set_sprint(db: AsyncSession, issue: Issue, access, *, sprint_id: int | None) -> None:
    """Into a sprint, or back into the backlog (`None`).

    A sprint hangs off a board, and a board off a project, so the check goes over the board:
    without it a ticket could be moved into the sprint of a foreign project, which would put
    it on a board its people never chose to show it on.
    """
    require_write(access)
    if sprint_id is None:
        issue.sprint_id = None
        return
    from ..models.ticket import Board, Sprint
    ok = (await db.execute(
        select(Sprint.id).join(Board, Board.id == Sprint.board_id)
        .where(Sprint.id == sprint_id, Board.project_id == issue.project_id)
    )).scalar_one_or_none()
    if ok is None:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.sprint_does_not_belong_project",
                     "The sprint does not belong to the project")
    issue.sprint_id = sprint_id


# ── Archive and delete ──────────────────────────────────────────────────────

async def _stop_testenv(db: AsyncSession, issue: Issue, access) -> None:
    if issue.testenv_status:
        from .testenv import stop_testenv
        await stop_testenv(db, issue, access.project.key)


async def archive(db: AsyncSession, issue: Issue, access) -> None:
    require_write(access)
    from ..models.agents import Run
    issue.archived = True
    issue.archived_at = _now()
    # Clear away an orphaned test environment as well: container, volumes, port.
    await _stop_testenv(db, issue, access)
    # The agent runs follow the ticket, otherwise they stay in the live list for ever.
    await db.execute(sa_update(Run).where(Run.issue_id == issue.id, Run.archived.is_(False))
                     .values(archived=True, archived_at=issue.archived_at))


async def unarchive(db: AsyncSession, issue: Issue, access) -> None:
    require_write(access)
    from ..models.agents import Run
    issue.archived = False
    issue.archived_at = None
    await db.execute(sa_update(Run).where(Run.issue_id == issue.id, Run.archived.is_(True))
                     .values(archived=False, archived_at=None))


async def delete(db: AsyncSession, issue: Issue, access) -> None:
    _require_maintainer(access)
    await _stop_testenv(db, issue, access)
    await db.delete(issue)


# ── The agent ───────────────────────────────────────────────────────────────

async def assign_agent(db: AsyncSession, issue: Issue, access, *, agent: str) -> None:
    """Assign a role AND start the lifecycle: setting the field alone starts nothing.

    This one is the expensive action of the list, and the only one that commits in the middle:
    `start_lifecycle` reads the ticket back out of the database, so what it is to work on has
    to stand there.
    """
    _require_ai(access)
    issue.assigned_agent = agent
    issue.assigned_by_user_id = access.user.id
    issue.assigned_at = _now()
    if issue.agent_status is None:
        from .artifacts import set_ticket_status
        await set_ticket_status(db, issue, TicketAgentStatus.planning)
    await db.commit()
    from .lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, access.user.id, entry="exec" if issue.plan else "plan")
    await db.commit()
    from .events import emit
    await emit(db, "issue.assigned", project_id=issue.project_id, issue_id=issue.id,
               actor_id=access.user.id,
               payload={"issue": {"key": issue.key, "agent": agent}})
