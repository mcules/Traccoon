import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..db import get_session
from ..models.enums import ProjectRole, SprintState
from ..models.project import ProjectMember
from ..models.ticket import (
    Board, BoardColumn, Issue, IssueType, Sprint, Tag, WorkflowStatus,
)
from ..models.user import User
from ..schemas.config import (
    BoardOut, ColumnOut, IssueTypeIn, IssueTypeOut, MemberLite, ProjectMeta,
    SprintIn, SprintOut, StatusIn, StatusOut, TagOut,
)
from .deps import Access, get_current_user, get_project_access, require_role

router = APIRouter(tags=["config"])


@router.get("/projects/{project_id}/meta", response_model=ProjectMeta)
async def project_meta(
    access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session)
):
    pid = access.project.id
    types = (
        await db.execute(select(IssueType).where(IssueType.project_id == pid).order_by(IssueType.order))
    ).scalars().all()
    statuses = (
        await db.execute(
            select(WorkflowStatus).where(WorkflowStatus.project_id == pid).order_by(WorkflowStatus.order)
        )
    ).scalars().all()
    boards = (await db.execute(select(Board).where(Board.project_id == pid))).scalars().all()
    board_out = []
    for b in boards:
        cols = (
            await db.execute(
                select(BoardColumn).where(BoardColumn.board_id == b.id).order_by(BoardColumn.order)
            )
        ).scalars().all()
        board_out.append(BoardOut(
            id=b.id, name=b.name,
            columns=[ColumnOut(status_id=c.status_id, order=c.order) for c in cols],
        ))
    board_ids = [b.id for b in boards]
    sprints = []
    if board_ids:
        sprints = (
            await db.execute(select(Sprint).where(Sprint.board_id.in_(board_ids)).order_by(Sprint.id))
        ).scalars().all()
    member_rows = (
        await db.execute(
            select(ProjectMember, User).join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == pid)
        )
    ).all()
    members = [
        MemberLite(user_id=u.id, username=u.username, display_name=u.display_name,
                   role=m.role.value, ai_assign=m.ai_assign, status=u.status.value)
        for m, u in member_rows
    ]
    return ProjectMeta(
        types=list(types), statuses=list(statuses), boards=board_out, sprints=list(sprints),
        members=members, my_ai_assign=access.ai_assign, my_role=access.role.value,
    )


# ---------- Issue-Typen ----------

