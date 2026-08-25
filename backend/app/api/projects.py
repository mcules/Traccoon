import json

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.enums import IssueTypeCategory, ProjectRole, ResourceType, StatusCategory
from ..models.hardware import HardwareAsset, Location
from ..models.project import Project, ProjectMember, ResourceGrant, default_ai_assign
from ..models.ticket import Board, BoardColumn, IssueCounter, IssueType, WorkflowStatus
from ..models.user import User
from ..schemas.project import (
    MemberCreate, MemberOut, MemberUpdate, ProjectCreate, ProjectOut,
    ProjectSettings, ProjectSettingsOut, ProjectUpdate, ResourceGrantIn, ResourceGrantOut,
)
from .deps import Access, get_current_user, get_project_access, require_role

router = APIRouter(tags=["projects"])


def project_out(project: Project, access: Access) -> ProjectOut:
    return ProjectOut(
        id=project.id, key=project.key, name=project.name, description=project.description,
        parent_id=project.parent_id, inherit_members=project.inherit_members,
        avatar_color=project.avatar_color, managed=project.managed,
        pm_chat_enabled=project.pm_chat_enabled, has_hardware=project.has_hardware,
        git_enabled=project.git_enabled, testenv_enabled=project.testenv_enabled,
        my_role=access.role, my_ai_assign=access.ai_assign,
        is_member=access.is_member, is_new=access.is_new,
        my_role_inherited=access.inherited,
    )


async def _seed_project_defaults(project: Project, db: AsyncSession) -> None:
    types = [
        ("Task", "task", "#4BADE8", IssueTypeCategory.standard),
        ("Bug", "bug", "#E5493A", IssueTypeCategory.standard),
        ("Epic", "epic", "#904EE2", IssueTypeCategory.epic),
        ("Subtask", "subtask", "#4BADE8", IssueTypeCategory.subtask),
        ("Hardware", "cpu", "#00857A", IssueTypeCategory.hardware),
    ]
    for i, (name, icon, color, cat) in enumerate(types):
        db.add(IssueType(project_id=project.id, name=name, icon=icon, color=color, category=cat, order=i))

    statuses = [
        ("To Do", StatusCategory.todo),
        ("In Progress", StatusCategory.in_progress),
        ("Waiting", StatusCategory.in_progress),
        # Test environment flow: a finished implementation waits here for acceptance.
        ("Testing", StatusCategory.in_progress),
        ("Done", StatusCategory.done),
    ]
    status_objs = []
    for i, (name, cat) in enumerate(statuses):
        s = WorkflowStatus(project_id=project.id, name=name, category=cat, order=i)
        db.add(s)
        status_objs.append(s)

    board = Board(project_id=project.id, name="Board")
    db.add(board)
    db.add(IssueCounter(project_id=project.id, last_number=0))
    await db.flush()  # ids for the board columns
    for i, s in enumerate(status_objs):
        db.add(BoardColumn(board_id=board.id, status_id=s.id, order=i))


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # An admin sees ALL projects (foreign ones as well, marked is_member=False); everybody
    # else sees their own plus the ones inherited from the parent tree.
    from ..models.enums import GlobalRole
    from .deps import build_access_bulk
    all_projects = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
    if user.global_role == GlobalRole.admin:
        return [
            project_out(p, Access(user, p, ProjectRole.owner, True, True))
            for p in all_projects
        ]
    # Direct membership OR inherited from the parent tree (sub-projects without a membership
    # of their own, a caretaker project under a main project for instance). All memberships
    # of the user AND all projects are preloaded in one query each, to avoid N+1 while walking up the tree.
    projects_by_id = {p.id: p for p in all_projects}
    memberships = (
        await db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id))
    ).scalars().all()
    members_by_project = {m.project_id: m for m in memberships}
    out = []
    for p in all_projects:
        access = build_access_bulk(p, user, members_by_project, projects_by_id)
        if access is None:
            continue
        out.append(project_out(p, access))
    return out


async def _gen_project_key(db: AsyncSession, name: str) -> str:
    """Globally unique, ALWAYS three character project key from the name (A-Z0-9)."""
    import re
    alnum = re.sub(r"[^A-Z0-9]", "", name.upper()) or "PRJ"
    if alnum[0].isdigit():
        alnum = "P" + alnum  # has to start with a letter
    base = alnum[:3].ljust(3, "X")   # exakt 3 Zeichen

    async def free(k: str) -> bool:
        return not (await db.execute(select(Project.id).where(Project.key == k))).first()

    if await free(base):
        return base
    # On a collision: numbered three character variants (UN2…UN9, U10…U99, 100…999)
    for n in range(2, 1000):
        suf = str(n)
        cand = (base[: 3 - len(suf)] + suf)[:3]
        if await free(cand):
            return cand
    raise Error(status.HTTP_409_CONFLICT, "err.no_free_project_key", "No free project key")


