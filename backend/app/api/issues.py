import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import ProjectRole, TicketAgentStatus, UserStatus
from ..models.project import Project, ProjectMember, default_ai_assign
from ..models.ticket import (
    Comment, Issue, IssueCounter, IssueTag, IssueType, Tag, WorkflowStatus,
)
from ..models.user import User
from ..schemas.issue import (
    AssignAgentIn, AssigneeIn, CommentCreate, CommentOut, IssueCreate, IssueOut, IssueUpdate,
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
    from ..services.dispatcher import sync_board_status
    await sync_board_status(db, issue)   # Planung startet → „In Arbeit" (raus aus To Do)
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


# ---------- Personen-Zuweisung (Mensch, orthogonal zur KI-Zuweisung) ----------

async def _get_or_create_placeholder(db: AsyncSession, display_name: str) -> User:
    """Findet ein bestehendes Platzhalter-Konto mit gleichem Namen (case-insensitive)
    oder legt ein neues an. Platzhalter haben keinen Login (leerer Passwort-Hash,
    Status placeholder) und dienen nur als Zuweisungsziel."""
    name = display_name.strip()
    existing = (
        await db.execute(
            select(User).where(
                User.status == UserStatus.placeholder,
                func.lower(User.display_name) == name.lower(),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    slug = "".join(c for c in name.lower().replace(" ", ".") if c.isalnum() or c == ".") or "person"
    suffix = secrets.token_hex(4)
    user = User(
        email=f"placeholder+{slug}.{suffix}@traccoon.local",
        username=f"{slug}.{suffix}"[:100],
        display_name=name,
        password_hash="",
        status=UserStatus.placeholder,
    )
    db.add(user)
    await db.flush()
    return user


async def _ensure_member(db: AsyncSession, project_id: int, user_id: int) -> None:
    """Stellt sicher, dass die zugewiesene Person Projektmitglied ist (sonst sieht
    sie das Ticket nicht in ihrer Liste / hat keinen Zugriff). Idempotent/racesicher
    via Savepoint + Auffangen der UniqueConstraint-Verletzung (statt Select-then-Insert)."""
    dup = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        return
    try:
        async with db.begin_nested():
            db.add(ProjectMember(
                project_id=project_id, user_id=user_id, role=ProjectRole.viewer,
                ai_assign=default_ai_assign(ProjectRole.viewer),
            ))
    except IntegrityError:
        # Paralleler Request hat die Mitgliedschaft zwischenzeitlich angelegt — ok.
        pass


@router.post("/issues/{key}/assignee", response_model=IssueOut)
async def set_assignee(
    data: AssigneeIn,
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)

    if data.user_id is not None:
        # Zuweisung per user_id NUR auf bereits existierende Projektmitglieder erlauben.
        # Sonst könnte jedes Member fremde/erratene User-IDs (auch aus anderen Projekten)
        # als Assignee setzen und sie dadurch automatisch (ohne Einladung/Zustimmung)
        # als ProjectMember hinzufügen — das umgeht den Invite-Flow (require_role(maintainer))
        # und erlaubt zudem User-ID-Enumeration über 200/404.
        target = (
            await db.execute(
                select(User)
                .join(ProjectMember, ProjectMember.user_id == User.id)
                .where(
                    ProjectMember.project_id == issue.project_id,
                    User.id == data.user_id,
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Person nicht gefunden")
    elif data.display_name:
        target = await _get_or_create_placeholder(db, data.display_name)
        await _ensure_member(db, issue.project_id, target.id)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id oder display_name erforderlich")

    issue.assignee_user_id = target.id
    await db.commit()
    await db.refresh(issue)
    return issue


@router.delete("/issues/{key}/assignee", response_model=IssueOut)
async def unset_assignee(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    issue.assignee_user_id = None
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
