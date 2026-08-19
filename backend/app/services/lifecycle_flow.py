"""Entry into the ticket lifecycle: the bracket between ticket and process engine.

Since the lifecycle is a graph (slot `ticket_lifecycle`), there is exactly one way to set an
agent in motion: start an instance of this flow. Who gets which flow is decided by
`workflow_sets.resolve_definition` (project copy, then set of the project, then set of an
owner, then the global default).

Three entries (`context.entry`, evaluated by the `entry` node of the default graph):
    plan    - the normal case: the agent plans first
    exec    - the plan exists (approved sub-task of a splitting)
    accept  - only acceptance is left (collective ticket whose parts are all finished)
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import WorkflowInstanceStatus, WorkflowSlot, WorkflowSubjectKind
from ..models.ticket import Issue
from ..models.workflow import WorkflowInstance
from .workflow_engine import start_workflow
from .workflow_sets import resolve_definition


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)

log = logging.getLogger("lifecycle_flow")

LIFECYCLE_SLOT = WorkflowSlot.ticket_lifecycle.value
LIVE = (WorkflowInstanceStatus.running, WorkflowInstanceStatus.waiting)


async def live_instance(db: AsyncSession, issue: Issue) -> WorkflowInstance | None:
    """Laufende Lebenszyklus-Instanz des Tickets (oder None)."""
    if issue.workflow_instance_id:
        inst = await db.get(WorkflowInstance, issue.workflow_instance_id)
        if inst is not None and inst.status in LIVE:
            return inst
    return (await db.execute(
        select(WorkflowInstance).where(
            WorkflowInstance.issue_id == issue.id,
            WorkflowInstance.parent_instance_id.is_(None),
            WorkflowInstance.status.in_(LIVE),
        ).order_by(WorkflowInstance.id.desc()))).scalars().first()


async def start_lifecycle(db: AsyncSession, issue: Issue, actor_id: int | None = None, *,
                          entry: str = "plan", restart: bool = False,
                          context: dict | None = None,
                          advance_now: bool = True) -> WorkflowInstance | None:
    """Start the lifecycle for a ticket (idempotent).

    If one is already running it is returned, except with `restart=True`, which aborts the
    old one. Without an assigned agent nothing happens: Traccoon works exclusively on an
    explicit assignment (a core principle, no auto pickup).
    """
    if issue.assigned_agent is None:
        return None
    existing = await live_instance(db, issue)
    if existing is not None:
        if not restart:
            return existing
        existing.status = WorkflowInstanceStatus.cancelled
        from ..models.enums import WorkflowTokenState
        from ..models.workflow import WorkflowToken
        for t in (await db.execute(select(WorkflowToken).where(
                WorkflowToken.instance_id == existing.id))).scalars().all():
            t.state = WorkflowTokenState.consumed
        await db.flush()

    # The issue type has a say: a bug may have a flow of its own.
    definition = await resolve_definition(db, issue.project_id, LIFECYCLE_SLOT,
                                          issue.type_id)
    if definition is None or definition.current_version_id is None:
        log.warning("Ticket %s: no published lifecycle flow", issue.key)
        return None
    ctx = {"entry": entry, "issue_key": issue.key, **(context or {})}
    inst = await start_workflow(
        db, definition, subject_kind=WorkflowSubjectKind.issue, issue_id=issue.id,
        context=ctx, actor_id=actor_id or issue.assigned_by_user_id or issue.reporter_id,
        source="ticket", advance_now=advance_now,
    )
    log.info("Ticket %s: Lebenszyklus gestartet (Instanz %s, Einstieg %s)",
             issue.key, inst.id, entry)
    return inst


async def entscheide_offene_genehmigung(
    db: AsyncSession, issue: Issue, decision: str, actor_id: int | None,
    reason: str | None = None,
) -> bool:
    """Decide the open approval of a ticket, without HTTP.

    The assistant operates Traccoon over native tools in the worker, not over the API.
    Without this path `traccoon_approve_plan` only set `agent_status = approved` and left the
    process standing at its approval node: the ticket looked approved and nobody started. The
    same trap as with the assignment (ABC-32 on 2026-08-07).

    Advancing deliberately does NOT happen here: `advance` belongs in the backend process,
    whose 30 s tick finds an active token anyway. An `advance` out of the worker would hang
    the watchers of the following steps in a foreign process.

    Returns False when there is nothing to decide right now, so that the caller can say so
    instead of reporting success.
    """
    from .workflow_engine import entscheide_genehmigung

    return await entscheide_genehmigung(db, await live_instance(db, issue), decision,
                                        actor_id=actor_id, reason=reason)


# Where an existing ticket stands in the default graph. Only states that WAIT; running ones
# (planning/approved/in_progress) enter anew over `entry`, because their agent run no longer
# exists at the changeover anyway.
_ADOPT_NODES: dict[str, tuple[str, str]] = {
    "plan_review": ("approve_plan", "approval"),
    "to_test": ("approve_result", "approval"),
    "testing": ("approve_result", "approval"),
}
_ADOPT_WAIT = {                     # (with a plan, without a plan)
    "hold": ("wait_exec", "wait_plan"),
    "failed": ("wait_exec", "wait_plan"),
}


async def adopt_orphans(db: AsyncSession) -> int:
    """Collect existing tickets without a process instance (changeover to the process engine).

    Waiting tickets are placed at the matching point of the graph so that the familiar
    buttons (approve plan, accept, comment) take hold again immediately. Runs idempotently at
    every start; a ticket whose instance is missing would otherwise lie there mutely.

    """
    from ..models.enums import WorkflowNodeType, WorkflowStepStatus, WorkflowTokenState
    from ..models.workflow import WorkflowStepRun, WorkflowToken, WorkflowVersion
    from .workflow_engine import node_type

    rows = (await db.execute(
        select(Issue).where(
            Issue.assigned_agent.isnot(None),
            Issue.workflow_instance_id.is_(None),
            Issue.agent_status.isnot(None),
        ))).scalars().all()
    adopted = 0
    for issue in rows:
        st = issue.agent_status.value
        if st in ("done", "open"):
            continue
        if await live_instance(db, issue) is not None:
            continue

        target = _ADOPT_NODES.get(st)
        if target is None and st in _ADOPT_WAIT:
            with_plan, without_plan = _ADOPT_WAIT[st]
            target = ((with_plan if issue.plan else without_plan), "wait_event")
        if target is None:
            # planning/approved/in_progress: enter regularly again
            inst = await start_lifecycle(db, issue, entry="exec" if issue.plan else "plan")
            adopted += 1 if inst else 0
            continue

        inst = await start_lifecycle(db, issue, entry="plan", advance_now=False)
        if inst is None:
            continue
        node_id, expected = target
        version = await db.get(WorkflowVersion, inst.version_id)
        node = next((n for n in ((version.graph if version else {}) or {}).get("nodes", [])
                     if n.get("id") == node_id), None)
        if node is None or node_type(node) != expected:
            # An adjusted graph without this node: the regular entry has to be enough.
            adopted += 1
            continue
        # Put the token on the waiting node and create the matching step.
        for t in (await db.execute(select(WorkflowToken).where(
                WorkflowToken.instance_id == inst.id))).scalars().all():
            t.node_id = node_id
            t.state = WorkflowTokenState.waiting
            t.waiting_for = "approval" if expected == "approval" else "event"
        inst.status = WorkflowInstanceStatus.waiting
        db.add(WorkflowStepRun(
            instance_id=inst.id, node_id=node_id,
            node_type=WorkflowNodeType(expected), status=WorkflowStepStatus.waiting,
            assignee_user_id=issue.assigned_by_user_id or issue.reporter_id,
        ))
        adopted += 1
    if adopted:
        await db.commit()
        log.info("Switch: %d existing ticket(s) taken into the lifecycle process", adopted)
    return adopted


async def cancel_lifecycle(db: AsyncSession, issue: Issue) -> bool:
    """Abort a running lifecycle (agent pulled off, ticket deleted or archived)."""
    from ..models.enums import WorkflowTokenState
    from ..models.workflow import WorkflowToken
    inst = await live_instance(db, issue)
    if inst is None:
        return False
    inst.status = WorkflowInstanceStatus.cancelled
    for t in (await db.execute(select(WorkflowToken).where(
            WorkflowToken.instance_id == inst.id))).scalars().all():
        t.state = WorkflowTokenState.consumed
    issue.workflow_instance_id = None
    await db.flush()
    return True


async def promote_split(db: AsyncSession, child: Issue) -> None:
    """Splitting chain: start the next parked sibling; when all are finished, the collective
    ticket goes into acceptance.

    Runs after the merge of one part (worker) so that part n+1 builds on the result of part
    n, exactly as before the migration, only that now an instance starts instead of a status
    jump.
    """
    from ..models.enums import TicketAgentStatus
    from .comments import add_system_comment

    umbrella_id = child.parent_ticket_id
    if not umbrella_id:
        return
    sibs = (await db.execute(
        select(Issue).where(Issue.parent_ticket_id == umbrella_id)
        .order_by(Issue.split_order).with_for_update())).scalars().all()

    nxt = next((s for s in sibs if s.agent_status is None), None)
    if nxt is not None:
        from .artifacts import set_ticket_status
        await set_ticket_status(db, nxt, TicketAgentStatus.approved)
        await add_system_comment(
            db, nxt.id,
            f"▶️ Automatisch freigegeben — Vorgänger {child.key} ist fertig.")
        await db.flush()
        # Sub-tasks already have their plan, so straight into the implementation.
        await start_lifecycle(db, nxt, entry="exec")
        return

    if all(s.agent_status == TicketAgentStatus.done for s in sibs):
        umbrella = await db.get(Issue, umbrella_id)
        if umbrella is None:
            return
        await add_system_comment(
            db, umbrella.id,
            f"✅ Alle {len(sibs)} Teilaufgaben fertig — Sammelticket geht in die Abnahme.")
        await db.flush()
        await start_lifecycle(db, umbrella, entry="accept", restart=True)
