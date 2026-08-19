import datetime as dt
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text, update as sa_update
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
from .deps import Access, build_access, get_current_user, get_project_access

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
    access = await build_access(project, user, db)  # 404 on a foreign project
    return issue, access


def _require_write(access: Access) -> None:
    if not access.has_role(ProjectRole.member):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Schreibrecht (member) erforderlich")


async def _assert_asset_in_project(asset_id: int, project_id: int, db: AsyncSession) -> None:
    """The hardware reference (TRA-25) may only point at units of one's own project;
    otherwise a ticket would reference a foreign unit and leak its existence."""
    from ..models.hardware import HardwareAsset
    asset = await db.get(HardwareAsset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The unit does not exist")
    if asset.project_id != project_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The unit does not belong to this project"
        )


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
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The project has no issue types")
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
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The project has no statuses")
        status_id = s.id

    # Race safe key allocation over a counter row lock.
    counter = (
        await db.execute(
            select(IssueCounter).where(IssueCounter.project_id == project.id).with_for_update()
        )
    ).scalar_one()
    counter.last_number += 1
    number = counter.last_number

    if data.asset_id is not None:
        await _assert_asset_in_project(data.asset_id, project.id, db)

    issue = Issue(
        project_id=project.id, number=number, key=f"{project.key}-{number}",
        type_id=type_id, status_id=status_id, priority=data.priority,
        summary=data.summary, description=data.description, reporter_id=access.user.id,
        parent_id=data.parent_id, asset_id=data.asset_id,
        sprint_id=data.sprint_id, story_points=data.story_points, rank=f"{number:08d}",
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    from ..services.events import emit
    await emit(db, "issue.created", project_id=project.id, issue_id=issue.id,
               actor_id=access.user.id,
               payload={"issue": {"key": issue.key, "summary": issue.summary,
                                  "priority": issue.priority.value}})
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
    fields = data.model_dump(exclude_unset=True)
    if fields.get("asset_id") is not None:
        await _assert_asset_in_project(fields["asset_id"], issue.project_id, db)
    for field, value in fields.items():
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Deleting requires maintainer")
    if issue.testenv_status:
        from ..services.testenv import stop_testenv
        await stop_testenv(db, issue, access.project.key)
    await db.delete(issue)
    await db.commit()


@router.post("/issues/{key}/archive", response_model=IssueOut)
async def archive_issue(
    pair: tuple[Issue, Access] = Depends(get_issue_access),
    db: AsyncSession = Depends(get_session),
):
    issue, access = pair
    _require_write(access)
    now = dt.datetime.now(tz=dt.timezone.utc)
    issue.archived = True
    issue.archived_at = now
    # Clear away an orphaned test environment as well (TRA-18): container, volumes, port.
    if issue.testenv_status:
        from ..services.testenv import stop_testenv
        await stop_testenv(db, issue, access.project.key)
    # Agent runs follow the ticket (TRA-29).
    from ..models.agents import Run
    await db.execute(
        sa_update(Run).where(Run.issue_id == issue.id, Run.archived.is_(False))
        .values(archived=True, archived_at=now)
    )
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
    from ..models.agents import Run
    await db.execute(
        sa_update(Run).where(Run.issue_id == issue.id, Run.archived.is_(True))
        .values(archived=False, archived_at=None)
    )
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Agent assignment (core feature, only with the AI right) ----------

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
    # The assignment starts the planning: with a PM the PM plans and orchestrates, with a
    # direct assignment the plan_agent plans and the assigned agent implements afterwards.
    if issue.agent_status is None:
        from ..services.artifacts import set_ticket_status
        # Planning starts, so "in progress" (out of to do); the artifact row follows.
        await set_ticket_status(db, issue, TicketAgentStatus.planning)
    await db.commit()
    # The flow itself sits in the process "AI ticket lifecycle" (project copy, set of the
    # user or global default); here it is only triggered.
    from ..services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, access.user.id,
                          entry="exec" if issue.plan else "plan")
    await db.commit()
    from ..services.events import emit
    await emit(db, "issue.assigned", project_id=issue.project_id, issue_id=issue.id,
               actor_id=access.user.id,
               payload={"issue": {"key": issue.key, "agent": data.agent}})
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
    # Without an agent there is no lifecycle any more: end a running instance, because
    # otherwise it would want to keep running on the next tick.
    from ..services.lifecycle_flow import cancel_lifecycle
    await cancel_lifecycle(db, issue)
    issue.assigned_agent = None
    issue.assigned_by_user_id = None
    issue.assigned_at = None
    from ..services.artifacts import set_ticket_status
    # The board column stays where it is; the ticket only loses its agent.
    await set_ticket_status(db, issue, None, board=False)
    await db.commit()
    await db.refresh(issue)
    return issue


