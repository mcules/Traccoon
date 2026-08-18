"""Events, the loose way to start a process.

Every trigger used to name a flow explicitly (a webhook with `workflow_definition_id`, a
job with the same column). Hanging a second flow on the same occasion meant changing the
trigger. Here it works the other way round: something happens ("issue.created",
"mail.received", a name of your own), and the flow decides whether it listens, through the
trigger on its start node:

    start.config.trigger = {
        "event": "issue.created",     # what to listen for
        "project_id": 27,             # optional: only for this project
        "filter": {...JSONLogic...},  # optional: only when the payload matches
    }

That way any number of flows hang off one event, and a project can put its own next to
them without rewiring anything.
"""
from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowSubjectKind
from ..models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from .jsonlogic import JsonLogicError, safe_eval
from .workflow_engine import node_type, start_workflow

log = logging.getLogger("events")

# Events Traccoon reports itself. Names of your own are allowed (webhook, API), this list
# only feeds the picker in the editor.
BUILTIN_EVENTS: list[tuple[str, str]] = [
    ("issue.created", "Ticket angelegt"),
    ("issue.assigned", "Agent zugewiesen"),
    ("issue.status_changed", "Board-Spalte gewechselt"),
    ("issue.agent_status_changed", "KI-Zustand gewechselt"),
    ("issue.done", "Ticket fertig"),
    ("comment.added", "Kommentar geschrieben"),
    ("hardware.status_changed", "Beschaffungs-Status gewechselt"),
    ("mail.received", "E-Mail eingegangen"),
    ("deployment.finished", "Deployment abgeschlossen"),
]


def trigger_of(graph: dict) -> dict | None:
    """Trigger-Angaben am Start-Knoten eines Graphen (oder None)."""
    for n in (graph or {}).get("nodes") or []:
        if node_type(n) == "start":
            cfg = (n.get("data") or {}).get("config") or n.get("config") or {}
            t = cfg.get("trigger")
            return t if isinstance(t, dict) and t.get("event") else None
    return None


async def listeners(db: AsyncSession, event: str, project_id: int | None) -> list[WorkflowDefinition]:
    """Published flows whose start node listens for this event.

    This reads the published definitions instead of a separate trigger table, so the graph
    stays the single truth and cannot drift apart from an index. At this scale (dozens of
    flows) that is cheap.
    """
    rows = (await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.enabled.is_(True),
            WorkflowDefinition.archived_at.is_(None),
            WorkflowDefinition.current_version_id.isnot(None),
            # Ein projektgebundener Ablauf reagiert nur auf sein eigenes Projekt.
            or_(WorkflowDefinition.project_id.is_(None),
                WorkflowDefinition.project_id == project_id) if project_id
            else WorkflowDefinition.project_id.is_(None),
        ))).scalars().all()

    treffer = []
    for d in rows:
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = trigger_of(version.graph if version else {})
        if not t or t.get("event") != event:
            continue
        # A project set on the trigger narrows it further.
        gewuenscht = t.get("project_id")
        if gewuenscht and int(gewuenscht) != (project_id or 0):
            continue
        if not await _darf_hoeren(db, d, project_id):
            continue
        treffer.append(d)
    return treffer


async def _darf_hoeren(db: AsyncSession, d: WorkflowDefinition, project_id: int | None) -> bool:
    """May this flow react to an event FROM THIS PROJECT?

    A free-standing flow belongs to a person but hangs off no project. Without this check
    it would run on every ticket event, including projects its owner is not allowed to see.
    The shipped set (`slot`) and project-bound flows are not affected: they are under the
    supervision of the project or an admin anyway.
    """
    if d.project_id is not None or d.slot or project_id is None:
        return True
    if d.created_by is None:
        # Legacy: only an admin could create such flows back then, so they are already
        # vetted. Newly created ones always carry their owner.
        return True
    from ..models.enums import GlobalRole
    from ..models.project import Project
    from ..models.user import User
    from ..api.deps import build_access

    besitzer = await db.get(User, d.created_by)
    if besitzer is None:
        return False
    if besitzer.global_role == GlobalRole.admin:
        return True
    projekt = await db.get(Project, project_id)
    if projekt is None:
        return False
    try:
        await build_access(projekt, besitzer, db)   # wirft bei fehlendem Zugriff
    except Exception:                              # noqa: BLE001, 403/404 means not listening
        log.info("Ablauf %s hört nicht auf Projekt %s: Eigentümer ohne Zugriff",
                 d.key, project_id)
        return False
    return True


async def emit(db: AsyncSession, event: str, *, project_id: int | None = None,
               payload: dict | None = None, issue_id: int | None = None,
               hardware_asset_id: int | None = None, actor_id: int | None = None,
               source_ref: str | None = None) -> list[int]:
    """Report an event and start every matching flow. Returns the instance ids.

    A failing flow breaks nothing: an event is a notice, not an order, and the caller
    (creating a ticket, writing a comment) must not depend on it.
    """
    ctx = {"event": {"name": event, "project_id": project_id}, **(payload or {})}
    gestartet: list[int] = []
    for d in await listeners(db, event, project_id):
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = trigger_of(version.graph if version else {}) or {}
        regel = t.get("filter")
        if regel:
            try:
                if not safe_eval(regel, ctx):
                    continue
            except JsonLogicError as e:
                log.warning("Ablauf %s: Trigger-Filter fehlerhaft (%s)", d.key, e)
                continue
        # Doppelte Zustellung desselben Ereignisses erzeugt keinen zweiten Lauf.
        if source_ref:
            dup = (await db.execute(select(WorkflowInstance).where(
                WorkflowInstance.definition_id == d.id,
                WorkflowInstance.source == f"event:{event}",
                WorkflowInstance.source_ref == source_ref))).scalars().first()
            if dup is not None:
                continue
        try:
            sk = d.subject_kind
            inst = await start_workflow(
                db, d, subject_kind=sk,
                issue_id=issue_id if sk == WorkflowSubjectKind.issue else None,
                hardware_asset_id=(hardware_asset_id
                                   if sk == WorkflowSubjectKind.hardware_asset else None),
                context=ctx, actor_id=actor_id, source=f"event:{event}", source_ref=source_ref,
            )
            gestartet.append(inst.id)
            log.info("Ereignis %s → Ablauf %s gestartet (Instanz %s)", event, d.key, inst.id)
        except Exception:  # noqa: BLE001, a broken flow must not disturb the trigger
            log.exception("Ereignis %s: Ablauf %s konnte nicht starten", event, d.key)
    return gestartet
