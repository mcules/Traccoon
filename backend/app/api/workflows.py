"""REST-Router der Workflow-Engine: Definitionen, Versionen (Editor), Instanzen, Aufgaben.

Zugriffsmodell:
- Definitionen schreiben/publishen: Projekt-Rolle owner|maintainer ODER access.ai_assign
  (globale Vorlagen ohne Projekt: nur Admin).
- Instanzen starten/Schritte: Projekt-Mitgliedschaft (member+); projektlose Instanzen: authentifiziert.
- approve/reject: access.ai_assign wenn node.config.gate=="ai_assign", sonst die konfigurierte Rolle.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import (
    ProjectRole, WorkflowNodeType, WorkflowStepStatus, WorkflowSubjectKind,
    WorkflowVersionStatus,
)
from ..models.project import Project
from ..models.user import User
from ..models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowStepRun, WorkflowToken, WorkflowVersion,
)
from ..schemas.workflow import (
    ApproveIn, InstanceCreate, InstanceOut, RejectIn, StepCompleteIn, StepRunOut, TokenLite,
    ValidateOut, WorkflowDefinitionCreate, WorkflowDefinitionOut, WorkflowDefinitionUpdate,
    WorkflowTaskLite, WorkflowVersionOut, WorkflowVersionUpdate,
)
from ..services import workflow_engine as engine
from ..services.workflow_engine import node_config
from .deps import build_access, get_current_user

router = APIRouter(tags=["workflows"])


# ── interne Helfer ───────────────────────────────────────────────────────────

async def _require_def_write(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Schreibrecht auf eine Definition: Projekt owner|maintainer ODER ai_assign; global → Admin."""
    if project_id is None:
        from ..models.enums import GlobalRole
        if user.global_role != GlobalRole.admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Globale Workflow-Vorlagen darf nur ein Admin ändern")
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
    access = await build_access(project, user, db)  # 404 bei Fremdprojekt
    if not (access.has_role(ProjectRole.maintainer) or access.ai_assign):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Rolle owner|maintainer oder KI-Recht (ai_assign) erforderlich")


