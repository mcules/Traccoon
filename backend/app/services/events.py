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
    ("issue.created", "a ticket was created"),
    ("issue.assigned", "Agent zugewiesen"),
    ("issue.status_changed", "Board-Spalte gewechselt"),
    ("issue.agent_status_changed", "KI-Zustand gewechselt"),
    ("issue.done", "Ticket fertig"),
    ("comment.added", "Kommentar geschrieben"),
    ("hardware.status_changed", "Beschaffungs-Status gewechselt"),
    ("mail.received", "E-Mail eingegangen"),
    ("bug.reported", "Meldung eingegangen"),
    ("assistant.session_created", "Unterhaltung angelegt"),
    ("assistant.session_closed", "Unterhaltung geschlossen"),
    ("assistant.session_deleted", "Unterhaltung geloescht"),
    ("deployment.finished", "Deployment abgeschlossen"),
    # Reported by the configuration audit itself (`services/agentshield.py`), one per finding
    # plus one for the run. They used to come from outside, from a collector with a token —
    # now the audit is part of the house and reports like everything else does.
    ("agentshield.finding.new", "a configuration finding appeared"),
    ("agentshield.finding.fixed", "a configuration finding is gone"),
    ("agentshield.audit.finished", "the configuration audit finished"),
]

# What an event carries that is worth filtering on, written out per event so the editor can
# offer it instead of having somebody guess the path. The same reasoning as with the action
# fields in the editor: without this table only an empty JSONLogic box remains, in which the
# right names have to be guessed — and a wrong guess is QUIET, because a filter that matches
# nothing simply never starts the flow, which looks exactly like "nothing happened".
#
# Anything not in here is still reachable through `filter`. This is the shortcut for the
# handful of fields one actually filters on, not a fence around the rest.
EVENT_FIELDS: dict[str, list[dict]] = {
    "agentshield.finding.new": [
        {"path": "finding.severity", "label": "Severity",
         "options": [("critical", "critical"), ("high", "high"), ("medium", "medium"),
                     ("low", "low"), ("info", "info")]},
    ],
    "agentshield.finding.fixed": [
        {"path": "finding.severity", "label": "Severity",
         "options": [("critical", "critical"), ("high", "high"), ("medium", "medium"),
                     ("low", "low"), ("info", "info")]},
    ],
    "bug.reported": [
        {"path": "report.kind", "label": "Art der Meldung",
         "options": [("bug", "Etwas ist kaputt"), ("feature", "Ein Wunsch"),
                     ("question", "Eine Frage")]},
    ],
}


def _selection_matches(where: dict, ctx: dict) -> bool:
    """Does the payload match what was ticked in the editor? (`where` on the trigger.)

    Deliberately beside `filter` and not inside it. A dropdown that wrote JSONLogic would
    have to read it back to know what is ticked, and an expression somebody wrote by hand
    would be overwritten by the next click. So: `where` belongs to the editor, `filter`
    belongs to the person, and both have to be true.

    A field without a selection means no restriction — one that was never touched must not
    silence the flow.
    """
    from .workflow_expr import evaluate

    for path, wanted in (where or {}).items():
        if isinstance(wanted, (str, int)):
            wanted = [wanted]
        if not wanted:
            continue
        value = evaluate(str(path), ctx)
        if str(value) not in {str(w) for w in wanted}:
            return False
    return True


def trigger_of(graph: dict) -> dict | None:
    """The trigger settings on the start node of a graph (or None)."""
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
            # A project bound flow reacts only to its own project.
            or_(WorkflowDefinition.project_id.is_(None),
                WorkflowDefinition.project_id == project_id) if project_id
            else WorkflowDefinition.project_id.is_(None),
        ))).scalars().all()

    hits = []
    for d in rows:
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = trigger_of(version.graph if version else {})
        if not t or t.get("event") != event:
            continue
        # A project set on the trigger narrows it further.
        wanted = t.get("project_id")
        if wanted and int(wanted) != (project_id or 0):
            continue
        if not await _may_listen(db, d, project_id):
            continue
        hits.append(d)
    return hits


async def _may_listen(db: AsyncSession, d: WorkflowDefinition, project_id: int | None) -> bool:
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

    owner = await db.get(User, d.created_by)
    if owner is None:
        return False
    if owner.global_role == GlobalRole.admin:
        return True
    project = await db.get(Project, project_id)
    if project is None:
        return False
    try:
        await build_access(project, owner, db)   # raises when access is missing
    except Exception:                              # noqa: BLE001, 403/404 means not listening
        log.info("Flow %s does not listen to project %s: the owner has no access",
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
    started: list[int] = []
    for d in await listeners(db, event, project_id):
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = trigger_of(version.graph if version else {}) or {}
        # What was ticked in the editor first: it costs no evaluation and is what a person
        # set with two clicks.
        if not _selection_matches(t.get("where") or {}, ctx):
            continue
        rule = t.get("filter")
        if rule:
            try:
                if not safe_eval(rule, ctx):
                    continue
            except JsonLogicError as e:
                log.warning("Flow %s: the trigger filter is faulty (%s)", d.key, e)
                continue
        # Delivering the same event twice does not produce a second run.
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
            started.append(inst.id)
            log.info("Event %s started flow %s (instance %s)", event, d.key, inst.id)
        except Exception:  # noqa: BLE001, a broken flow must not disturb the trigger
            log.exception("Event %s: flow %s could not start", event, d.key)
    return started
