"""REST router of the workflow engine: definitions, versions (editor), instances, tasks.

Access model:
- Writing/publishing definitions: project role owner|maintainer OR access.ai_assign
  (global templates without a project: admin only).
- Starting instances/steps: project membership (member+); project-less instances: authenticated.
- approve/reject: access.ai_assign when node.config.gate=="ai_assign", otherwise the configured role.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.enums import (
    ProjectRole, WorkflowNodeType, WorkflowStepStatus, WorkflowVersionStatus,
)
from ..models.project import Project
from ..models.user import User
from ..models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowStepRun, WorkflowToken, WorkflowVersion,
)
from ..schemas.workflow import (
    ApproveIn, InstanceCreate, InstanceOut, RejectIn, SlotOut, StepCompleteIn, StepRunOut,
    TokenLite, ValidateOut, WorkflowDefinitionCreate, WorkflowDefinitionOut,
    WorkflowDefinitionUpdate, WorkflowSetCreate, WorkflowSetOut, WorkflowTaskLite,
    WorkflowVersionOut, WorkflowVersionUpdate,
)
from ..schemas.workflow import DiffOut, GraphSaveOut
from ..services import workflow_engine as engine
from ..services import workflow_graph as wgraph
from ..services import workflow_sets as sets
from ..services.workflow_engine import node_config
from .deps import build_access, get_current_user

router = APIRouter(tags=["workflows"])


# ── interne Helfer ───────────────────────────────────────────────────────────

def _is_admin(user: User) -> bool:
    from ..models.enums import GlobalRole
    return user.global_role == GlobalRole.admin


def _belongs(d, user: User) -> bool:
    """Is this a free flow of this person's own?

    Free means: bound to no project and no slot. Whoever creates one is its owner
    (`created_by`) and alone sees, changes and starts it. Without that boundary "own flow"
    would be a contradiction: the definition lies project-less in the same table as the
    shipped templates and would therefore be open to everybody.
    """
    return d.project_id is None and not d.slot and d.created_by == user.id


async def _require_def_write(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Write permission on a definition: project owner|maintainer OR ai_assign.

    Without a project **every logged-in user** may create one: an own flow is not an admin
    right. What it may do is not decided here but where it takes effect: bound artifacts and
    event triggers are checked against the rights of its owner
    (`_require_subjekt_recht`, `events.listeners`).
    """
    if project_id is None:
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")
    access = await build_access(project, user, db)  # 404 on a foreign project
    if not (access.has_role(ProjectRole.maintainer) or access.ai_assign):
        raise Error(status.HTTP_403_FORBIDDEN, "err.role_or_ai_right_required",
                     "Role owner|maintainer or the AI right (ai_assign) is required")


async def _require_write(db: AsyncSession, user: User, d) -> None:
    """Write permission on a concrete definition.

    If it belongs to a process set, the set decides (personal = owner, global = admin); a
    free flow belongs to its creator; otherwise the project rules apply.
    
    """
    if d.set_id:
        return await _require_set_write(db, user, await _get_set(db, d.set_id))
    if d.project_id is None and not d.slot:
        if not (_belongs(d, user) or _is_admin(user)):
            raise Error(status.HTTP_403_FORBIDDEN, "err.flow_belongs_somebody_else",
                         "This flow belongs to somebody else")
        return
    await _require_def_write(db, user, d.project_id)