async def _assert_valid_parent(project_id: int | None, parent_id: int | None, db: AsyncSession) -> None:
    """The parent project has to exist and must not be a descendant (cycle protection).
    `project_id=None` while creating, and then the descendant check is dropped."""
    if parent_id is None:
        return
    if await db.get(Project, parent_id) is None:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.parent_project_does_not_exist",
                     "The parent project does not exist")
    if project_id is None:
        return
    if parent_id == project_id:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.project_cannot_own_parent",
                     "A project cannot be its own parent")
    # Walk up from the new parent: if the project itself turns up, it would be a cycle.
    seen: set[int] = set()
    cur = parent_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        if cur == project_id:
            raise Error(status.HTTP_400_BAD_REQUEST, "err.parent_below_project",
                         "The parent project lies below this project (cycle)")
        node = await db.get(Project, cur)
        cur = node.parent_id if node is not None else None


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    await _assert_valid_parent(None, data.parent_id, db)

    project = Project(
        key=await _gen_project_key(db, data.name), name=data.name, description=data.description,
        parent_id=data.parent_id, managed=data.managed, lead_user_id=user.id,
    )
    db.add(project)
    await db.flush()
    # Creator = owner with the AI right
    db.add(ProjectMember(
        project_id=project.id, user_id=user.id, role=ProjectRole.owner, ai_assign=True
    ))
    await _seed_project_defaults(project, db)
    await db.commit()
    await db.refresh(project)
    access = Access(user, project, ProjectRole.owner, True, True)
    return project_out(project, access)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(access: Access = Depends(get_project_access)):
    return project_out(access.project, access)


