import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..db import get_session
from ..models.hardware import (
    HardwareAsset, HardwareAssetStep, HardwareModel, HardwareWorkflow,
    HardwareWorkflowStep, Location,
)
from ..models.project import Project, ResourceGrant
from ..models.user import User
from ..schemas.hardware import (
    AssetIn, AssetOut, AssetUpdate, LocationIn, LocationOut, ModelIn, ModelOut,
    StepComplete, StepOut, WorkflowIn, WorkflowStepOut,
)
from ..models.enums import GlobalRole, GrantLevel, ProjectRole, ResourceType
from .deps import Access, build_access, get_current_user, get_project_access, require_role

router = APIRouter(tags=["hardware"])

DEFAULT_STEPS = ["Bestellen", "Erhalten", "Einlagern", "Einbauen"]

GRANT_RANK = {GrantLevel.view: 0, GrantLevel.manage: 1}


async def _location_grant_level(loc_id: int, user: User, db: AsyncSession) -> GrantLevel | None:
    """Highest grant for a location, directly OR inherited from an ancestor location with
    recursive=True (a caretaker gets a grant on the pump house, which then applies to the
    masts below it as well). The grant has to belong to the current project of the location
    (project_id scope), as defence in depth against cross-project grants (see the validation
    in add_resource_grant)."""
    best: GrantLevel | None = None
    loc = await db.get(Location, loc_id)
    seen: set[int] = set()
    first = True
    while loc is not None and loc.id not in seen:
        seen.add(loc.id)
        g = (
            await db.execute(
                select(ResourceGrant).where(
                    ResourceGrant.user_id == user.id,
                    ResourceGrant.resource_type == ResourceType.location,
                    ResourceGrant.resource_id == loc.id,
                    ResourceGrant.project_id == loc.project_id,
                )
            )
        ).scalar_one_or_none()
        if g is not None and (first or g.recursive):
            if best is None or GRANT_RANK[g.level] > GRANT_RANK[best]:
                best = g.level
        first = False
        loc = await db.get(Location, loc.parent_id) if loc.parent_id is not None else None
    return best


async def _can_view_location(loc: Location, user: User, db: AsyncSession) -> bool:
    if user.global_role == GlobalRole.admin:
        return True
    if loc.project_id is None:
        return True  # global or stored location: visible to all logged-in users
    proj = await db.get(Project, loc.project_id)
    if proj is not None:
        try:
            await build_access(proj, user, db)
            return True
        except HTTPException:
            pass
    return (await _location_grant_level(loc.id, user, db)) is not None


async def _require_location_manage(loc: Location, user: User, db: AsyncSession) -> None:
    """Creating, changing and deleting a location: project member (maintainer+) OR a manage
    grant. Project-less locations stay open to every logged-in user as before."""
    if loc.project_id is None:
        return
    proj = await db.get(Project, loc.project_id)
    if proj is None:
        raise Fehler(400, "err.project_does_not_exist", "The project does not exist")
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        access = None
    if access is not None and access.has_role(ProjectRole.maintainer):
        return
    level = await _location_grant_level(loc.id, user, db)
    if level == GrantLevel.manage:
        return
    raise Fehler(403, "err.no_access_location", "No access to this location")


async def _compute_full_path(loc: Location, db: AsyncSession) -> str:
    parts = [loc.name]
    seen = {loc.id}
    parent_id = loc.parent_id
    while parent_id is not None and parent_id not in seen:
        parent = await db.get(Location, parent_id)
        if parent is None:
            break
        parts.append(parent.name)
        seen.add(parent.id)
        parent_id = parent.parent_id
    return " / ".join(reversed(parts))


# ---------- Lagerorte ----------

@router.get("/locations", response_model=list[LocationOut])
async def list_locations(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Location).order_by(Location.full_path))).scalars().all()
    if user.global_role == GlobalRole.admin:
        return list(rows)
    return [loc for loc in rows if await _can_view_location(loc, user, db)]