async def _require_project_read(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Read/use access to a project (global and project-less objects: free for logged-in users)."""
    if project_id is None:
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.project_not_found", "Project not found")
    await build_access(project, user, db)  # raises 404 when access is missing


async def _get_def(db: AsyncSession, def_id: int) -> WorkflowDefinition:
    d = await db.get(WorkflowDefinition, def_id)
    if d is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.workflow_not_found", "Workflow not found")
    return d


async def _next_version_number(db: AsyncSession, def_id: int) -> int:
    from sqlalchemy import func
    n = (await db.execute(
        select(func.max(WorkflowVersion.version)).where(WorkflowVersion.definition_id == def_id)
    )).scalar()
    return (n or 0) + 1


async def _load_instance_out(db: AsyncSession, inst: WorkflowInstance) -> InstanceOut:
    tokens = (await db.execute(
        select(WorkflowToken).where(WorkflowToken.instance_id == inst.id)
        .order_by(WorkflowToken.id))).scalars().all()
    steps = (await db.execute(
        select(WorkflowStepRun).where(WorkflowStepRun.instance_id == inst.id)
        .order_by(WorkflowStepRun.id))).scalars().all()
    version = await db.get(WorkflowVersion, inst.version_id)
    graph = (version.graph if version else None) or {}
    return InstanceOut(
        id=inst.id, definition_id=inst.definition_id, version_id=inst.version_id,
        project_id=inst.project_id, subject_kind=inst.subject_kind, issue_id=inst.issue_id,
        hardware_asset_id=inst.hardware_asset_id, status=inst.status, context=inst.context or {},
        error=inst.error, started_at=inst.started_at, finished_at=inst.finished_at,
        tokens=[TokenLite.model_validate(t) for t in tokens],
        steps=[StepRunOut.model_validate(s) for s in steps],
        graph=graph,
    )


async def _instance_access(db: AsyncSession, user: User, inst: WorkflowInstance,
                           minimum: ProjectRole = ProjectRole.member):
    """Access to an instance plus (when there is a project) the access object."""
    if inst.project_id is None:
        return None
    project = await db.get(Project, inst.project_id)
    if project is None:
        return None
    access = await build_access(project, user, db)
    if not access.has_role(minimum):
        raise Error(status.HTTP_403_FORBIDDEN, "err.role_required",
                     "Role {role} is required", role=minimum.value)
    return access


@router.get("/workflow-events")
async def workflow_events(user: User = Depends(get_current_user)):
    """Events Traccoon reports itself: suggestion list for the trigger."""
    from ..services.events import BUILTIN_EVENTS
    return [{"event": e, "label": l} for e, l in BUILTIN_EVENTS]


class EventIn(BaseModel):
    event: str = Field(min_length=1, max_length=120)
    project_id: int | None = None
    payload: dict = {}
    source_ref: str | None = None


@router.post("/events")
async def post_event(
    data: EventIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Report an event by hand. Starts every flow whose start node listens for it."""
    if data.project_id is not None:
        await _require_project_read(db, user, data.project_id)
    from ..services.events import emit
    ids = await emit(db, data.event, project_id=data.project_id, payload=data.payload,
                     actor_id=user.id, source_ref=data.source_ref)
    return {"event": data.event, "instances": ids}


@router.get("/workflow-layout")
async def workflow_layout(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Spacing (px) for "arrange" in the editor. Readable for everyone; it is set by the admin
    under `PUT /admin/workflow-layout`."""
    from ..services.appsettings import get_layout_gap
    return {"gap": await get_layout_gap(db)}


@router.get("/workflow-context-fields")
async def workflow_context_fields(user: User = Depends(get_current_user)):
    """Which fields are in the context, per trigger, action and node type.

    The editor builds the selection at a branch from it. Before, that was an empty text
    field: you had to know the path, and a typo only showed when the branch never took hold
    in operation.
    """
    from ..services.workflow_context import catalog
    from ..services.workflow_expr import catalog as filter_catalog
    return {**catalog(), "filter": filter_catalog()}


@router.get("/workflows/{def_id}/webhook")
async def workflow_webhook_read(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """The incoming address of this flow (or `null` when it has none)."""
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    return await _webhook_from(db, d)


@router.post("/workflows/{def_id}/webhook", status_code=201)
async def workflow_webhook_create(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Give this flow an address of its own under which a foreign system triggers it.

    Not every system speaks MCP, and very few know Traccoon's events, but almost every one
    can send a webhook. Until now you had to create it in the settings and pick the flow
    there: the source stood at the other end, and in the flow itself it was invisible. Now it
    comes into being where it belongs.

    The call is idempotent: a second one returns the same address.
    """
    import secrets as _secrets
    import uuid as _uuid

    from ..models.ops import WebhookSub

    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    existing = await _webhook_from(db, d)
    if existing:
        return existing

    sub = WebhookSub(
        public_id=str(_uuid.uuid4()), owner_user_id=user.id,
        route=(d.key or f"ablauf-{d.id}")[:120], secret=_secrets.token_hex(24),
        mode="workflow", project_id=d.project_id, workflow_definition_id=d.id,
        context_map={},   # without a mapping the whole payload lands in the context
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return await _webhook_from(db, d)


async def _webhook_from(db: AsyncSession, d) -> dict | None:
    """The webhook that starts exactly this flow (including address and secret)."""
    from ..config import settings
    from ..models.ops import WebhookSub

    sub = (await db.execute(select(WebhookSub).where(
        WebhookSub.mode == "workflow",
        WebhookSub.workflow_definition_id == d.id).order_by(WebhookSub.id))).scalars().first()
    if sub is None:
        return None
    # The same base as with invitation links; without it the path stays relative so that
    # nobody copies a URL that points nowhere from the outside.
    basis = (settings.app_base_url or "").rstrip("/")
    return {
        "id": sub.id, "route": sub.route, "public_id": sub.public_id,
        "url": f"{basis}/api/hooks/{sub.public_id}" if basis
               else f"/api/hooks/{sub.public_id}",
        "secret": sub.secret, "enabled": sub.enabled,
        "ref_field": sub.ref_field or "",
    }


@router.get("/workflow-tools")
async def workflow_tools(user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """The MCP tools of THIS person: the selection for the node "call tool".

    Deliberately their own: a flow later calls through the MCPJungle access of its owner, not
    through a global one. What is not listed here it cannot call either.
    """
    from ..services.workflow_tools import tools
    return await tools(db, user.id)


@router.get("/workflow-templates")
async def workflow_templates_list(user: User = Depends(get_current_user)):
    """Finished flows to copy: the selection when creating one.

    Only the description, not the graph: the overview does not need it, and whoever takes a
    template gets it as their own version 1 anyway (`POST /workflows` with `template`).
    """
    from ..services import workflow_templates
    return workflow_templates.listing()


# ── Process sets ─────────────────────────────────────────────────────────────

async def _get_set(db: AsyncSession, set_id: int):
    from ..models.workflow import WorkflowSet
    s = await db.get(WorkflowSet, set_id)
    if s is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.process_set_not_found",
                     "Process set not found")
    return s


async def _require_set_write(db: AsyncSession, user: User, s) -> None:
    """Global sets may only be changed by an admin, personal ones only by their owner."""
    from ..models.enums import GlobalRole, WorkflowSetScope
    if s.scope == WorkflowSetScope.user:
        if s.user_id != user.id and user.global_role != GlobalRole.admin:
            raise Error(status.HTTP_403_FORBIDDEN, "err.foreign_personal_process_set",
                         "Foreign personal process set")
        return
    if user.global_role != GlobalRole.admin:
        raise Error(status.HTTP_403_FORBIDDEN, "err.only_admin_may_change_global_process",
                     "Only an admin may change global process sets")


@router.get("/workflow-sets", response_model=list[WorkflowSetOut])
async def list_sets(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Visible sets: all global ones plus one's own (admins see all)."""
    from ..models.enums import GlobalRole, WorkflowSetScope
    from ..models.workflow import WorkflowSet
    q = select(WorkflowSet)
    if user.global_role != GlobalRole.admin:
        q = q.where(or_(WorkflowSet.scope == WorkflowSetScope.global_,
                        WorkflowSet.user_id == user.id))
    return list((await db.execute(q.order_by(WorkflowSet.id))).scalars().all())


@router.get("/workflow-sets/{set_id}/slots", response_model=list[SlotOut])
async def set_slots(
    set_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """What a set holds: the template stored for each slot."""
    s = await _get_set(db, set_id)
    out = []
    for slot, meta in sets.SLOT_META.items():
        d = await sets.set_definition(db, s.id, slot)
        out.append(SlotOut(
            slot=slot, name=meta["name"], description=meta["description"],
            subject_kind=meta["subject_kind"],
            origin="builtin" if s.is_builtin else s.scope.value,
            set_id=s.id, set_name=s.name,
            definition_id=d.id if d else None, definition_name=d.name if d else None,
            published=bool(d and d.current_version_id),
        ))
    return out


@router.post("/me/workflow-set", response_model=WorkflowSetOut, status_code=201)
async def create_my_set(
    data: WorkflowSetCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Create an own default set (a copy of the global one); it then applies to all projects
    in which I have the owner role and which have not chosen a set of their own."""
    if user.workflow_set_id:
        raise Error(status.HTTP_409_CONFLICT, "err.personal_set_already_exists",
                     "A personal set already exists")
    return await sets.create_user_set(db, user, data.name, data.source_set_id)


@router.delete("/me/workflow-set", status_code=204)
async def drop_my_set(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Give up the personal set, so my projects follow the global default again."""
    from ..models.workflow import WorkflowSet
    sid = user.workflow_set_id
    user.workflow_set_id = None
    if sid:
        s = await db.get(WorkflowSet, sid)
        if s is not None and not s.is_builtin:
            await db.delete(s)
    await db.commit()


# ── Slots of a project (adjust / reset) ──────────────────────────────────────

@router.get("/projects/{project_id}/workflow-slots", response_model=list[SlotOut])
async def project_slots(
    project_id: int, user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    await _require_project_read(db, user, project_id)
    project = await db.get(Project, project_id)
    return [SlotOut(**row) for row in await sets.slot_overview(db, project)]


@router.post("/projects/{project_id}/workflow-slots/{slot}/customize",
             response_model=WorkflowDefinitionOut, status_code=201)
async def customize_slot(
    project_id: int, slot: str, issue_type_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Create a project-owned copy of the applicable flow (copy-on-write).

    With `issue_type_id` the copy applies only to this issue type; all other tickets of the
    project keep following the set.
    """
    await _require_def_write(db, user, project_id)
    project = await db.get(Project, project_id)
    try:
        return await sets.customize(db, project, slot, user.id, issue_type_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.post("/projects/{project_id}/workflow-slots/{slot}/reset", status_code=200)
async def reset_slot(
    project_id: int, slot: str, issue_type_id: int | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Discard the adjustment, so the set applies again. Running instances stay untouched.

    With `issue_type_id` it concerns only the flow of this issue type.
    """
    await _require_def_write(db, user, project_id)
    project = await db.get(Project, project_id)
    done = await sets.reset(db, project, slot, issue_type_id)
    return {"reset": done}


@router.put("/projects/{project_id}/workflow-set", response_model=list[SlotOut])
async def set_project_set(
    project_id: int, set_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Choose the set this project follows (NULL = owner set respectively global default)."""
    await _require_def_write(db, user, project_id)
    project = await db.get(Project, project_id)
    if set_id is not None:
        await _get_set(db, set_id)
    project.workflow_set_id = set_id
    await db.commit()
    return [SlotOut(**row) for row in await sets.slot_overview(db, project)]


# ── Definitionen ─────────────────────────────────────────────────────────────

@router.get("/workflows", response_model=list[WorkflowDefinitionOut])
async def list_workflows(
    project_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    if project_id is not None:
        await _require_project_read(db, user, project_id)
        q = select(WorkflowDefinition).where(
            or_(WorkflowDefinition.project_id == project_id, WorkflowDefinition.project_id.is_(None)))
    else:
        q = select(WorkflowDefinition)
    # Reset project copies do stay in the database (instances hang off them) but no longer
    # belong in the selection.
    q = q.where(WorkflowDefinition.archived_at.is_(None))
    rows = (await db.execute(q.order_by(WorkflowDefinition.id))).scalars().all()
    # Free flows are private: they stand project-less in the same table as the shipped
    # templates but belong to a person. Without this filter everybody would see the flows of
    # everybody else.
    if not _is_admin(user):
        rows = [d for d in rows
                if d.project_id is not None or d.slot or d.created_by == user.id]
    return list(rows)


@router.post("/workflows", response_model=WorkflowDefinitionOut, status_code=201)
async def create_workflow(
    data: WorkflowDefinitionCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    await _require_def_write(db, user, data.project_id)
    exists = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.project_id == data.project_id, WorkflowDefinition.key == data.key
    ))).scalar_one_or_none()
    if exists is not None:
        raise Error(status.HTTP_409_CONFLICT, "err.key_already_taken_project",
                     "The key is already taken in the project")
    from ..services import workflow_templates
    template = workflow_templates.template(data.template) if data.template else None
    if data.template and template is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.unknown_template",
                     "Unknown template '{name}'", name=data.template)
    d = WorkflowDefinition(
        project_id=data.project_id, key=data.key, name=data.name,
        description=data.description or (template["description"] if template else ""),
        subject_kind=template["subject_kind"] if template else data.subject_kind,
        created_by=user.id,
    )
    db.add(d)
    await db.flush()
    # Version 1 with a start and an end, not empty. An empty canvas tells nobody where to
    # begin; with the two ends the frame stands and the first step goes in between. (Noticed
    # while clicking through: a fresh flow had not a single node, not even a start.)
    v1 = WorkflowVersion(
        definition_id=d.id, version=1, status=WorkflowVersionStatus.draft, created_by=user.id,
        graph=workflow_templates.graph(data.template) if template else {
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
                 "data": {"config": {"label": "Auslöser"}}},
                {"id": "ende", "type": "end", "position": {"x": 0, "y": 260},
                 "data": {"config": {"label": "Done", "outcome": "completed"}}},
            ],
            "edges": [{"id": "e-start-out-ende", "source": "start", "target": "ende"}],
        })
    db.add(v1)
    await db.commit()
    await db.refresh(d)
    return d


@router.get("/workflows/{def_id}", response_model=WorkflowDefinitionOut)
async def get_workflow(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    return d


async def _key_set(db: AsyncSession, d: WorkflowDefinition, raw: str) -> None:
    """Change the key of a flow — with the three rules that hang on it.

    It is more than a label: a slot finds its flow through it, and a shipped set is matched
    against it at every start. Where that applies it stays as it is; everywhere else it may be
    called what the matter is called.
    """
    from ..core.slug import slug

    key = slug(raw, 60)
    if not key:
        raise Error(400, "err.key_invalid",
                     "Der Schlüssel braucht Buchstaben oder Ziffern")
    if key == d.key:
        return
    if d.slot or d.set_id:
        raise Error(400, "err.key_fixed",
                     "Dieser Ablauf gehört zu einem Satz oder einer festen Aufgabe — "
                     "sein Schlüssel bleibt")
    already = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.project_id.is_(None) if d.project_id is None
        else WorkflowDefinition.project_id == d.project_id,
        WorkflowDefinition.key == key,
        WorkflowDefinition.id != d.id))).scalars().first()
    if already is not None:
        raise Error(400, "err.key_taken", "Diesen Schlüssel gibt es hier schon")
    d.key = key


@router.put("/workflows/{def_id}", response_model=WorkflowDefinitionOut)
async def update_workflow(
    def_id: int, data: WorkflowDefinitionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise Error(400, "err.name_required", "Der Ablauf braucht einen Namen")
        d.name = name
    if data.key is not None:
        await _key_set(db, d, data.key)
    if data.description is not None:
        d.description = data.description
    if data.enabled is not None:
        d.enabled = data.enabled
    await db.commit()
    await db.refresh(d)
    return d


@router.delete("/workflows/{def_id}", status_code=204)
async def delete_workflow(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    await db.delete(d)
    await db.commit()


# ── Versionen ────────────────────────────────────────────────────────────────

@router.get("/workflows/{def_id}/versions", response_model=list[WorkflowVersionOut])
async def list_versions(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    rows = (await db.execute(select(WorkflowVersion).where(WorkflowVersion.definition_id == def_id)
                             .order_by(WorkflowVersion.version))).scalars().all()
    return list(rows)


@router.get("/workflows/{def_id}/editable", response_model=WorkflowVersionOut)
async def editable_version(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """What the editor works on: the open draft, otherwise the published version.

    Deliberately WITHOUT creating anything. Opening a flow used to clone a draft, so merely
    looking at one left a version number behind, and the history filled with entries in which
    nothing had happened. A draft now comes into being where it belongs: at the first change
    (see `save_graph`).

    An empty flow that was never published gets an empty graph in the answer, not a row.
    """
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    draft = (await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == def_id,
            WorkflowVersion.status == WorkflowVersionStatus.draft)
        .order_by(WorkflowVersion.version.desc()))).scalars().first()
    if draft is not None:
        return draft
    if d.current_version_id:
        return await db.get(WorkflowVersion, d.current_version_id)
    # Nothing published and nothing drafted: a shell the editor can fill. It carries no id,
    # so the browser knows there is nothing to save onto yet.
    return WorkflowVersion(id=0, definition_id=def_id, version=0,
                           graph={"nodes": [], "edges": []},
                           status=WorkflowVersionStatus.draft, created_by=user.id, notes="")


@router.put("/workflows/{def_id}/graph", response_model=GraphSaveOut)
async def save_graph(
    def_id: int, data: WorkflowVersionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Save the editor state, and decide what that is worth.

    Three cases, and only the last one costs a version:

    * The content matches the version the editor started from, and only positions moved:
      the arrangement is written into THAT version, even a published one. A picture is not
      behaviour, and a flow that was never touched must not be marked as changed because
      somebody tidied up the boxes.
    * A draft is already open: it is updated.
    * The content differs from what is published: a draft comes into being now.

    The answer says which of the three happened (`ergebnis`), so the editor can say it too.
    """
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    graph = data.graph or {"nodes": [], "edges": []}

    draft = (await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == def_id,
            WorkflowVersion.status == WorkflowVersionStatus.draft)
        .order_by(WorkflowVersion.version.desc()))).scalars().first()
    live = await db.get(WorkflowVersion, d.current_version_id) if d.current_version_id else None

    # Layout only: write the positions where they belong and keep quiet about it.
    target = draft or live
    if target is not None and wgraph.same_content(target.graph, graph):
        target.graph = wgraph.with_positions(target.graph, wgraph.positions(graph))
        await db.commit()
        await db.refresh(target)
        return GraphSaveOut(result="layout", version=target, hint="Anordnung gespeichert")

    if draft is not None:
        draft.graph = graph
        if data.notes is not None:
            draft.notes = data.notes
        await db.commit()
        await db.refresh(draft)
        return GraphSaveOut(result="entwurf", version=draft, hint="Entwurf gespeichert")

    draft = WorkflowVersion(
        definition_id=def_id, version=await _next_version_number(db, def_id),
        graph=graph, status=WorkflowVersionStatus.draft, created_by=user.id,
        notes=data.notes if data.notes is not None else (
            f"Änderung an v{live.version}" if live else "Erster Entwurf"),
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return GraphSaveOut(result="neuer_entwurf", version=draft,
                        hint="Entwurf angelegt (der Inhalt weicht ab)")


@router.delete("/workflows/{def_id}/draft", status_code=204)
async def discard_draft(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Throw the open draft away and go back to what is published.

    There was no way to do this: whoever had talked themselves into a corner had to rebuild
    the graph by hand, and a stale draft silently overwrote newer published versions on the
    next publish.
    """
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    draft = (await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == def_id,
            WorkflowVersion.status == WorkflowVersionStatus.draft)
        .order_by(WorkflowVersion.version.desc()))).scalars().first()
    if draft is None:
        return
    if draft.id == d.current_version_id:
        raise Error(status.HTTP_409_CONFLICT, "err.draft_is_current",
                     "This draft is the current version and cannot be discarded")
    await db.delete(draft)
    await db.commit()


@router.get("/workflows/{def_id}/versions/{vid}/diff", response_model=DiffOut)
async def version_diff(
    def_id: int, vid: int, against: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """What changed between two versions, in the words of the editor.

    Without `against` the comparison runs against the version before it, which is the question
    one usually has ("what did this version change?"). Positions are left out: they are not
    what a version is about.
    """
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    new = await db.get(WorkflowVersion, vid)
    if new is None or new.definition_id != def_id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.version_not_found", "Version not found")
    if against is not None:
        old = await db.get(WorkflowVersion, against)
        if old is None or old.definition_id != def_id:
            raise Error(status.HTTP_404_NOT_FOUND, "err.version_not_found", "Version not found")
    else:
        old = (await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.definition_id == def_id,
                                          WorkflowVersion.version < new.version)
            .order_by(WorkflowVersion.version.desc()))).scalars().first()
    return DiffOut(from_version=old.version if old else None, to_version=new.version,
                   **wgraph.differences(old.graph if old else None, new.graph))


async def _get_draft(db: AsyncSession, def_id: int, vid: int) -> WorkflowVersion:
    v = await db.get(WorkflowVersion, vid)
    if v is None or v.definition_id != def_id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.version_not_found", "Version not found")
    return v


@router.put("/workflows/{def_id}/versions/{vid}", response_model=WorkflowVersionOut)
async def update_version(
    def_id: int, vid: int, data: WorkflowVersionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    v = await _get_draft(db, def_id, vid)
    if v.status != WorkflowVersionStatus.draft:
        raise Error(status.HTTP_409_CONFLICT, "err.published_version_immutable",
                     "A published version is immutable")
    v.graph = data.graph
    if data.notes is not None:
        v.notes = data.notes
    await db.commit()
    await db.refresh(v)
    return v


@router.post("/workflows/{def_id}/versions/{vid}/validate", response_model=ValidateOut)
async def validate_version(
    def_id: int, vid: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    v = await _get_draft(db, def_id, vid)
    errors = engine.validate_graph(d.subject_kind, v.graph or {})
    return ValidateOut(ok=not errors, errors=errors)


@router.post("/workflows/{def_id}/versions/{vid}/publish", response_model=WorkflowVersionOut)
async def publish_version(
    def_id: int, vid: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    v = await _get_draft(db, def_id, vid)
    errors = engine.validate_graph(d.subject_kind, v.graph or {})
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"message": "Validation failed",
                                                          "errors": errors})
    v.status = WorkflowVersionStatus.published
    v.published_at = dt.datetime.now(tz=dt.timezone.utc)
    d.current_version_id = v.id
    await db.commit()
    await db.refresh(v)
    return v


@router.post("/workflows/{def_id}/versions/{vid}/rollback", response_model=WorkflowVersionOut)
async def rollback_version(
    def_id: int, vid: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Back to an earlier version, as a NEW version, not by bending the pointer.

    The old version stays untouched: running instances hang off their version, and the
    history should show that a rollback happened instead of looking as if the time in
    between never happened.
    """
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    old = await db.get(WorkflowVersion, vid)
    if old is None or old.definition_id != def_id:
        raise Error(status.HTTP_404_NOT_FOUND, "err.version_not_found", "Version not found")
    if old.status != WorkflowVersionStatus.published:
        raise Error(status.HTTP_409_CONFLICT, "err.rolling_back_only_possible_onto",
                     "Rolling back is only possible onto a published version")
    if d.current_version_id == vid:
        raise Error(status.HTTP_409_CONFLICT, "err.version_already_current_one",
                     "This version is already the current one")
    errors = engine.validate_graph(d.subject_kind, old.graph or {})
    if errors:
        # Can happen when the validation rules have grown stricter since.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"message": "This version no longer satisfies today's rules",
                             "errors": errors})
    new = WorkflowVersion(
        definition_id=def_id, version=await _next_version_number(db, def_id),
        graph=old.graph, status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc), created_by=user.id,
        notes=f"Zurückgerollt auf Fassung {old.version}",
    )
    db.add(new)
    await db.flush()
    d.current_version_id = new.id
    await db.commit()
    await db.refresh(new)
    return new


# ── Instanzen ────────────────────────────────────────────────────────────────

async def _require_subject_right(db: AsyncSession, user: User, issue_id: int | None,
                                 hardware_asset_id: int | None) -> None:
    """Check the rights on the artifact the instance is bound to.

    A flow is harmless as long as it touches nothing, and its subject is what it touches:
    setting states, writing fields, assigning agents. Therefore it is not the definition that
    decides what it may do, but the project of the artifact.
    """
    pids: list[int] = []
    if issue_id is not None:
        from ..models.ticket import Issue
        issue = await db.get(Issue, issue_id)
        if issue is None:
            raise Error(status.HTTP_404_NOT_FOUND, "err.ticket_not_found", "Ticket not found")
        pids.append(issue.project_id)
    if hardware_asset_id is not None:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, hardware_asset_id)
        if asset is None:
            raise Error(status.HTTP_404_NOT_FOUND, "err.unit_not_found", "Unit not found")
        if asset.project_id is not None:
            pids.append(asset.project_id)
    for pid in pids:
        project = await db.get(Project, pid)
        if project is None:
            continue
        access = await build_access(project, user, db)   # 404 when access is missing
        if not access.has_role(ProjectRole.member):
            raise Error(status.HTTP_403_FORBIDDEN, "err.you_have_no_rights_artifact",
                         "You have no rights on this artifact")


@router.post("/workflows/{def_id}/instances", response_model=InstanceOut, status_code=201)
async def start_instance(
    def_id: int, data: InstanceCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    if d.current_version_id is None:
        raise Error(status.HTTP_409_CONFLICT, "err.workflow_has_no_published_version",
                     "The workflow has no published version")
    # Starting instances: project membership (with a project-bound workflow)
    if d.project_id is not None:
        project = await db.get(Project, d.project_id)
        access = await build_access(project, user, db)
        if not access.has_role(ProjectRole.member):
            raise Error(status.HTTP_403_FORBIDDEN, "err.project_membership_required",
                         "Project membership is required")
    elif not d.slot and not (_belongs(d, user) or _is_admin(user)):
        raise Error(status.HTTP_403_FORBIDDEN, "err.flow_belongs_somebody_else",
                     "This flow belongs to somebody else")
    # A flow acts on its subject, so whoever starts it must have rights on that subject.
    await _require_subject_right(db, user, data.issue_id, data.hardware_asset_id)
    try:
        inst = await engine.start_workflow(
            db, d, subject_kind=data.subject_kind, issue_id=data.issue_id,
            hardware_asset_id=data.hardware_asset_id, context=data.context or {},
            actor_id=user.id, source="manual",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return await _load_instance_out(db, inst)


# IMPORTANT: declare the static route BEFORE /workflow-instances/{iid:int} (otherwise 422).
class DryrunIn(BaseModel):
    """What the flow is played through with, usually the example payload.

    `graph` is the state from the editor. Without it the trial would run against what is in
    the database, but while building you change things constantly without saving, and what
    should be checked is what you see in front of you.
    """
    context: dict = {}
    graph: dict | None = None


class DraftIn(BaseModel):
    """One sentence about what the flow should do, plus optionally the state on the canvas.

    If a graph is enclosed it is a rebuild ("put an approval in front of it"), otherwise a
    fresh drawing. Nothing is saved in either case: the draft lands in the editor.
    """
    description: str = Field(min_length=3, max_length=2000)
    graph: dict | None = None


@router.post("/workflows/{def_id}/draft")
async def workflow_draft(
    def_id: int, data: DraftIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Have a flow drawn from a description.

    Back comes the graph for the canvas, plus the errors of the validation (the draft is
    delivered even when something is still missing, because an almost finished graph is worth
    more than an error message) and a sentence or two about what it does.
    """
    from ..services import workflow_author

    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    try:
        return await workflow_author.compose(
            db, owner_id=user.id, description=data.description,
            subject_kind=d.subject_kind, existing=data.graph)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)[:300])
    except Exception as exc:  # noqa: BLE001
        raise Error(status.HTTP_502_BAD_GATEWAY, "err.draft_failed",
                     "The draft failed: {reason}", reason=str(exc)[:270])


@router.post("/workflows/{def_id}/dry-run", response_model=InstanceOut, status_code=201)
async def dryrun(
    def_id: int, data: DryrunIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Play the flow through without anything happening.

    The graph runs through completely, with real branches, real expressions and real loops,
    but every action only reports what it would do. Waiting points (approval, event, timer)
    do not stop but take their path, so that the whole flow becomes visible.

    What is taken is the **draft version**: what should be checked is what you have just
    built, not what was published last.
    """
    from ..models.workflow import WorkflowVersion

    d = await _get_def(db, def_id)
    await _require_write(db, user, d)

    graph = data.graph
    transient = None
    if graph is None:
        version = (await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.definition_id == d.id)
            .order_by(WorkflowVersion.version.desc()))).scalars().first()
        if version is None:
            raise Error(status.HTTP_409_CONFLICT, "err.flow_has_no_version_yet",
                         "The flow has no version yet")
        graph = version.graph or {}
    error = engine.validate_graph(d.subject_kind, graph)
    if error:
        raise Error(status.HTTP_422_UNPROCESSABLE_ENTITY, "err.flow_not_coherent",
                     "The flow is not coherent yet: {error}",
                     error="; ".join(error[:3]))

    if data.graph is not None:
        # A version for this moment only: the engine hangs every instance off a version, and
        # the editor state is not one yet. It disappears again after the run, because a trial
        # should leave no version history behind.
        last = (await db.execute(
            select(WorkflowVersion.version).where(WorkflowVersion.definition_id == d.id)
            .order_by(WorkflowVersion.version.desc()))).scalars().first() or 0
        transient = WorkflowVersion(definition_id=d.id, version=last + 1, graph=graph,
                                    status=WorkflowVersionStatus.draft, notes="Probelauf",
                                    created_by=user.id)
        db.add(transient)
        await db.flush()
        version = transient
    else:
        version = (await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.definition_id == d.id)
            .order_by(WorkflowVersion.version.desc()))).scalars().first()

    real_version, d.current_version_id = d.current_version_id, version.id
    try:
        inst = await engine.start_workflow(
            db, d, subject_kind=d.subject_kind,
            context={**(data.context or {}), engine.PROBE_KEY: True},
            actor_id=user.id, source="probelauf")
        result = await _load_instance_out(db, await db.get(WorkflowInstance, inst.id))
    finally:
        d.current_version_id = real_version
        await db.commit()

    if transient is not None:
        # First the run, then the version: the foreign key of the instance hangs off it.
        inst_row = await db.get(WorkflowInstance, inst.id)
        if inst_row is not None:
            await db.delete(inst_row)
        await db.flush()
        await db.delete(transient)
        await db.commit()
    return result


@router.get("/workflow-instances/tasks", response_model=list[WorkflowTaskLite])
async def my_tasks(
    assignee: str = Query("me"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Open steps (waiting, human_task|approval) of the current user."""
    if assignee != "me":
        raise Error(status.HTTP_400_BAD_REQUEST, "err.only_assignee_me_supported",
                     "Only assignee=me is supported")
    rows = (await db.execute(
        select(WorkflowStepRun, WorkflowInstance, WorkflowDefinition)
        .join(WorkflowInstance, WorkflowInstance.id == WorkflowStepRun.instance_id)
        .join(WorkflowDefinition, WorkflowDefinition.id == WorkflowInstance.definition_id)
        .where(
            WorkflowStepRun.status == WorkflowStepStatus.waiting,
            WorkflowStepRun.assignee_user_id == user.id,
            WorkflowStepRun.node_type.in_([WorkflowNodeType.human_task, WorkflowNodeType.approval]),
        )
        .order_by(WorkflowStepRun.entered_at))).all()

    out: list[WorkflowTaskLite] = []
    proj_cache: dict[int, Project | None] = {}
    for step, inst, d in rows:
        version = await db.get(WorkflowVersion, inst.version_id)
        graph = (version.graph if version else None) or {}
        node = next((n for n in (graph.get("nodes") or []) if n.get("id") == step.node_id), None)
        cfg = node_config(node) if node else {}
        project = None
        if inst.project_id is not None:
            if inst.project_id not in proj_cache:
                proj_cache[inst.project_id] = await db.get(Project, inst.project_id)
            project = proj_cache[inst.project_id]
        issue_key = None
        if inst.issue_id:
            from ..models.ticket import Issue
            issue = await db.get(Issue, inst.issue_id)
            issue_key = issue.key if issue else None
        out.append(WorkflowTaskLite(
            step_id=step.id, instance_id=inst.id, definition_name=d.name, node_id=step.node_id,
            node_type=step.node_type, node_config=cfg, project_id=inst.project_id,
            project_key=project.key if project else None, subject_kind=inst.subject_kind,
            issue_key=issue_key, entered_at=step.entered_at,
        ))
    return out


async def _get_instance(db: AsyncSession, iid: int) -> WorkflowInstance:
    inst = await db.get(WorkflowInstance, iid)
    if inst is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.instance_not_found", "Instance not found")
    return inst


@router.get("/workflow-instances", response_model=list[InstanceOut])
async def list_instances(
    subject: str = Query(..., description="issue:<id> oder hardware_asset:<id>"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    try:
        kind, raw = subject.split(":", 1)
        sid = int(raw)
    except ValueError:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.subject_form",
                     "subject has to be 'issue:<id>' or 'hardware_asset:<id>'")
    q = select(WorkflowInstance)
    if kind == "issue":
        q = q.where(WorkflowInstance.issue_id == sid)
    elif kind == "hardware_asset":
        q = q.where(WorkflowInstance.hardware_asset_id == sid)
    else:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.unknown_subject_kind",
                     "Unknown subject kind")
    rows = (await db.execute(q.order_by(WorkflowInstance.id.desc()))).scalars().all()
    out = []
    for inst in rows:
        try:
            await _instance_access(db, user, inst, ProjectRole.viewer)
        except HTTPException:
            continue
        out.append(await _load_instance_out(db, inst))
    return out


@router.get("/workflow-instances/{iid:int}", response_model=InstanceOut)
async def get_instance(
    iid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    inst = await _get_instance(db, iid)
    await _instance_access(db, user, inst, ProjectRole.viewer)
    return await _load_instance_out(db, inst)


def _write_context(inst: WorkflowInstance, node_id: str, form_data: dict | None) -> None:
    """Also store form inputs under context[node_id] (new dict, so JSON-dirty)."""
    if form_data is None:
        return
    ctx = dict(inst.context or {})
    ctx[node_id] = form_data
    inst.context = ctx


@router.post("/workflow-instances/{iid:int}/steps/{sid}/complete", response_model=InstanceOut)
async def complete_step(
    iid: int, sid: int, data: StepCompleteIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    inst = await _get_instance(db, iid)
    await _instance_access(db, user, inst, ProjectRole.member)
    step = await db.get(WorkflowStepRun, sid)
    if step is None or step.instance_id != iid:
        raise Error(status.HTTP_404_NOT_FOUND, "err.step_not_found", "Step not found")
    if step.node_type != WorkflowNodeType.human_task or step.status != WorkflowStepStatus.waiting:
        raise Error(status.HTTP_409_CONFLICT, "err.step_not_open_human_task",
                     "The step is not an open human_task")
    step.status = WorkflowStepStatus.done
    step.decision = "out"
    step.form_data = data.form_data
    step.completed_by = user.id
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    _write_context(inst, step.node_id, data.form_data)
    # Reactivate the token so that advance takes the "out" edge
    await _reactivate_token(db, iid, step.node_id)
    await db.commit()
    await engine.advance(iid)
    # Optional: assign the waiting human_task newly created by advance to next_assignee
    if data.next_assignee is not None:
        nxt = (await db.execute(
            select(WorkflowStepRun).where(
                WorkflowStepRun.instance_id == iid,
                WorkflowStepRun.status == WorkflowStepStatus.waiting,
                WorkflowStepRun.node_type == WorkflowNodeType.human_task,
            ).order_by(WorkflowStepRun.id.desc()))).scalars().first()
        if nxt is not None:
            nxt.assignee_user_id = data.next_assignee
            await db.commit()
    fresh = await _get_instance(db, iid)
    await db.refresh(fresh)
    return await _load_instance_out(db, fresh)


async def _reactivate_token(db: AsyncSession, iid: int, node_id: str) -> None:
    """Set a waiting token active again (advance then takes the matching edge)."""
    from ..models.enums import WorkflowInstanceStatus, WorkflowTokenState
    token = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == iid, WorkflowToken.state == WorkflowTokenState.waiting)
        .with_for_update())).scalars().first()
    if token is not None:
        token.state = WorkflowTokenState.active
        token.waiting_for = None
    inst = await db.get(WorkflowInstance, iid)
    if inst is not None:
        inst.status = WorkflowInstanceStatus.running


@router.post("/workflow-instances/{iid:int}/steps/{sid}/approve", response_model=InstanceOut)
async def approve_step(
    iid: int, sid: int, data: ApproveIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    return await _decide(db, user, iid, sid, "approved", data.reason)


@router.post("/workflow-instances/{iid:int}/steps/{sid}/reject", response_model=InstanceOut)
async def reject_step(
    iid: int, sid: int, data: RejectIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    return await _decide(db, user, iid, sid, "rejected", data.reason)


async def _decide(db: AsyncSession, user: User, iid: int, sid: int, decision: str,
                  reason: str | None) -> InstanceOut:
    inst = await _get_instance(db, iid)
    step = await db.get(WorkflowStepRun, sid)
    if step is None or step.instance_id != iid:
        raise Error(status.HTTP_404_NOT_FOUND, "err.step_not_found", "Step not found")
    if step.node_type != WorkflowNodeType.approval or step.status != WorkflowStepStatus.waiting:
        raise Error(status.HTTP_409_CONFLICT, "err.step_not_open_approval",
                     "The step is not an open approval")
    # Gate: node.config.gate == "ai_assign" → ai_assign; otherwise the configured role
    version = await db.get(WorkflowVersion, inst.version_id)
    graph = (version.graph if version else None) or {}
    node = next((n for n in (graph.get("nodes") or []) if n.get("id") == step.node_id), None)
    cfg = node_config(node) if node else {}
    await _require_approval_right(db, user, inst, cfg)

    step.status = WorkflowStepStatus.done
    step.decision = decision
    step.result = {"reason": reason} if reason else None
    step.completed_by = user.id
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    if inst.issue_id:
        from ..services.comments import add_system_comment
        verb = "genehmigt" if decision == "approved" else "abgelehnt"
        who = user.display_name or user.username
        txt = f"Workflow-Genehmigung „{step.node_id}“ {verb} von {who}"
        if reason:
            txt += f": {reason}"
        await add_system_comment(db, inst.issue_id, txt, author_label="Workflow")
    await _reactivate_token(db, iid, step.node_id)
    await db.commit()
    await engine.advance(iid)
    fresh = await _get_instance(db, iid)
    await db.refresh(fresh)
    return await _load_instance_out(db, fresh)


async def _require_approval_right(db: AsyncSession, user: User, inst: WorkflowInstance,
                                  cfg: dict) -> None:
    gate = cfg.get("gate")
    if inst.project_id is None:
        return  # project-less: no role check possible, so every logged-in user
    project = await db.get(Project, inst.project_id)
    access = await build_access(project, user, db)
    if gate == "ai_assign":
        if not access.ai_assign:
            raise Error(status.HTTP_403_FORBIDDEN, "err.ai_right_ai_assign_required",
                         "The AI right (ai_assign) is required")
        return
    role_name = cfg.get("role") or "member"
    try:
        minimum = ProjectRole(role_name)
    except ValueError:
        minimum = ProjectRole.member
    if not access.has_role(minimum):
        raise Error(status.HTTP_403_FORBIDDEN, "err.role_required",
                     "Role {role} is required", role=minimum.value)


@router.post("/workflow-instances/{iid:int}/cancel", response_model=InstanceOut)
async def cancel_instance(
    iid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    inst = await _get_instance(db, iid)
    await _instance_access(db, user, inst, ProjectRole.member)
    from ..models.enums import WorkflowInstanceStatus, WorkflowTokenState
    if inst.status in (WorkflowInstanceStatus.completed, WorkflowInstanceStatus.failed,
                       WorkflowInstanceStatus.cancelled):
        raise Error(status.HTTP_409_CONFLICT, "err.instance_has_already_ended",
                     "The instance has already ended")
    inst.status = WorkflowInstanceStatus.cancelled
    inst.finished_at = dt.datetime.now(tz=dt.timezone.utc)
    tokens = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == iid))).scalars().all()
    for t in tokens:
        t.state = WorkflowTokenState.consumed
    await db.commit()
    from ..core.redis import publish_event
    await publish_event(inst.project_id or 0, {"type": "workflow_update", "instance_id": inst.id,
                                               "status": inst.status.value})
    await db.refresh(inst)
    return await _load_instance_out(db, inst)