# ---------- Person assignment (human, orthogonal to the AI assignment) ----------

# The slug length is limited so that "placeholder+{slug}.{suffix}@traccoon.local"
# (<=255, email) and "{slug}.{suffix}" (<=100, username) fit safely with an 8 digit hex
# suffix and the fixed parts, even with a display_name of up to 255 characters.
# The slug is truncated BEFORE the suffix is appended (not afterwards), because otherwise
# the suffix would be cut off with long names and the randomisation would be lost.
_PLACEHOLDER_SLUG_MAX = 60


async def _get_or_create_placeholder(db: AsyncSession, project_id: int, display_name: str) -> User:
    """Finds an existing placeholder account with the same name (case insensitive) that is
    already a member of THIS project, or creates a new one. Placeholders have no login
    (empty password hash, status placeholder) and serve only as an assignment target. The
    search is scoped to the project membership so that placeholders are not reused across
    projects (which would break the otherwise strictly enforced multi-tenancy isolation, see
    build_access)."""
    name = display_name.strip()

    # Transaction wide advisory lock on (project_id, lower(name)), so that two parallel
    # requests with an identical name are serialised; otherwise both would see "no hit"
    # (select-then-insert race) and create two placeholders with the same name. The lock is
    # released automatically on commit or rollback.
    lock_key = int(
        hashlib.sha256(f"placeholder:{project_id}:{name.lower()}".encode()).hexdigest()[:15],
        16,
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    existing = (
        await db.execute(
            select(User)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                User.status == UserStatus.placeholder,
                func.lower(User.display_name) == name.lower(),
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    slug = "".join(c for c in name.lower().replace(" ", ".") if c.isalnum() or c == ".") or "person"
    slug = slug[:_PLACEHOLDER_SLUG_MAX]
    # Race safe: savepoint plus catching the unique constraint violation and retrying with a
    # new random suffix (analogous to _ensure_member) instead of throwing an unhandled 500.
    # The advisory lock above already covers the normal case; this is an additional
    # safeguard against exotic collisions (for instance a suffix hit from another name).
    for _ in range(5):
        suffix = secrets.token_hex(4)
        user = User(
            email=f"placeholder+{slug}.{suffix}@traccoon.local",
            username=f"{slug}.{suffix}",
            display_name=name,
            password_hash="",
            status=UserStatus.placeholder,
        )
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError:
            continue
        return user
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "The placeholder account could not be created"
    )


async def _ensure_member(db: AsyncSession, project_id: int, user_id: int) -> None:
    """Makes sure the assigned person is a project member (otherwise they do not see the
    ticket in their list and have no access). Idempotent and race safe via a savepoint plus
    catching the unique constraint violation (instead of select-then-insert)."""
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
        # A parallel request created the membership in the meantime, which is fine.
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
    # Assignment by user_id is only allowed on already existing project members. Otherwise
    # every member could set foreign or guessed user ids (from other projects as well) as
    # the assignee and thereby add them automatically (without an invitation or consent) as
    # a ProjectMember, which would bypass the invite flow (require_role(maintainer)) and
    # additionally allow user id enumeration over 200/404.
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
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")
    elif data.display_name:
        target = await _get_or_create_placeholder(db, issue.project_id, data.display_name)
        await _ensure_member(db, issue.project_id, target.id)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_id or display_name is required")

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
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A viewer may not comment")
    if data.kind not in ("agent", "internal"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind has to be agent|internal")
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


async def _guard_done_transition(issue: Issue, target: WorkflowStatus, db: AsyncSession) -> None:
    """In the test environment flow, "done" may ONLY be set over POST /issues/{key}/complete
    (stop, merge, done). A direct board move there would skip the merge and show a silently
    unmerged ticket as finished (TRA-18)."""
    from ..models.enums import StatusCategory
    if target.category != StatusCategory.done:
        return
    if issue.agent_status not in (TicketAgentStatus.to_test, TicketAgentStatus.testing):
        return
    project = await db.get(Project, issue.project_id)
    if project is None or not project.testenv_enabled:
        return
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        'On to "done" only over "set to done", which stops the test environment '
        "and merges the branch.",
    )


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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The status does not belong to the project")
    await _guard_done_transition(issue, target_status, db)
    issue.status_id = data.status_id
    await db.flush()
    # Order all tickets of the target column (except this one), insert anew, ranks sequential.
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
