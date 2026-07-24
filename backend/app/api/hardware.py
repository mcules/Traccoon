import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.hardware import (
    HardwareAsset, HardwareAssetStep, HardwareModel, HardwareWorkflow,
    HardwareWorkflowStep, Location,
)
from ..models.project import Project, ResourceGrant
from ..models.user import User
from ..schemas.hardware import (
    AssetIn, AssetOut, AssetUpdate, LocationIn, LocationOut, ModelIn, ModelOut,
    StepComplete, StepOut, WorkflowIn,
)
from ..models.enums import GlobalRole, GrantLevel, ProjectRole, ResourceType
from .deps import Access, build_access, get_current_user, get_project_access, require_role

router = APIRouter(tags=["hardware"])

DEFAULT_STEPS = ["Bestellen", "Erhalten", "Einlagern", "Einbauen"]

GRANT_RANK = {GrantLevel.view: 0, GrantLevel.manage: 1}


async def _user_matches_role_grant(g: ResourceGrant, user: User, db: AsyncSession) -> bool:
    """Greift eine rollenbasierte Freigabe für diesen User? Prüft die effektive (auch
    geerbte, s. Teil A) Rolle des Users im Kontext-Projekt der Freigabe gegen die
    Mindest-Rolle des Grants."""
    if g.role is None:
        return False
    proj = await db.get(Project, g.project_id)
    if proj is None:
        return False
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        return False
    return access.has_role(g.role)


async def _matching_grants(rt: ResourceType, resource_id: int, user: User, db: AsyncSession) -> list[ResourceGrant]:
    """Alle Freigaben (user- ODER rollenbasiert), die für diesen User auf dieses Objekt greifen."""
    rows = (
        await db.execute(
            select(ResourceGrant).where(
                ResourceGrant.resource_type == rt,
                ResourceGrant.resource_id == resource_id,
            )
        )
    ).scalars().all()
    out = []
    for g in rows:
        if g.user_id is not None:
            if g.user_id == user.id:
                out.append(g)
        elif await _user_matches_role_grant(g, user, db):
            out.append(g)
    return out


async def _location_grant_level(loc_id: int, user: User, db: AsyncSession) -> GrantLevel | None:
    """Höchste Freigabe für einen Ort — direkt ODER geerbt von einem Vorfahren-Ort
    mit recursive=True (Wart bekommt Grant aufs Wasserhäuschen → gilt auch für Masten drunter).
    Berücksichtigt sowohl user- als auch rollenbasierte Freigaben."""
    best: GrantLevel | None = None
    loc = await db.get(Location, loc_id)
    seen: set[int] = set()
    first = True
    while loc is not None and loc.id not in seen:
        seen.add(loc.id)
        for g in await _matching_grants(ResourceType.location, loc.id, user, db):
            if first or g.recursive:
                if best is None or GRANT_RANK[g.level] > GRANT_RANK[best]:
                    best = g.level
        first = False
        loc = await db.get(Location, loc.parent_id) if loc.parent_id is not None else None
    return best


async def _can_view_location(loc: Location, user: User, db: AsyncSession) -> bool:
    if user.global_role == GlobalRole.admin:
        return True
    if loc.project_id is None:
        return True  # globaler/lagernder Ort — sichtbar für alle angemeldeten Nutzer
    proj = await db.get(Project, loc.project_id)
    if proj is not None:
        try:
            await build_access(proj, user, db)
            return True
        except HTTPException:
            pass
    return (await _location_grant_level(loc.id, user, db)) is not None


async def _require_target_project_maintainer(project_id: int | None, user: User, db: AsyncSession) -> None:
    """Validiert das ZIEL-Projekt einer Zuordnung (create/move): existiert es, und ist der
    Aufrufer dort mind. maintainer? Verhindert, dass ein Nutzer mit Rechten in Projekt A
    (oder nur Grant auf den alten Ort/Asset) ein Objekt in ein fremdes Projekt B verschiebt."""
    if project_id is None:
        return
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(400, "Projekt existiert nicht")
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        raise HTTPException(403, "Kein Zugriff auf dieses Projekt")
    if not access.has_role(ProjectRole.maintainer):
        raise HTTPException(403, "Kein Zugriff auf dieses Projekt")


async def _require_location_manage(loc: Location, user: User, db: AsyncSession) -> None:
    """Anlegen/Ändern/Löschen eines Orts: Projekt-Mitglied (maintainer+) ODER manage-Grant.
    Projektlose Orte bleiben wie bisher für jeden angemeldeten Nutzer offen."""
    if loc.project_id is None:
        return
    proj = await db.get(Project, loc.project_id)
    if proj is None:
        raise HTTPException(400, "Projekt existiert nicht")
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        access = None
    if access is not None and access.has_role(ProjectRole.maintainer):
        return
    level = await _location_grant_level(loc.id, user, db)
    if level == GrantLevel.manage:
        return
    raise HTTPException(403, "Kein Zugriff auf diesen Ort")


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
            raise HTTPException(400, "Projekt existiert nicht")
        try:
            access = await build_access(proj, user, db)
        except HTTPException:
            raise HTTPException(403, "Kein Zugriff auf dieses Projekt")
        if not access.has_role(ProjectRole.maintainer):
            raise HTTPException(403, "Kein Zugriff auf dieses Projekt")
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
        raise HTTPException(404, "Ort nicht gefunden")
    await _require_location_manage(loc, user, db)
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
        raise HTTPException(404, "Ort nicht gefunden")
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
        raise HTTPException(404, "Modell nicht gefunden")
    n = (await db.execute(select(func.count(HardwareAsset.id)).where(
        HardwareAsset.model_id == model_id))).scalar() or 0
    if n:
        raise HTTPException(409, f"{n} Exemplar(e) nutzen dieses Modell — erst entfernen")
    await db.delete(m)
    await db.commit()