@router.post("/locations", response_model=LocationOut, status_code=201)
async def create_location(
    data: LocationIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    if data.project_id is not None:
        proj = await db.get(Project, data.project_id)
        if proj is None:
            raise Fehler(400, "err.project_does_not_exist", "The project does not exist")
        if not (await build_access(proj, user, db)).has_role(ProjectRole.maintainer):
            raise Fehler(403, "err.no_access_project", "No access to this project")
    loc = Location(
        name=data.name, type=data.type, parent_id=data.parent_id,
        project_id=data.project_id, notes=data.notes,
    )
    db.add(loc)
    await db.flush()
    loc.full_path = await _compute_full_path(loc, db)
    await db.commit()
    await db.refresh(loc)
    return loc


@router.put("/locations/{loc_id}", response_model=LocationOut)
async def update_location(
    loc_id: int, data: LocationIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    loc = await db.get(Location, loc_id)
    if loc is None:
        raise Fehler(404, "err.location_not_found", "Location not found")
    await _require_location_manage(loc, user, db)
    # Moving into another project additionally requires maintainer+ in the TARGET project;
    # otherwise a maintainer of project A could simply move a location into project B.
    if data.project_id != loc.project_id:
        if data.project_id is not None:
            target_proj = await db.get(Project, data.project_id)
            if target_proj is None:
                raise Fehler(400, "err.project_does_not_exist", "The project does not exist")
            if not (await build_access(target_proj, user, db)).has_role(ProjectRole.maintainer):
                raise Fehler(403, "err.no_access_target_project",
                             "No access to the target project")
    loc.name, loc.type, loc.parent_id = data.name, data.type, data.parent_id
    loc.project_id, loc.notes = data.project_id, data.notes
    await db.flush()
    loc.full_path = await _compute_full_path(loc, db)
    # Kinder-Pfade neu berechnen
    children = (await db.execute(select(Location).where(Location.parent_id == loc.id))).scalars().all()
    for ch in children:
        ch.full_path = await _compute_full_path(ch, db)
    await db.commit()
    await db.refresh(loc)
    return loc


@router.delete("/locations/{loc_id}", status_code=204)
async def delete_location(
    loc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    loc = await db.get(Location, loc_id)
    if loc is None:
        raise Fehler(404, "err.location_not_found", "Location not found")
    await _require_location_manage(loc, user, db)
    await db.delete(loc)
    await db.commit()


# ---------- Katalog (Modelle) ----------

@router.get("/hardware/models", response_model=list[ModelOut])
async def list_models(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(HardwareModel).order_by(HardwareModel.name))).scalars().all()
    return list(rows)


@router.post("/hardware/models", response_model=ModelOut, status_code=201)
async def create_model(
    data: ModelIn, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    m = HardwareModel(**data.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.delete("/hardware/models/{model_id}", status_code=204)
async def delete_model(
    model_id: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    m = await db.get(HardwareModel, model_id)
    if m is None:
        raise Fehler(404, "err.model_not_found", "Model not found")
    n = (await db.execute(select(func.count(HardwareAsset.id)).where(
        HardwareAsset.model_id == model_id))).scalar() or 0
    if n:
        raise Fehler(409, "err.model_still_has_units",
                     "{anzahl} unit(s) use this model, remove them first", anzahl=n)
    await db.delete(m)
    await db.commit()


# ---------- Exemplare (Assets) ----------

async def _asset_grant_level(
    asset_id: int, user: User, db: AsyncSession, project_id: int | None = None
) -> GrantLevel | None:
    """The grant has to belong to the current project of the asset (project_id scope) when
    known, as defence in depth against cross-project grants."""
    q = select(ResourceGrant).where(
        ResourceGrant.user_id == user.id,
        ResourceGrant.resource_type == ResourceType.asset,
        ResourceGrant.resource_id == asset_id,
    )
    if project_id is not None:
        q = q.where(ResourceGrant.project_id == project_id)
    g = (await db.execute(q)).scalar_one_or_none()
    return g.level if g is not None else None


async def _can_view_asset(asset: HardwareAsset, user: User, db: AsyncSession) -> bool:
    if user.global_role == GlobalRole.admin:
        return True
    if asset.project_id is None:
        return True  # stock or storage: visible to all logged-in users
    proj = await db.get(Project, asset.project_id)
    if proj is not None:
        try:
            await build_access(proj, user, db)
            return True
        except HTTPException:
            pass
    if (await _asset_grant_level(asset.id, user, db, asset.project_id)) is not None:
        return True
    if asset.location_id is not None:
        loc = await db.get(Location, asset.location_id)
        if loc is not None and (await _location_grant_level(loc.id, user, db)) is not None:
            return True
    return False


@router.get("/hardware/assets", response_model=list[AssetOut])
async def list_assets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    project_id: int | None = None,
    location_id: int | None = None,
):
    q = select(HardwareAsset)
    if project_id is not None:
        q = q.where(HardwareAsset.project_id == project_id)
    if location_id is not None:
        q = q.where(HardwareAsset.location_id == location_id)
    rows = (await db.execute(q.order_by(HardwareAsset.id))).scalars().all()
    if user.global_role == GlobalRole.admin:
        return list(rows)
    return [a for a in rows if await _can_view_asset(a, user, db)]


async def _require_project_member(project_id: int | None, user: User, db: AsyncSession) -> None:
    """Project bound hardware only for members of the project (member+)."""
    if project_id is None:
        return  # stock or storage without a project
    proj = await db.get(Project, project_id)
    if proj is None:
        raise Fehler(400, "err.project_does_not_exist", "The project does not exist")
    if not (await build_access(proj, user, db)).has_role(ProjectRole.member):
        raise Fehler(403, "err.no_access_project", "No access to this project")


async def _require_asset_manage(asset: HardwareAsset, user: User, db: AsyncSession) -> None:
    """Changing and deleting an existing unit: project member (member+) OR a manage grant on
    the asset OR a manage grant on its location (the caretaker case)."""
    if asset.project_id is None:
        return
    proj = await db.get(Project, asset.project_id)
    if proj is None:
        raise Fehler(400, "err.project_does_not_exist", "The project does not exist")
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        access = None
    if access is not None and access.has_role(ProjectRole.member):
        return
    if (await _asset_grant_level(asset.id, user, db, asset.project_id)) == GrantLevel.manage:
        return
    if asset.location_id is not None:
        loc = await db.get(Location, asset.location_id)
        if loc is not None and (await _location_grant_level(loc.id, user, db)) == GrantLevel.manage:
            return
    raise Fehler(403, "err.no_access_unit", "No access to this unit")


@router.post("/hardware/assets", response_model=AssetOut, status_code=201)
async def create_asset(
    data: AssetIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    if await db.get(HardwareModel, data.model_id) is None:
        raise Fehler(400, "err.model_does_not_exist", "The model does not exist")
    await _require_project_member(data.project_id, user, db)
    a = HardwareAsset(**data.model_dump())
    db.add(a)
    await db.flush()
    # Every unit is an artifact: the common identity and state come into being immediately;
    # otherwise the object would hang in the air without an artifact row until the next start.
    from ..services.artifacts import ensure_for_asset
    await ensure_for_asset(db, a)
    await db.commit()
    await db.refresh(a)
    return a


@router.put("/hardware/assets/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: int, data: AssetUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    await _require_asset_manage(a, user, db)
    fields = data.model_dump(exclude_unset=True)
    # Moving into another project additionally requires member+ in the TARGET project;
    # otherwise a project member could simply move a unit into a foreign project.
    if "project_id" in fields and fields["project_id"] != a.project_id:
        await _require_project_member(fields["project_id"], user, db)
    for field, value in fields.items():
        setattr(a, field, value)
    # Pull the artifact row along (title, project, state): it is the common view.
    from ..services.artifacts import sync_asset_artifact
    await sync_asset_artifact(db, a)
    await db.commit()
    await db.refresh(a)
    return a


@router.delete("/hardware/assets/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    await _require_asset_manage(a, user, db)
    await db.delete(a)
    await db.commit()


@router.get("/hardware/assets/{asset_id}/issues")
async def asset_issues(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    """Tickets that hang off this unit (ABC-25), the opposite direction to Issue.asset_id."""
    from ..models.ticket import Issue, WorkflowStatus
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    if not await _can_view_asset(a, user, db):
        raise Fehler(403, "err.no_access_unit", "No access to this unit")
    rows = (
        await db.execute(
            select(Issue).where(Issue.asset_id == asset_id).order_by(Issue.number.desc())
        )
    ).scalars().all()
    out = []
    for i in rows:
        st = await db.get(WorkflowStatus, i.status_id)
        out.append({
            "key": i.key, "summary": i.summary, "status": st.name if st else "",
            "archived": i.archived,
        })
    return out


# ---------- Beschaffungs-Workflow + Schritte ----------

async def _ensure_workflow(project_id: int, db: AsyncSession) -> HardwareWorkflow:
    wf = (
        await db.execute(select(HardwareWorkflow).where(HardwareWorkflow.project_id == project_id))
    ).scalar_one_or_none()
    if wf is None:
        wf = HardwareWorkflow(project_id=project_id)
        db.add(wf)
        await db.flush()
        for i, name in enumerate(DEFAULT_STEPS):
            db.add(HardwareWorkflowStep(workflow_id=wf.id, name=name, order=i))
        await db.commit()
        await db.refresh(wf)
    return wf


@router.get("/projects/{project_id}/hardware-workflow", response_model=list[WorkflowStepOut])
async def get_workflow(
    access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session),
):
    """Procurement steps of the project (creates the default set when none exists yet)."""
    wf = await _ensure_workflow(access.project.id, db)
    rows = (await db.execute(
        select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id)
        .order_by(HardwareWorkflowStep.order))).scalars().all()
    return [WorkflowStepOut(id=s.id, name=s.name, order=s.order, assignee=s.assignee or {})
            for s in rows]


@router.put("/projects/{project_id}/hardware-workflow", status_code=204)
async def set_workflow(
    project_id: int, data: WorkflowIn,
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    wf = await _ensure_workflow(access.project.id, db)
    old = (
        await db.execute(select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id))
    ).scalars().all()
    for s in old:
        await db.delete(s)
    for i, step in enumerate(data.steps):
        db.add(HardwareWorkflowStep(workflow_id=wf.id, name=step.name, order=step.order or i,
                                    assignee=step.assignee or {}))
    await db.commit()
    # If a generic procurement definition already exists, publish a new version from it;
    # otherwise the process would keep running with the old steps (ABC-26).
    from ..services.hardware_workflow import sync_hardware_definition
    await sync_hardware_definition(db, access.project.id, access.user.id)


async def _instantiate_steps(asset: HardwareAsset, db: AsyncSession) -> None:
    if asset.project_id is None:
        return
    existing = (
        await db.execute(select(HardwareAssetStep).where(HardwareAssetStep.asset_id == asset.id))
    ).scalars().first()
    if existing is not None:
        return
    wf = await _ensure_workflow(asset.project_id, db)
    steps = (
        await db.execute(
            select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id)
            .order_by(HardwareWorkflowStep.order)
        )
    ).scalars().all()
    for s in steps:
        # Prefill the responsible person from the template when a concrete user stands there
        # (role and context assignment is only resolved by the workflow engine), ABC-26.
        spec = s.assignee or {}
        assignee_id = spec.get("user_id") if spec.get("mode") == "user" else None
        db.add(HardwareAssetStep(asset_id=asset.id, name=s.name, order=s.order,
                                 assignee_id=assignee_id))
    await db.commit()


@router.get("/hardware/assets/{asset_id}/steps", response_model=list[StepOut])
async def asset_steps(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    if not await _can_view_asset(a, user, db):
        raise Fehler(403, "err.no_access_unit", "No access to this unit")
    await _instantiate_steps(a, db)
    rows = (
        await db.execute(
            select(HardwareAssetStep).where(HardwareAssetStep.asset_id == asset_id)
            .order_by(HardwareAssetStep.order)
        )
    ).scalars().all()
    return list(rows)


# ---------- Procurement on the generic workflow engine (additive) ----------

@router.post("/projects/{project_id}/hardware-workflow/definition")
async def ensure_hw_workflow_definition(
    access: Access = Depends(require_role(ProjectRole.maintainer)),
    db: AsyncSession = Depends(get_session),
):
    """Creates (idempotently) the published "hardware procurement" workflow definition of the
    project from the existing steps. Visible and extendable in the process editor afterwards."""
    from ..services.hardware_workflow import ensure_hardware_definition
    d = await ensure_hardware_definition(db, access.project.id, access.user.id)
    return {"definition_id": d.id, "key": d.key, "current_version_id": d.current_version_id}


@router.post("/hardware/assets/{asset_id}/workflow")
async def start_asset_workflow(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Starts (idempotently) a procurement workflow instance for a unit."""
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    await _require_project_member(a.project_id, user, db)
    if a.project_id is None:
        raise Fehler(409, "err.supply_without_project_has_no_workflow",
                     "A supply or stock without a project has no procurement workflow")
    from ..services.hardware_workflow import start_hardware_instance
    inst = await start_hardware_instance(db, a, user.id)
    return {"instance_id": inst.id, "status": inst.status.value, "definition_id": inst.definition_id}


@router.post("/hardware/assets/{asset_id}/steps/{step_id}/complete", response_model=list[StepOut])
async def complete_step(
    asset_id: int, step_id: int, data: StepComplete,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    step = await db.get(HardwareAssetStep, step_id)
    if step is None or step.asset_id != asset_id:
        raise Fehler(404, "err.step_not_found", "Step not found")
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise Fehler(404, "err.unit_not_found", "Unit not found")
    await _require_asset_manage(a, user, db)
    step.status = "DONE"
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    step.completed_by_id = user.id
    if data.note:
        step.note = data.note
    # Handover: assign the next open step to the responsible person
    if data.next_assignee is not None:
        nxt = (
            await db.execute(
                select(HardwareAssetStep).where(
                    HardwareAssetStep.asset_id == asset_id,
                    HardwareAssetStep.status == "PENDING",
                    HardwareAssetStep.order > step.order,
                ).order_by(HardwareAssetStep.order)
            )
        ).scalars().first()
        if nxt is not None:
            nxt.assignee_id = data.next_assignee
    await db.commit()
    rows = (
        await db.execute(
            select(HardwareAssetStep).where(HardwareAssetStep.asset_id == asset_id)
            .order_by(HardwareAssetStep.order)
        )
    ).scalars().all()
    return list(rows)
