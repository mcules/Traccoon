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
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from ..services import workflow_engine as engine
from ..services import workflow_sets as sets
from ..services.workflow_engine import node_config
from .deps import build_access, get_current_user

router = APIRouter(tags=["workflows"])


# ── interne Helfer ───────────────────────────────────────────────────────────

def _ist_admin(user: User) -> bool:
    from ..models.enums import GlobalRole
    return user.global_role == GlobalRole.admin


def _gehoert(d, user: User) -> bool:
    """Ist das ein eigener, freier Ablauf dieses Menschen?

    Frei heißt: an kein Projekt und an keinen Slot gebunden. Wer so einen anlegt, ist sein
    Eigentümer (`created_by`) — er allein sieht, ändert und startet ihn. Ohne diese Grenze
    wäre „eigener Ablauf" ein Widerspruch: die Definition liegt projektlos in derselben
    Tabelle wie die ausgelieferten Vorlagen und stünde damit allen offen.
    """
    return d.project_id is None and not d.slot and d.created_by == user.id


async def _require_def_write(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Schreibrecht auf eine Definition: Projekt owner|maintainer ODER ai_assign.

    Projektlos darf **jeder Angemeldete** anlegen — ein eigener Ablauf ist kein Adminrecht.
    Was er darf, entscheidet sich nicht hier, sondern dort, wo er wirkt: gebundene Artefakte
    und Ereignis-Auslöser werden gegen die Rechte seines Eigentümers geprüft
    (`_require_subjekt_recht`, `events.listeners`).
    """
    if project_id is None:
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
    access = await build_access(project, user, db)  # 404 bei Fremdprojekt
    if not (access.has_role(ProjectRole.maintainer) or access.ai_assign):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Rolle owner|maintainer oder KI-Recht (ai_assign) erforderlich")


async def _require_write(db: AsyncSession, user: User, d) -> None:
    """Schreibrecht auf eine konkrete Definition.

    Gehört sie zu einem Prozess-Satz, entscheidet der Satz (persönlich = Eigentümer,
    global = Admin); ein freier Ablauf gehört seinem Ersteller; sonst gelten die
    Projekt-Regeln.
    """
    if d.set_id:
        return await _require_set_write(db, user, await _get_set(db, d.set_id))
    if d.project_id is None and not d.slot:
        if not (_gehoert(d, user) or _ist_admin(user)):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Dieser Ablauf gehört jemand anderem")
        return
    await _require_def_write(db, user, d.project_id)


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


@router.get("/workflow-events")
async def workflow_events(user: User = Depends(get_current_user)):
    """Ereignisse, die Traccoon selbst meldet — Vorschlagsliste für den Auslöser."""
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
    """Ereignis von Hand melden. Startet jeden Ablauf, dessen Start-Knoten darauf hört."""
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
    """Abstand (px) für „Anordnen" im Editor. Lesbar für alle — gesetzt wird er vom Admin
    unter `PUT /admin/workflow-layout`."""
    from ..services.appsettings import get_layout_gap
    return {"gap": await get_layout_gap(db)}


@router.get("/workflow-context-fields")
async def workflow_context_fields(user: User = Depends(get_current_user)):
    """Welche Felder im Kontext stehen — je Auslöser, Aktion und Knotentyp.

    Der Editor baut daraus die Auswahl an einer Verzweigung. Vorher war das ein leeres
    Textfeld: man musste den Pfad kennen, und ein Tippfehler fiel erst auf, wenn der Zweig
    im Betrieb nie griff.
    """
    from ..services.workflow_context import katalog
    from ..services.workflow_expr import katalog as filter_katalog
    return {**katalog(), "filter": filter_katalog()}


@router.get("/workflows/{def_id}/webhook")
async def workflow_webhook_lesen(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Die eingehende Adresse dieses Ablaufs (oder `null`, wenn er keine hat)."""
    d = await _get_def(db, def_id)
    await _require_project_read(db, user, d.project_id)
    return await _webhook_von(db, d)


@router.post("/workflows/{def_id}/webhook", status_code=201)
async def workflow_webhook_anlegen(
    def_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Diesem Ablauf eine eigene Adresse geben, unter der ihn ein fremdes System anstößt.

    Nicht jedes System spricht MCP, und die wenigsten kennen Traccoons Ereignisse — aber
    einen Webhook kann fast jedes schicken. Bisher musste man ihn in den Einstellungen
    anlegen und dort den Ablauf auswählen: die Quelle stand am anderen Ende, im Ablauf
    selbst war sie unsichtbar. Jetzt entsteht sie dort, wo sie hingehört.

    Der Aufruf ist idempotent — ein zweiter gibt dieselbe Adresse zurück.
    """
    import secrets as _secrets
    import uuid as _uuid

    from ..models.ops import WebhookSub

    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    vorhanden = await _webhook_von(db, d)
    if vorhanden:
        return vorhanden

    sub = WebhookSub(
        public_id=str(_uuid.uuid4()), owner_user_id=user.id,
        route=(d.key or f"ablauf-{d.id}")[:120], secret=_secrets.token_hex(24),
        mode="workflow", project_id=d.project_id, workflow_definition_id=d.id,
        context_map={},   # ohne Abbildung landet die ganze Nutzlast im Kontext
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return await _webhook_von(db, d)


async def _webhook_von(db: AsyncSession, d) -> dict | None:
    """Der Webhook, der genau diesen Ablauf startet (samt Adresse und Geheimnis)."""
    from ..config import settings
    from ..models.ops import WebhookSub

    sub = (await db.execute(select(WebhookSub).where(
        WebhookSub.mode == "workflow",
        WebhookSub.workflow_definition_id == d.id).order_by(WebhookSub.id))).scalars().first()
    if sub is None:
        return None
    # Dieselbe Basis wie bei Einladungslinks — ohne sie bleibt der Pfad relativ, damit
    # niemand eine URL kopiert, die von außen ins Leere zeigt.
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
    """Die MCP-Werkzeuge DIESES Menschen — Auswahl für den Knoten „Werkzeug aufrufen".

    Bewusst die eigenen: ein Ablauf ruft später über den MCPJungle-Zugang seines
    Eigentümers, nicht über einen globalen. Was hier nicht steht, kann er auch nicht rufen.
    """
    from ..services.workflow_tools import werkzeuge
    return await werkzeuge(db, user.id)


# ── Prozess-Sätze ────────────────────────────────────────────────────────────

async def _get_set(db: AsyncSession, set_id: int):
    from ..models.workflow import WorkflowSet
    s = await db.get(WorkflowSet, set_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prozess-Satz nicht gefunden")
    return s


async def _require_set_write(db: AsyncSession, user: User, s) -> None:
    """Globale Sätze darf nur ein Admin ändern, persönliche nur ihr Eigentümer."""
    from ..models.enums import GlobalRole, WorkflowSetScope
    if s.scope == WorkflowSetScope.user:
        if s.user_id != user.id and user.global_role != GlobalRole.admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Fremder persönlicher Prozess-Satz")
        return
    if user.global_role != GlobalRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Globale Prozess-Sätze darf nur ein Admin ändern")


@router.get("/workflow-sets", response_model=list[WorkflowSetOut])
async def list_sets(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Sichtbare Sätze: alle globalen plus die eigenen (Admins sehen alle)."""
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
    """Belegung eines Satzes — je Slot die hinterlegte Vorlage."""
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
    """Eigenen Standard-Satz anlegen (Kopie des globalen) — gilt danach für alle Projekte,
    in denen ich die Owner-Rolle habe und die keinen eigenen Satz gewählt haben."""
    if user.workflow_set_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Es gibt bereits einen persönlichen Satz")
    return await sets.create_user_set(db, user, data.name, data.source_set_id)


@router.delete("/me/workflow-set", status_code=204)
async def drop_my_set(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Persönlichen Satz aufgeben → meine Projekte folgen wieder dem globalen Standard."""
    from ..models.workflow import WorkflowSet
    sid = user.workflow_set_id
    user.workflow_set_id = None
    if sid:
        s = await db.get(WorkflowSet, sid)
        if s is not None and not s.is_builtin:
            await db.delete(s)
    await db.commit()


# ── Slots eines Projekts (anpassen / zurücksetzen) ───────────────────────────

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
    """Projekt-eigene Kopie des geltenden Ablaufs anlegen (copy-on-write).

    Mit `issue_type_id` gilt die Kopie nur für diese Vorgangsart — alle anderen Tickets des
    Projekts folgen weiter dem Satz.
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
    """Anpassung verwerfen → wieder der Satz gilt. Laufende Instanzen bleiben unberührt.

    Mit `issue_type_id` betrifft es nur den Ablauf dieser Vorgangsart.
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
    """Satz wählen, dem dieses Projekt folgt (NULL = Owner-Satz bzw. globaler Standard)."""
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
    # Zurückgesetzte Projekt-Kopien bleiben zwar in der DB (Instanzen hängen dran),
    # gehören aber nicht mehr in die Auswahl.
    q = q.where(WorkflowDefinition.archived_at.is_(None))
    rows = (await db.execute(q.order_by(WorkflowDefinition.id))).scalars().all()
    # Freie Abläufe sind privat: sie stehen projektlos in derselben Tabelle wie die
    # ausgelieferten Vorlagen, gehören aber einem Menschen. Ohne diesen Filter sähe jeder
    # die Abläufe aller anderen.
    if not _ist_admin(user):
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
        raise HTTPException(status.HTTP_409_CONFLICT, "Key im Projekt bereits vergeben")
    d = WorkflowDefinition(
        project_id=data.project_id, key=data.key, name=data.name,
        description=data.description or "", subject_kind=data.subject_kind, created_by=user.id,
    )
    db.add(d)
    await db.flush()
    # Version 1 mit Start und Ende — nicht leer. Eine leere Fläche sagt niemandem, wo er
    # anfangen soll; mit den beiden Enden steht das Gerüst, und der erste Schritt kommt
    # dazwischen. (Fiel beim Durchklicken auf: ein frischer Ablauf hatte keinen einzigen
    # Knoten, nicht einmal einen Start.)
    v1 = WorkflowVersion(
        definition_id=d.id, version=1, status=WorkflowVersionStatus.draft, created_by=user.id,
        graph={
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0},
                 "data": {"config": {"label": "Auslöser"}}},
                {"id": "ende", "type": "end", "position": {"x": 0, "y": 260},
                 "data": {"config": {"label": "Fertig", "outcome": "completed"}}},
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


@router.put("/workflows/{def_id}", response_model=WorkflowDefinitionOut)
async def update_workflow(
    def_id: int, data: WorkflowDefinitionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
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
    """Aktuelle Draft-Version für den Editor. Existiert keine, wird eine neue Draft aus der
    veröffentlichten current_version geklont (oder leer angelegt)."""
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
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
    await _require_write(db, user, d)
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
    await _require_write(db, user, d)
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


@router.post("/workflows/{def_id}/versions/{vid}/rollback", response_model=WorkflowVersionOut)
async def rollback_version(
    def_id: int, vid: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Auf eine frühere Fassung zurück — als NEUE Version, nicht durch Umbiegen.

    Die alte Version bleibt unangetastet: laufende Instanzen hängen an ihrer Version, und
    die Historie soll zeigen, dass zurückgerollt wurde, statt so auszusehen, als wäre die
    Zwischenzeit nie passiert.
    """
    d = await _get_def(db, def_id)
    await _require_write(db, user, d)
    alt = await db.get(WorkflowVersion, vid)
    if alt is None or alt.definition_id != def_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version nicht gefunden")
    if alt.status != WorkflowVersionStatus.published:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Nur auf eine veröffentlichte Fassung lässt sich zurückrollen")
    if d.current_version_id == vid:
        raise HTTPException(status.HTTP_409_CONFLICT, "Diese Fassung ist bereits die aktuelle")
    errors = engine.validate_graph(d.subject_kind, alt.graph or {})
    if errors:
        # Kann passieren, wenn die Prüfregeln seither strenger wurden.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"message": "Diese Fassung erfüllt die heutigen Regeln nicht mehr",
                             "errors": errors})
    neu = WorkflowVersion(
        definition_id=def_id, version=await _next_version_number(db, def_id),
        graph=alt.graph, status=WorkflowVersionStatus.published,
        published_at=dt.datetime.now(tz=dt.timezone.utc), created_by=user.id,
        notes=f"Zurückgerollt auf Fassung {alt.version}",
    )
    db.add(neu)
    await db.flush()
    d.current_version_id = neu.id
    await db.commit()
    await db.refresh(neu)
    return neu


# ── Instanzen ────────────────────────────────────────────────────────────────

async def _require_subjekt_recht(db: AsyncSession, user: User, issue_id: int | None,
                                 hardware_asset_id: int | None) -> None:
    """Rechte an dem Artefakt prüfen, an das die Instanz gebunden wird.

    Ein Ablauf ist harmlos, solange er nichts anfasst — sein Subjekt fasst er an: Zustände
    setzen, Felder schreiben, Agenten zuweisen. Deshalb entscheidet nicht die Definition
    darüber, was er darf, sondern das Projekt des Artefakts.
    """
    pids: list[int] = []
    if issue_id is not None:
        from ..models.ticket import Issue
        issue = await db.get(Issue, issue_id)
        if issue is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket nicht gefunden")
        pids.append(issue.project_id)
    if hardware_asset_id is not None:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, hardware_asset_id)
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Exemplar nicht gefunden")
        if asset.project_id is not None:
            pids.append(asset.project_id)
    for pid in pids:
        project = await db.get(Project, pid)
        if project is None:
            continue
        access = await build_access(project, user, db)   # 404 bei fehlendem Zugriff
        if not access.has_role(ProjectRole.member):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Auf dieses Artefakt hast du keine Rechte")


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
    elif not d.slot and not (_gehoert(d, user) or _ist_admin(user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Dieser Ablauf gehört jemand anderem")
    # Ein Ablauf wirkt auf sein Subjekt — wer ihn startet, muss darauf Rechte haben.
    await _require_subjekt_recht(db, user, data.issue_id, data.hardware_asset_id)
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
