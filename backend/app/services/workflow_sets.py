"""Process sets: which flow applies to which project, and how to adapt it.

A set holds at most one template per slot (ticket_lifecycle, acceptance,
hardware_procurement, ticket_intake). Projects reference a set; a copy of their own comes
into existence only when they customize it (copy on write). A change to the global or
personal set therefore takes effect immediately in every project without one of its own,
with no sync run and without breaking running instances, which are pinned to their version.

Resolution (`resolve_definition`):
    1. a project-owned, unarchived definition for this slot
    2. the set the project references (`Project.workflow_set_id`)
    3. the set of a project owner (`ProjectMember.role == owner`, lead first)
    4. the global default set (`is_builtin`)

Resetting archives the project copy instead of deleting it: instances hang off it
(`workflow_instances.definition_id` cascades), and history must not disappear.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import (
    ProjectRole, WorkflowSetScope, WorkflowSlot, WorkflowVersionStatus,
)
from ..models.project import Project, ProjectMember
from ..models.user import User
from ..models.workflow import WorkflowDefinition, WorkflowSet, WorkflowVersion

log = logging.getLogger("workflow_sets")

# Display name and subject per slot, the only place this is written down.
SLOT_META: dict[str, dict] = {
    WorkflowSlot.ticket_lifecycle.value: {
        "name": "AI ticket lifecycle",
        "description": "Planning → approval → implementation → acceptance of an assigned ticket.",
        "subject_kind": "issue",
    },
    WorkflowSlot.acceptance.value: {
        "name": "Acceptance & delivery",
        "description": "Clear the test environment away, merge the branch or open a PR, queue a deployment.",
        "subject_kind": "issue",
    },
    WorkflowSlot.hardware_procurement.value: {
        "name": "Hardware procurement",
        "description": "Order → receive → store → install a unit.",
        "subject_kind": "hardware_asset",
    },
    WorkflowSlot.ticket_intake.value: {
        "name": "Ticket intake",
        "description": "Look at an incoming report (webhook/mail), create a ticket and assign it.",
        "subject_kind": "standalone",
    },
}

BUILTIN_SET_KEY = "standard"


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# -- resolution --------------------------------------------------------------

async def owner_set_id(db: AsyncSession, project: Project) -> int | None:
    """Set of the first project owner who has one.

    The order is fixed on purpose: project lead first (when they are owner), then owner
    memberships by `id`. With several owners it would otherwise be unpredictable whose
    personal set applies.
    """
    members = (await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project.id,
                                    ProjectMember.role == ProjectRole.owner)
        .order_by(ProjectMember.id))).scalars().all()
    candidates = [m.user_id for m in members]
    if project.lead_user_id in candidates:
        candidates.remove(project.lead_user_id)
        candidates.insert(0, project.lead_user_id)
    for uid in candidates:
        user = await db.get(User, uid)
        if user is not None and user.workflow_set_id:
            return user.workflow_set_id
    return None


async def builtin_set(db: AsyncSession) -> WorkflowSet | None:
    return (await db.execute(
        select(WorkflowSet).where(WorkflowSet.is_builtin.is_(True))
        .order_by(WorkflowSet.id))).scalars().first()


async def effective_set(db: AsyncSession, project: Project | None) -> WorkflowSet | None:
    """The set that applies to this project, ignoring project-owned customizations."""
    if project is not None:
        if project.workflow_set_id:
            s = await db.get(WorkflowSet, project.workflow_set_id)
            if s is not None:
                return s
        sid = await owner_set_id(db, project)
        if sid:
            s = await db.get(WorkflowSet, sid)
            if s is not None:
                return s
    return await builtin_set(db)


async def set_definition(db: AsyncSession, set_id: int, slot: str) -> WorkflowDefinition | None:
    return (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.set_id == set_id,
            WorkflowDefinition.slot == slot,
            WorkflowDefinition.archived_at.is_(None),
        ))).scalar_one_or_none()


async def project_override(db: AsyncSession, project_id: int, slot: str,
                           issue_type_id: int | None = None) -> WorkflowDefinition | None:
    """Project-owned copy of a slot.

    First the one for exactly this issue type, otherwise the general one of the project. A
    bug may run a different lifecycle than a task without forcing a copy for every issue
    type.
    """
    if issue_type_id is not None:
        own = (await db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.project_id == project_id,
                WorkflowDefinition.slot == slot,
                WorkflowDefinition.issue_type_id == issue_type_id,
                WorkflowDefinition.archived_at.is_(None),
            ))).scalars().first()
        if own is not None:
            return own
    return (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.project_id == project_id,
            WorkflowDefinition.slot == slot,
            WorkflowDefinition.issue_type_id.is_(None),
            WorkflowDefinition.archived_at.is_(None),
        ))).scalars().first()


async def resolve_definition(db: AsyncSession, project_id: int | None, slot: str,
                             issue_type_id: int | None = None) -> WorkflowDefinition | None:
    """The definition that applies NOW: issue type, project copy, set, owner, global.

    `issue_type_id` only matters when the project has a flow of its own for that issue
    type, otherwise nothing changes.
    """
    slot = slot.value if isinstance(slot, WorkflowSlot) else str(slot)
    project = await db.get(Project, project_id) if project_id else None
    if project is not None:
        own = await project_override(db, project.id, slot, issue_type_id)
        if own is not None and own.enabled:
            return own
    s = await effective_set(db, project)
    if s is None:
        return None
    return await set_definition(db, s.id, slot)


async def resolve_source(db: AsyncSession, project_id: int, slot: str) -> dict:
    """Origin of a slot for the UI: where does the applicable flow come from?"""
    slot = slot.value if isinstance(slot, WorkflowSlot) else str(slot)
    project = await db.get(Project, project_id)
    own = await project_override(db, project_id, slot) if project else None
    if own is not None and own.enabled:
        return {"origin": "project", "definition": own, "set": None}
    s = await effective_set(db, project)
    if s is None:
        return {"origin": "none", "definition": None, "set": None}
    d = await set_definition(db, s.id, slot)
    origin = "builtin" if s.is_builtin else ("user" if s.scope == WorkflowSetScope.user else "global")
    return {"origin": origin, "definition": d, "set": s}


# -- customize and reset -----------------------------------------------------

async def _next_version_number(db: AsyncSession, def_id: int) -> int:
    from sqlalchemy import func
    n = (await db.execute(select(func.max(WorkflowVersion.version))
                          .where(WorkflowVersion.definition_id == def_id))).scalar()
    return (n or 0) + 1


async def _copy_definition(db: AsyncSession, source: WorkflowDefinition, *, project_id: int | None,
                           set_id: int | None, actor_id: int | None,
                           issue_type_id: int | None = None,
                           key: str | None = None, name: str | None = None,
                           publish: bool = True) -> WorkflowDefinition:
    """Copy a definition including its current graph. The copy starts as a published v1 so
    it runs right away, further editing creates a draft version as usual."""
    base = (await db.get(WorkflowVersion, source.current_version_id)
            if source.current_version_id else None)
    graph = (base.graph if base else None) or {"nodes": [], "edges": []}
    # The issue type binding belongs in the INSERT: the unique index applies at once,
    # and a binding set only afterwards would collide with the generic copy.
    copy = WorkflowDefinition(
        project_id=project_id, set_id=set_id, slot=source.slot,
        key=key or source.key, name=name or source.name,
        description=source.description, subject_kind=source.subject_kind,
        issue_type_id=issue_type_id, enabled=True, created_by=actor_id,
    )
    db.add(copy)
    await db.flush()
    version = WorkflowVersion(
        definition_id=copy.id, version=1, graph=graph,
        status=WorkflowVersionStatus.published if publish else WorkflowVersionStatus.draft,
        published_at=_now() if publish else None, created_by=actor_id,
        notes=f"Kopie aus „{source.name}“",
    )
    db.add(version)
    await db.flush()
    if publish:
        copy.current_version_id = version.id
    return copy


async def customize(db: AsyncSession, project: Project, slot: str, actor_id: int | None,
                    issue_type_id: int | None = None) -> WorkflowDefinition:
    """Create a project-owned copy of the applicable flow (copy on write).

    From then on the project is decoupled from the set: changes to the global or personal
    set no longer reach it until somebody calls `reset`. With `issue_type_id` the copy
    applies to that issue type only, so a bug gets a different lifecycle than a task while
    everything else keeps following the default.
    """
    slot = slot.value if isinstance(slot, WorkflowSlot) else str(slot)
    if issue_type_id is None:
        existing = await project_override(db, project.id, slot)
        if existing is not None and existing.issue_type_id is None:
            return existing
    else:
        existing = (await db.execute(select(WorkflowDefinition).where(
            WorkflowDefinition.project_id == project.id,
            WorkflowDefinition.slot == slot,
            WorkflowDefinition.issue_type_id == issue_type_id,
            WorkflowDefinition.archived_at.is_(None)))).scalars().first()
        if existing is not None:
            return existing
    source = await resolve_definition(db, project.id, slot, issue_type_id)
    if source is None:
        raise ValueError(f"There is no flow to copy for the slot '{slot}'")
    # The key is unique per project, so the copy for an issue type needs one of its own,
    # and the name should say at a glance who it applies to.
    key = name = None
    if issue_type_id is not None:
        from ..models.ticket import IssueType
        kind = await db.get(IssueType, issue_type_id)
        key = f"{source.key}-{issue_type_id}"
        name = f"{source.name} ({kind.name})" if kind else source.name
    copy = await _copy_definition(db, source, project_id=project.id, set_id=None,
                                  actor_id=actor_id, issue_type_id=issue_type_id,
                                  key=key, name=name)
    await db.commit()
    await db.refresh(copy)
    log.info("Project %s: slot %s adjusted (definition %s)", project.key, slot, copy.id)
    return copy


async def reset(db: AsyncSession, project: Project, slot: str,
                issue_type_id: int | None = None) -> bool:
    """Drop the project customization so the set applies again. False when there was none.

    With `issue_type_id` only the copy for that issue type is dropped, without it the
    general one. The copy is archived, not deleted: running instances point at it, and the
    step history of finished runs should stay readable.
    """
    slot = slot.value if isinstance(slot, WorkflowSlot) else str(slot)
    if issue_type_id is not None:
        own = (await db.execute(select(WorkflowDefinition).where(
            WorkflowDefinition.project_id == project.id,
            WorkflowDefinition.slot == slot,
            WorkflowDefinition.issue_type_id == issue_type_id,
            WorkflowDefinition.archived_at.is_(None)))).scalars().first()
    else:
        own = await project_override(db, project.id, slot)
        if own is not None and own.issue_type_id is not None:
            own = None          # only the generic copy is meant
    if own is None:
        return False
    own.archived_at = _now()
    own.enabled = False
    await db.commit()
    log.info("Project %s: slot %s reset (definition %s archived)",
             project.key, slot, own.id)
    return True


async def create_user_set(db: AsyncSession, user: User, name: str = "",
                          source_set_id: int | None = None) -> WorkflowSet:
    """Create a personal set as a full copy of a template set (the global default unless
    told otherwise) and activate it for that user right away."""
    source = (await db.get(WorkflowSet, source_set_id) if source_set_id
              else await builtin_set(db))
    s = WorkflowSet(
        scope=WorkflowSetScope.user, user_id=user.id, key=BUILTIN_SET_KEY,
        name=name or f"Prozesse von {user.display_name or user.username}",
        description="Persönlicher Standard — gilt für alle Projekte, in denen ich Owner bin.",
        created_by=user.id,
    )
    db.add(s)
    await db.flush()
    if source is not None:
        rows = (await db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.set_id == source.id,
                WorkflowDefinition.archived_at.is_(None)))).scalars().all()
        for d in rows:
            await _copy_definition(db, d, project_id=None, set_id=s.id, actor_id=user.id)
    user.workflow_set_id = s.id
    await db.commit()
    await db.refresh(s)
    return s


async def slot_overview(db: AsyncSession, project: Project) -> list[dict]:
    """All slots of a project with the applicable flow and its origin (for the process tab)."""
    out = []
    for slot in WorkflowSlot:
        info = await resolve_source(db, project.id, slot.value)
        d = info["definition"]
        meta = SLOT_META[slot.value]
        out.append({
            "slot": slot.value,
            "name": meta["name"],
            "description": meta["description"],
            "subject_kind": meta["subject_kind"],
            "origin": info["origin"],
            "set_id": info["set"].id if info["set"] else None,
            "set_name": info["set"].name if info["set"] else None,
            "definition_id": d.id if d else None,
            "definition_name": d.name if d else None,
            "published": bool(d and d.current_version_id),
            "customizable": True,
            "per_issue_type": await _per_casekind(db, project.id, slot.value),
        })
    return out


async def _per_casekind(db: AsyncSession, project_id: int, slot: str) -> list[dict]:
    """Copies that apply to one issue type only, the usual case for the lifecycle."""
    from ..models.ticket import IssueType
    rows = (await db.execute(
        select(WorkflowDefinition, IssueType)
        .join(IssueType, IssueType.id == WorkflowDefinition.issue_type_id)
        .where(WorkflowDefinition.project_id == project_id,
               WorkflowDefinition.slot == slot,
               WorkflowDefinition.archived_at.is_(None))
        .order_by(IssueType.order, IssueType.id))).all()
    return [{"issue_type_id": t.id, "issue_type_name": t.name, "definition_id": d.id,
             "published": bool(d.current_version_id)} for d, t in rows]