async def _require_project_read(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Lese-/Nutzungs-Zugriff auf ein Projekt (globale/projektlose Objekte: frei für Angemeldete)."""
    if project_id is None:
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
    await build_access(project, user, db)  # wirft 404 bei fehlendem Zugriff


async def _get_def(db: AsyncSession, def_id: int) -> WorkflowDefinition:
    d = await db.get(WorkflowDefinition, def_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow nicht gefunden")
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
    """Zugriff auf eine Instanz + (falls Projekt) Access-Objekt zurück."""
    if inst.project_id is None:
        return None
    project = await db.get(Project, inst.project_id)
    if project is None:
        return None
    access = await build_access(project, user, db)
    if not access.has_role(minimum):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Rolle {minimum.value} erforderlich")
    return access


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
    rows = (await db.execute(q.order_by(WorkflowDefinition.id))).scalars().all()
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
        raise HTTPException(status.HTTP_409_CONFLICT, "Key im Projekt bereits vergeben")
    d = WorkflowDefinition(
        project_id=data.project_id, key=data.key, name=data.name,
        description=data.description or "", subject_kind=data.subject_kind, created_by=user.id,
    )
    db.add(d)
    await db.flush()
    # Leere Draft-Version v1 anlegen
    v1 = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                         status=WorkflowVersionStatus.draft, created_by=user.id)
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


@router.put("/workflows/{def_id}", response_model=WorkflowDefinitionOut)
async def update_workflow(
    def_id: int, data: WorkflowDefinitionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_def_write(db, user, d.project_id)
    if data.name is not None:
        d.name = data.name
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
    await _require_def_write(db, user, d.project_id)
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
    """Aktuelle Draft-Version für den Editor. Existiert keine, wird eine neue Draft aus der
    veröffentlichten current_version geklont (oder leer angelegt)."""
    d = await _get_def(db, def_id)
    await _require_def_write(db, user, d.project_id)
    draft = (await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == def_id,
            WorkflowVersion.status == WorkflowVersionStatus.draft)
        .order_by(WorkflowVersion.version.desc()))).scalars().first()
    if draft is not None:
        return draft
    base = await db.get(WorkflowVersion, d.current_version_id) if d.current_version_id else None
    graph = (base.graph if base else None) or {"nodes": [], "edges": []}
    draft = WorkflowVersion(
        definition_id=def_id, version=await _next_version_number(db, def_id),
        graph=graph, status=WorkflowVersionStatus.draft, created_by=user.id,
        notes=(f"Klon aus v{base.version}" if base else ""),
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def _get_draft(db: AsyncSession, def_id: int, vid: int) -> WorkflowVersion:
    v = await db.get(WorkflowVersion, vid)
    if v is None or v.definition_id != def_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version nicht gefunden")
    return v


@router.put("/workflows/{def_id}/versions/{vid}", response_model=WorkflowVersionOut)
async def update_version(
    def_id: int, vid: int, data: WorkflowVersionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_def_write(db, user, d.project_id)
    v = await _get_draft(db, def_id, vid)
    if v.status != WorkflowVersionStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Veröffentlichte Version ist unveränderlich")
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
    await _require_def_write(db, user, d.project_id)
    v = await _get_draft(db, def_id, vid)
    errors = engine.validate_graph(d.subject_kind, v.graph or {})
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"message": "Validierung fehlgeschlagen",
                                                          "errors": errors})
    v.status = WorkflowVersionStatus.published
    v.published_at = dt.datetime.now(tz=dt.timezone.utc)
    d.current_version_id = v.id
    await db.commit()
    await db.refresh(v)
    return v


# ── Instanzen ────────────────────────────────────────────────────────────────

@router.post("/workflows/{def_id}/instances", response_model=InstanceOut, status_code=201)
async def start_instance(
    def_id: int, data: InstanceCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    if d.current_version_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow hat keine veröffentlichte Version")
    # Instanzen starten: Projekt-Mitgliedschaft (bei projektgebundenem Workflow)
    if d.project_id is not None:
        project = await db.get(Project, d.project_id)
        access = await build_access(project, user, db)
        if not access.has_role(ProjectRole.member):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Projekt-Mitgliedschaft erforderlich")
    try:
        inst = await engine.start_workflow(
            db, d, subject_kind=data.subject_kind, issue_id=data.issue_id,
            hardware_asset_id=data.hardware_asset_id, context=data.context or {},
            actor_id=user.id, source="manual",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return await _load_instance_out(db, inst)


# WICHTIG: statische Route VOR /workflow-instances/{iid:int} deklarieren (sonst 422).
@router.get("/workflow-instances/tasks", response_model=list[WorkflowTaskLite])
async def my_tasks(
    assignee: str = Query("me"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Offene Schritte (waiting, human_task|approval) des aktuellen Users."""
    if assignee != "me":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur assignee=me unterstützt")
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "subject muss 'issue:<id>' oder 'hardware_asset:<id>' sein")
    q = select(WorkflowInstance)
    if kind == "issue":
        q = q.where(WorkflowInstance.issue_id == sid)
    elif kind == "hardware_asset":
        q = q.where(WorkflowInstance.hardware_asset_id == sid)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannter subject-Typ")
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
    """Formular-Eingaben zusätzlich unter context[node_id] ablegen (neues dict → JSON-dirty)."""
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schritt nicht gefunden")
    if step.node_type != WorkflowNodeType.human_task or step.status != WorkflowStepStatus.waiting:
        raise HTTPException(status.HTTP_409_CONFLICT, "Schritt ist keine offene human_task")
    step.status = WorkflowStepStatus.done
    step.decision = "out"
    step.form_data = data.form_data
    step.completed_by = user.id
    step.completed_at = dt.datetime.now(tz=dt.timezone.utc)
    _write_context(inst, step.node_id, data.form_data)
    # Token reaktivieren, damit advance die "out"-Kante nimmt
    await _reactivate_token(db, iid, step.node_id)
    await db.commit()
    await engine.advance(iid)
    # Optional: den durch advance neu entstandenen wartenden human_task dem next_assignee zuweisen
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
    """Wartendes Token wieder aktiv setzen (advance nimmt danach die passende Kante)."""
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schritt nicht gefunden")
    if step.node_type != WorkflowNodeType.approval or step.status != WorkflowStepStatus.waiting:
        raise HTTPException(status.HTTP_409_CONFLICT, "Schritt ist keine offene Genehmigung")
    # Gate: node.config.gate == "ai_assign" → ai_assign; sonst konfigurierte Rolle
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
        return  # projektlos: keine Rollenprüfung möglich → jeder Angemeldete
    project = await db.get(Project, inst.project_id)
    access = await build_access(project, user, db)
    if gate == "ai_assign":
        if not access.ai_assign:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "KI-Recht (ai_assign) erforderlich")
        return
    role_name = cfg.get("role") or "member"
    try:
        minimum = ProjectRole(role_name)
    except ValueError:
        minimum = ProjectRole.member
    if not access.has_role(minimum):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Rolle {minimum.value} erforderlich")


@router.post("/workflow-instances/{iid:int}/cancel", response_model=InstanceOut)
async def cancel_instance(
    iid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    inst = await _get_instance(db, iid)
    await _instance_access(db, user, inst, ProjectRole.member)
    from ..models.enums import WorkflowInstanceStatus, WorkflowTokenState
    if inst.status in (WorkflowInstanceStatus.completed, WorkflowInstanceStatus.failed,
                       WorkflowInstanceStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "Instanz ist bereits beendet")
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
