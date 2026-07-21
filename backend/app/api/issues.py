import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import ProjectRole, TicketAgentStatus
from ..models.project import Project
from ..models.ticket import (
    Comment, Issue, IssueCounter, IssueTag, IssueType, Tag, WorkflowStatus,
)
from ..models.user import User
from ..schemas.issue import (
    AssignAgentIn, CommentCreate, CommentOut, IssueCreate, IssueOut, IssueUpdate,
    MoveIn, TagIn,
)
from .deps import Access, build_access, get_current_user, get_project_access, require_role

router = APIRouter(tags=["issues"])


async def get_issue_access(
    key: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> tuple[Issue, Access]:
    issue = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    project = await db.get(Project, issue.project_id)
    access = await build_access(project, user, db)  # 404 bei fremdem Projekt
    return issue, access


def _require_write(access: Access) -> None:
    if not access.has_role(ProjectRole.member):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Schreibrecht (member) erforderlich")


# ---------- Liste / Anlegen ----------

@router.get("/projects/{project_id}/issues", response_model=list[IssueOut])
async def list_issues(
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
    sprint_id: int | None = None,
    assignee_user_id: int | None = None,
    archived: bool = False,
):
    q = select(Issue).where(Issue.project_id == access.project.id, Issue.archived == archived)
    if sprint_id is not None:
        q = q.where(Issue.sprint_id == sprint_id)
    if assignee_user_id is not None:
        q = q.where(Issue.assignee_user_id == assignee_user_id)
    q = q.order_by(Issue.rank, Issue.number)
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/issues", response_model=IssueOut,
             status_code=status.HTTP_201_CREATED)