# ---------- Exemplare (Assets) ----------

async def _asset_grant_level(asset_id: int, user: User, db: AsyncSession) -> GrantLevel | None:
    """Höchste Freigabe (user- oder rollenbasiert) für dieses Exemplar."""
    best: GrantLevel | None = None
    for g in await _matching_grants(ResourceType.asset, asset_id, user, db):
        if best is None or GRANT_RANK[g.level] > GRANT_RANK[best]:
            best = g.level
    return best


async def _can_view_asset(asset: HardwareAsset, user: User, db: AsyncSession) -> bool:
    if user.global_role == GlobalRole.admin:
        return True
    if asset.project_id is None:
        return True  # Vorrat/Lager — sichtbar für alle angemeldeten Nutzer
    proj = await db.get(Project, asset.project_id)
    if proj is not None:
        try:
            await build_access(proj, user, db)
            return True
        except HTTPException:
            pass
    if (await _asset_grant_level(asset.id, user, db)) is not None:
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
    """Projekt-gebundene Hardware nur für Mitglieder des Projekts (member+)."""
    if project_id is None:
        return  # Vorrat/Lager ohne Projekt
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(400, "Projekt existiert nicht")
    if not (await build_access(proj, user, db)).has_role(ProjectRole.member):
        raise HTTPException(403, "Kein Zugriff auf dieses Projekt")


async def _require_asset_manage(asset: HardwareAsset, user: User, db: AsyncSession) -> None:
    """Ändern/Löschen eines bestehenden Exemplars: Projekt-Mitglied (member+) ODER
    manage-Grant auf das Asset ODER manage-Grant auf dessen Ort (Wart-Fall)."""
    if asset.project_id is None:
        return
    proj = await db.get(Project, asset.project_id)
    if proj is None:
        raise HTTPException(400, "Projekt existiert nicht")
    try:
        access = await build_access(proj, user, db)
    except HTTPException:
        access = None
    if access is not None and access.has_role(ProjectRole.member):
        return
    if (await _asset_grant_level(asset.id, user, db)) == GrantLevel.manage:
        return
    if asset.location_id is not None:
        loc = await db.get(Location, asset.location_id)
        if loc is not None and (await _location_grant_level(loc.id, user, db)) == GrantLevel.manage:
            return
    raise HTTPException(403, "Kein Zugriff auf dieses Exemplar")


@router.post("/hardware/assets", response_model=AssetOut, status_code=201)
async def create_asset(
    data: AssetIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    if await db.get(HardwareModel, data.model_id) is None:
        raise HTTPException(400, "Modell existiert nicht")
    await _require_project_member(data.project_id, user, db)
    a = HardwareAsset(**data.model_dump())
    db.add(a)
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
        raise HTTPException(404, "Exemplar nicht gefunden")
    await _require_asset_manage(a, user, db)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(a, field, value)
    await db.commit()
    await db.refresh(a)
    return a


@router.delete("/hardware/assets/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise HTTPException(404, "Exemplar nicht gefunden")
    await _require_asset_manage(a, user, db)
    await db.delete(a)
    await db.commit()


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


@router.get("/projects/{project_id}/hardware-workflow", response_model=list[StepOut])
async def get_workflow(
    access: Access = Depends(get_project_access), db: AsyncSession = Depends(get_session),
):
    """Beschaffungsschritte des Projekts (legt den Standard-Satz an, falls noch keiner existiert)."""
    wf = await _ensure_workflow(access.project.id, db)
    rows = (await db.execute(
        select(HardwareWorkflowStep).where(HardwareWorkflowStep.workflow_id == wf.id)
        .order_by(HardwareWorkflowStep.order))).scalars().all()
    # Als StepOut ausgeben (Vorlagenschritte haben keine Zuständigen/Status)
    return [StepOut(id=s.id, name=s.name, order=s.order, assignee_id=None,
                    status="", note=None, completed_at=None, completed_by_id=None) for s in rows]


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
        db.add(HardwareWorkflowStep(workflow_id=wf.id, name=step.name, order=step.order or i))
    await db.commit()


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
        db.add(HardwareAssetStep(asset_id=asset.id, name=s.name, order=s.order))
    await db.commit()


@router.get("/hardware/assets/{asset_id}/steps", response_model=list[StepOut])
async def asset_steps(
    asset_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)
):
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise HTTPException(404, "Exemplar nicht gefunden")
    if not await _can_view_asset(a, user, db):
        raise HTTPException(403, "Kein Zugriff auf dieses Exemplar")
    await _instantiate_steps(a, db)
    rows = (
        await db.execute(
            select(HardwareAssetStep).where(HardwareAssetStep.asset_id == asset_id)
            .order_by(HardwareAssetStep.order)
        )
    ).scalars().all()
    return list(rows)


@router.post("/hardware/assets/{asset_id}/steps/{step_id}/complete", response_model=list[StepOut])
async def complete_step(
    asset_id: int, step_id: int, data: StepComplete,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    step = await db.get(HardwareAssetStep, step_id)
    if step is None or step.asset_id != asset_id:
        raise HTTPException(404, "Schritt nicht gefunden")
    a = await db.get(HardwareAsset, asset_id)
    if a is None:
        raise HTTPException(404, "Exemplar nicht gefunden")
    await _require_asset_manage(a, user, db)
    step.status = "DONE"
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    step.completed_by_id = user.id
    if data.note:
        step.note = data.note
    # Übergabe: nächsten offenen Schritt der zuständigen Person zuordnen
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