@router.put("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    data: ProjectUpdate,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    p = access.project
    fields = data.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"] != p.parent_id:
        await _assert_valid_parent(p.id, fields["parent_id"], db)
    for field, value in fields.items():
        setattr(p, field, value)
    await db.commit()
    await db.refresh(p)
    return project_out(p, access)


_SETTINGS_FIELDS = tuple(ProjectSettings.model_fields)


def _settings_out(p: Project) -> ProjectSettingsOut:
    return ProjectSettingsOut(**{f: getattr(p, f) for f in _SETTINGS_FIELDS},
                              git_token_set=bool(p.git_token_enc),
                              testenv_env_set=bool(p.testenv_env_enc))


class TestenvEnvIn(BaseModel):
    env: dict[str, str]


@router.put("/projects/{project_id}/testenv-env", status_code=204)
async def set_testenv_env(
    data: TestenvEnvIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Store the env of the test environment encrypted (it is never delivered again)."""
    from ..core.security import encrypt_secret
    access.project.testenv_env_enc = encrypt_secret(json.dumps(data.env)) if data.env else ""
    await db.commit()


@router.get("/projects/{project_id}/settings", response_model=ProjectSettingsOut)
async def get_settings(access: Access = Depends(require_role(ProjectRole.maintainer))):
    return _settings_out(access.project)


@router.put("/projects/{project_id}/settings", response_model=ProjectSettingsOut)
async def update_settings(
    data: ProjectSettings,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    p = access.project
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    await db.commit()
    await db.refresh(p)
    return _settings_out(p)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    access: Access = Depends(require_role(ProjectRole.owner)),
    db: AsyncSession = Depends(get_session),
):
    await db.delete(access.project)
    await db.commit()


# ---------- Mitglieder ----------

@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
async def list_members(
    access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session)
):
    rows = (
        await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == access.project.id)
            .order_by(ProjectMember.id)
        )
    ).all()
    return [
        MemberOut(id=m.id, user_id=u.id, username=u.username, display_name=u.display_name,
                  role=m.role, ai_assign=m.ai_assign)
        for m, u in rows
    ]


@router.post("/projects/{project_id}/members", response_model=MemberOut,
             status_code=status.HTTP_201_CREATED)
async def add_member(
    data: MemberCreate,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    target = await db.get(User, data.user_id)
    if target is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.user_not_found", "User not found")
    dup = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == access.project.id,
                ProjectMember.user_id == data.user_id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise Error(status.HTTP_409_CONFLICT, "err.already_member", "Already a member")
    ai = data.ai_assign if data.ai_assign is not None else default_ai_assign(data.role)
    m = ProjectMember(project_id=access.project.id, user_id=data.user_id, role=data.role, ai_assign=ai)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return MemberOut(id=m.id, user_id=target.id, username=target.username,
                     display_name=target.display_name, role=m.role, ai_assign=m.ai_assign)


@router.put("/projects/{project_id}/members/{user_id}", response_model=MemberOut)
async def update_member(
    user_id: int,
    data: MemberUpdate,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    m = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == access.project.id, ProjectMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.not_member", "Not a member")
    if data.role is not None:
        m.role = data.role
        if data.ai_assign is None:
            m.ai_assign = default_ai_assign(data.role)
    if data.ai_assign is not None:
        m.ai_assign = data.ai_assign
    await db.commit()
    await db.refresh(m)
    target = await db.get(User, user_id)
    return MemberOut(id=m.id, user_id=user_id, username=target.username,
                     display_name=target.display_name, role=m.role, ai_assign=m.ai_assign)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: int,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    m = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == access.project.id, ProjectMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.not_member", "Not a member")
    if m.role == ProjectRole.owner and access.role != ProjectRole.owner:
        raise Error(status.HTTP_403_FORBIDDEN, "err.owner_may_only_removed_owner",
                     "An owner may only be removed by an owner")
    await db.delete(m)
    await db.commit()


# ---------- Granular grants (caretaker case: a single location or asset without a full membership) ----------

async def _resource_label(rt: ResourceType, rid: int, db: AsyncSession) -> str:
    if rt == ResourceType.location:
        loc = await db.get(Location, rid)
        return loc.full_path if loc else f"#{rid}"
    asset = await db.get(HardwareAsset, rid)
    if asset is None:
        return f"#{rid}"
    from ..models.hardware import HardwareModel
    model = await db.get(HardwareModel, asset.model_id)
    parts = [model.name if model else f"Modell #{asset.model_id}", asset.serial_number, f"#{asset.id}"]
    return " · ".join(p for p in parts if p)


async def _grant_out(g: ResourceGrant, db: AsyncSession) -> ResourceGrantOut:
    user = await db.get(User, g.user_id)
    return ResourceGrantOut(
        id=g.id, project_id=g.project_id, user_id=g.user_id,
        username=user.username if user else "?", display_name=user.display_name if user else "?",
        resource_type=g.resource_type, resource_id=g.resource_id,
        resource_label=await _resource_label(g.resource_type, g.resource_id, db),
        level=g.level, recursive=g.recursive,
    )


@router.get("/projects/{project_id}/resource-grants", response_model=list[ResourceGrantOut])
async def list_resource_grants(
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    rows = (
        await db.execute(
            select(ResourceGrant).where(ResourceGrant.project_id == access.project.id)
            .order_by(ResourceGrant.id)
        )
    ).scalars().all()
    return [await _grant_out(g, db) for g in rows]


@router.post("/projects/{project_id}/resource-grants", response_model=ResourceGrantOut,
             status_code=status.HTTP_201_CREATED)
async def add_resource_grant(
    data: ResourceGrantIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    if await db.get(User, data.user_id) is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.user_not_found", "User not found")
    if data.resource_type == ResourceType.location:
        exists = await db.get(Location, data.resource_id)
    else:
        exists = await db.get(HardwareAsset, data.resource_id)
    if exists is None:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.object_does_not_exist",
                     "The object does not exist")
    # The object has to belong to the granting project; otherwise a maintainer could hand out
    # grants for foreign locations or assets from other projects (privilege escalation).
    if exists.project_id != access.project.id:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.object_does_not_belong_project",
                     "The object does not belong to this project")
    dup = (
        await db.execute(
            select(ResourceGrant).where(
                ResourceGrant.user_id == data.user_id,
                ResourceGrant.resource_type == data.resource_type,
                ResourceGrant.resource_id == data.resource_id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise Error(status.HTTP_409_CONFLICT, "err.grant_already_exists",
                     "The grant already exists")
    # `recursive` only makes sense with locations (inheritance to child locations). With
    # units it is normalised so that equivalent grants are not stored differently.
    recursive = data.recursive if data.resource_type == ResourceType.location else False
    g = ResourceGrant(
        project_id=access.project.id, user_id=data.user_id, resource_type=data.resource_type,
        resource_id=data.resource_id, level=data.level, recursive=recursive,
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return await _grant_out(g, db)


@router.delete("/projects/{project_id}/resource-grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_resource_grant(
    grant_id: int,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    g = await db.get(ResourceGrant, grant_id)
    if g is None or g.project_id != access.project.id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.grant_not_found", "Grant not found")
    await db.delete(g)
    await db.commit()