async def create_issue(
    data: IssueCreate,
    access: Access = Depends(get_project_access),
    db: AsyncSession = Depends(get_session),
):
    _require_write(access)
    project = access.project

    type_id = data.type_id
    if type_id is None:
        t = (
            await db.execute(
                select(IssueType).where(IssueType.project_id == project.id).order_by(IssueType.order)
            )
        ).scalars().first()
        if t is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Projekt hat keine Issue-Typen")
        type_id = t.id

    status_id = data.status_id
    if status_id is None:
        s = (
            await db.execute(
                select(WorkflowStatus)
                .where(WorkflowStatus.project_id == project.id)
                .order_by(WorkflowStatus.order)
            )
        ).scalars().first()
        if s is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Projekt hat keine Status")
        status_id = s.id

    # Race-sichere Key-Vergabe über Counter-Row-Lock.
    counter = (
        await db.execute(
            select(IssueCounter).where(IssueCounter.project_id == project.id).with_for_update()
        )
    ).scalar_one()
    counter.last_number += 1
    number = counter.last_number

    issue = Issue(
        project_id=project.id, number=number, key=f"{project.key}-{number}",
        type_id=type_id, status_id=status_id, priority=data.priority,
        summary=data.summary, description=data.description, reporter_id=access.user.id,
        assignee_user_id=data.assignee_user_id, parent_id=data.parent_id,
        sprint_id=data.sprint_id, story_points=data.story_points, rank=f"{number:08d}",
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Einzel-Ticket ----------

@router.get("/issues/{key}", response_model=IssueOut)
async def get_issue(pair: tuple[Issue, Access] = Depends(get_issue_access)):
    return pair[0]


@router.put("/issues/{key}", response_model=IssueOut)
async def update_issue(
    data: IssueUpdate,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(issue, field, value)
    await db.commit()
    await db.refresh(issue)
    return issue


@router.delete("/issues/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_issue(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    if not access.has_role(ProjectRole.maintainer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Löschen erfordert maintainer")
    await db.delete(issue)
    await db.commit()


@router.post("/issues/{key}/archive", response_model=IssueOut)
async def archive_issue(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    issue.archived = True
    issue.archived_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    await db.refresh(issue)
    return issue


@router.post("/issues/{key}/unarchive", response_model=IssueOut)
async def unarchive_issue(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    issue.archived = False
    issue.archived_at = None
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Agent-Zuweisung (Kern-Feature, nur mit KI-Recht) ----------

@router.post("/issues/{key}/assign-agent", response_model=IssueOut)
async def assign_agent(
    data: AssignAgentIn,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")
    issue.assigned_agent = data.agent
    issue.assigned_by_user_id = access.user.id
    issue.assigned_at = dt.datetime.now(tz=dt.timezone.utc)
    # Zuweisung startet die Planung: bei PM plant/orchestriert der PM, bei
    # Direktzuweisung plant der plan_agent, danach führt der zugewiesene Agent aus.
    if issue.agent_status is None:
        issue.agent_status = TicketAgentStatus.planning
    await db.commit()
    await db.refresh(issue)
    return issue


@router.delete("/issues/{key}/assign-agent", response_model=IssueOut)
async def unassign_agent(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    if not access.ai_assign:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")
    if issue.agent_working:
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent arbeitet gerade — erst stoppen")
    issue.assigned_agent = None
    issue.assigned_by_user_id = None
    issue.assigned_at = None
    issue.agent_status = None
    issue.hold_reason = None
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Kommentare ----------

@router.get("/issues/{key}/comments", response_model=list[CommentOut])
async def list_comments(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, _ = pair
    rows = (
        await db.execute(
            select(Comment).where(Comment.issue_id == issue.id).order_by(Comment.created_at)
        )
    ).scalars().all()
    return list(rows)


@router.post("/issues/{key}/comments", response_model=CommentOut,
             status_code=status.HTTP_201_CREATED)
async def add_comment(
    data: CommentCreate,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    if access.role == ProjectRole.viewer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewer darf nicht kommentieren")
    if data.kind not in ("agent", "internal"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind muss agent|internal sein")
    from ..services.comments import apply_user_comment
    label = access.user.display_name or access.user.username
    await apply_user_comment(db, issue, data.body, access.user.id, label, data.kind)
    c = (
        await db.execute(
            select(Comment).where(Comment.issue_id == issue.id).order_by(Comment.id.desc())
        )
    ).scalars().first()
    return c


# ---------- Board-Move (Status + Reihenfolge) ----------

RANK_STEP = 1000


@router.put("/issues/{key}/move", response_model=IssueOut)
async def move_issue(
    data: MoveIn,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    target_status = await db.get(WorkflowStatus, data.status_id)
    if target_status is None or target_status.project_id != issue.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Status gehört nicht zum Projekt")
    issue.status_id = data.status_id
    await db.flush()
    # Alle Tickets der Zielspalte (ohne dieses) ordnen, neu einfügen, Ränge sequenziell.
    others = (
        await db.execute(
            select(Issue).where(
                Issue.project_id == issue.project_id,
                Issue.status_id == data.status_id,
                Issue.id != issue.id,
            ).order_by(Issue.rank, Issue.number)
        )
    ).scalars().all()
    pos = max(0, min(data.position, len(others)))
    ordered = others[:pos] + [issue] + others[pos:]
    for i, it in enumerate(ordered):
        it.rank = f"{(i + 1) * RANK_STEP:012d}"
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Tags ----------

@router.post("/issues/{key}/tags", response_model=IssueOut)
async def add_issue_tag(
    data: TagIn,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    name = data.name.strip()
    tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, color=data.color)
        db.add(tag)
        await db.flush()
    exists = (
        await db.execute(
            select(IssueTag).where(IssueTag.issue_id == issue.id, IssueTag.tag_id == tag.id)
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(IssueTag(issue_id=issue.id, tag_id=tag.id))
    await db.commit()
    await db.refresh(issue)
    return issue


@router.delete("/issues/{key}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_issue_tag(
    tag_id: int,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    link = (
        await db.execute(
            select(IssueTag).where(IssueTag.issue_id == issue.id, IssueTag.tag_id == tag_id)
        )
    ).scalar_one_or_none()
    if link is not None:
        await db.delete(link)
        await db.commit()