@router.post("/projects/{project_id}/types", response_model=IssueTypeOut, status_code=201)
async def add_type(
    data: IssueTypeIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    last = (
        await db.execute(
            select(IssueType).where(IssueType.project_id == access.project.id).order_by(IssueType.order.desc())
        )
    ).scalars().first()
    order = (last.order + 1) if last else 0
    t = IssueType(project_id=access.project.id, name=data.name, icon=data.icon,
                  color=data.color, category=data.category, order=order)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@router.delete("/projects/{project_id}/types/{type_id}", status_code=204)
async def delete_type(
    type_id: int,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    t = await db.get(IssueType, type_id)
    if t is None or t.project_id != access.project.id:
        raise Fehler(404, "err.type_not_found", "Type not found")
    await db.delete(t)
    await db.commit()


# ---------- Status ----------

@router.post("/projects/{project_id}/statuses", response_model=StatusOut, status_code=201)
async def add_status(
    data: StatusIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    last = (
        await db.execute(
            select(WorkflowStatus).where(WorkflowStatus.project_id == access.project.id)
            .order_by(WorkflowStatus.order.desc())
        )
    ).scalars().first()
    order = (last.order + 1) if last else 0
    s = WorkflowStatus(project_id=access.project.id, name=data.name, category=data.category, order=order)
    db.add(s)
    await db.flush()
    # Take it into all boards as a column
    boards = (await db.execute(select(Board).where(Board.project_id == access.project.id))).scalars().all()
    for b in boards:
        db.add(BoardColumn(board_id=b.id, status_id=s.id, order=order))
    await db.commit()
    await db.refresh(s)
    return s


@router.put("/projects/{project_id}/statuses/{status_id}", response_model=StatusOut)
async def update_status(
    status_id: int, data: StatusIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Rename a status or change its category."""
    s = await db.get(WorkflowStatus, status_id)
    if s is None or s.project_id != access.project.id:
        raise Fehler(404, "err.status_not_found", "Status not found")
    s.name = data.name
    s.category = data.category
    await db.commit()
    await db.refresh(s)
    return s


class ReorderIn(BaseModel):
    ordered_ids: list[int]


@router.post("/projects/{project_id}/statuses/reorder", response_model=list[StatusOut])
async def reorder_statuses(
    data: ReorderIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Set the column order. Acts on the status order AND the board columns."""
    rows = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == access.project.id))).scalars().all()
    by_id = {s.id: s for s in rows}
    if set(data.ordered_ids) != set(by_id):
        raise Fehler(400, "err.list_needs_all_statuses",
                     "The list has to contain exactly all statuses of the project")
    boards = (await db.execute(select(Board).where(Board.project_id == access.project.id))).scalars().all()
    cols = (await db.execute(select(BoardColumn).where(
        BoardColumn.board_id.in_([b.id for b in boards])))).scalars().all() if boards else []
    for i, sid in enumerate(data.ordered_ids):
        by_id[sid].order = i
        for c in cols:
            if c.status_id == sid:
                c.order = i
    await db.commit()
    return sorted(by_id.values(), key=lambda s: s.order)


@router.delete("/projects/{project_id}/statuses/{status_id}", status_code=204)
async def delete_status(
    status_id: int,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    s = await db.get(WorkflowStatus, status_id)
    if s is None or s.project_id != access.project.id:
        raise Fehler(404, "err.status_not_found", "Status not found")
    # Do not delete the last status; otherwise the board would have no column and new
    # tickets would have no target status.
    count = (await db.execute(select(func.count(WorkflowStatus.id)).where(
        WorkflowStatus.project_id == access.project.id))).scalar() or 0
    if count <= 1:
        raise Fehler(409, "err.last_status_cannot_deleted", "The last status cannot be deleted")
    # Tickets in this status block the deletion (an FK without cascade), with a clear message.
    n_issues = (await db.execute(select(func.count(Issue.id)).where(
        Issue.status_id == status_id))).scalar() or 0
    if n_issues:
        raise Fehler(409, "err.status_still_has_tickets",
                     "{anzahl} ticket(s) stand in this status, move them first", anzahl=n_issues)
    await db.delete(s)
    await db.commit()


# ---------- Sprints ----------

@router.post("/projects/{project_id}/sprints", response_model=SprintOut, status_code=201)
async def add_sprint(
    data: SprintIn,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    board = (await db.execute(select(Board).where(Board.project_id == access.project.id))).scalars().first()
    if board is None:
        raise Fehler(400, "err.no_board", "No board")
    sp = Sprint(board_id=board.id, name=data.name, goal=data.goal, state=data.state,
                start_date=data.start_date, end_date=data.end_date)
    db.add(sp)
    await db.commit()
    await db.refresh(sp)
    return sp


@router.post("/projects/{project_id}/sprints/{sprint_id}/start", response_model=SprintOut)
async def start_sprint(
    sprint_id: int,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    """Start a sprint. Only one is active per board; otherwise "the current sprint" is ambiguous."""
    sp = await _sprint_of_project(db, sprint_id, access.project.id)
    if sp.state == SprintState.closed:
        raise Fehler(409, "err.finished_sprint_cannot_started",
                     "A finished sprint cannot be started")
    laufend = (await db.execute(select(Sprint).where(
        Sprint.board_id == sp.board_id, Sprint.state == SprintState.active,
        Sprint.id != sp.id))).scalars().first()
    if laufend is not None:
        raise Fehler(409, "err.sprint_still_running_close_first",
                     'The sprint "{name}" is still running, close it first', name=laufend.name)
    sp.state = SprintState.active
    sp.start_date = sp.start_date or dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    await db.refresh(sp)
    return sp


@router.post("/projects/{project_id}/sprints/{sprint_id}/complete", response_model=SprintOut)
async def complete_sprint(
    sprint_id: int,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    """Finish a sprint. Unfinished tickets wander back into the backlog."""
    sp = await _sprint_of_project(db, sprint_id, access.project.id)
    offen = (await db.execute(select(Issue).where(
        Issue.sprint_id == sp.id, Issue.resolved_at.is_(None)))).scalars().all()
    for issue in offen:
        issue.sprint_id = None
    sp.state = SprintState.closed
    sp.end_date = sp.end_date or dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    await db.refresh(sp)
    return sp


async def _sprint_of_project(db: AsyncSession, sprint_id: int, project_id: int) -> Sprint:
    sp = await db.get(Sprint, sprint_id)
    if sp is None:
        raise Fehler(404, "err.sprint_not_found", "Sprint not found")
    board = await db.get(Board, sp.board_id)
    if board is None or board.project_id != project_id:
        raise Fehler(404, "err.sprint_not_found", "Sprint not found")  # no hint at foreign projects
    return sp


@router.delete("/projects/{project_id}/sprints/{sprint_id}", status_code=204)
async def delete_sprint(
    sprint_id: int,
    access: Access = Depends(require_role(ProjectRole.member)),
    db: AsyncSession = Depends(get_session),
):
    sp = await _sprint_of_project(db, sprint_id, access.project.id)
    await db.delete(sp)
    await db.commit()


# ---------- Tags (global) ----------

@router.get("/tags", response_model=list[TagOut])
async def list_tags(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Tag).order_by(Tag.name))).scalars().all()
    return list(rows)
