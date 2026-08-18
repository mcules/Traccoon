"""Einstieg in den Ticket-Lebenszyklus — die Klammer zwischen Ticket und Prozess-Engine.

Seit der Lebenszyklus ein Graph ist (Slot `ticket_lifecycle`), gibt es genau einen Weg,
einen Agenten in Bewegung zu setzen: eine Instanz dieses Ablaufs starten. Wer welchen
Ablauf bekommt, entscheidet `workflow_sets.resolve_definition` (Projekt-Kopie → Satz des
Projekts → Satz eines Owners → globaler Standard).

Drei Einstiege (`context.entry`, ausgewertet vom `entry`-Knoten des Standard-Graphen):
    plan    — Normalfall: der Agent plant erst
    exec    — Plan liegt vor (freigegebene Teilaufgabe einer Aufteilung)
    accept  — nur noch abnehmen (Sammelticket, dessen Teile alle fertig sind)
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
    """Lebenszyklus für ein Ticket starten (idempotent).

    Läuft bereits einer, wird er zurückgegeben — außer `restart=True`, dann wird der alte
    abgebrochen. Ohne zugewiesenen Agenten passiert nichts: Traccoon arbeitet ausschließlich
    auf ausdrückliche Zuweisung (Kernprinzip, kein Auto-Pickup).
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

    # Die Vorgangsart entscheidet mit: ein Bug darf einen eigenen Ablauf haben.
    definition = await resolve_definition(db, issue.project_id, LIFECYCLE_SLOT,
                                          issue.type_id)
    if definition is None or definition.current_version_id is None:
        log.warning("Ticket %s: kein veröffentlichter Lebenszyklus-Ablauf", issue.key)
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
    """Die offene Genehmigung eines Tickets entscheiden — ohne HTTP.

    Der Assistent bedient Traccoon über native Werkzeuge im Worker, nicht über die API. Ohne
    diesen Weg setzte `traccoon_approve_plan` nur `agent_status = approved` und ließ den
    Prozess an seinem Genehmigungs-Knoten stehen: das Ticket sah freigegeben aus, und niemand
    fing an. Dieselbe Falle wie bei der Zuweisung (ABC-32 am 2026-08-07).

    Weitergeschaltet wird bewusst NICHT hier: `advance` gehört in den Backend-Prozess, dessen
    30-s-Tick ein aktives Token ohnehin findet. Ein `advance` aus dem Worker würde die Wächter
    der Folgeschritte in einem fremden Prozess aufhängen.

    Liefert False, wenn es gerade nichts zu entscheiden gibt — der Aufrufer soll das sagen
    können, statt Erfolg zu melden.
    """
    from .workflow_engine import entscheide_genehmigung

    return await entscheide_genehmigung(db, await live_instance(db, issue), decision,
                                        actor_id=actor_id, reason=reason)


# Wo ein Bestandsticket im Standard-Graphen steht. Nur Zustände, die WARTEN — laufende
# (planning/approved/in_progress) steigen über `entry` neu ein, weil ihr Agentenlauf beim
# Umstieg ohnehin nicht mehr existiert.
_ADOPT_NODES: dict[str, tuple[str, str]] = {
    "plan_review": ("approve_plan", "approval"),
    "to_test": ("approve_result", "approval"),
    "testing": ("approve_result", "approval"),
}
_ADOPT_WAIT = {                     # (mit Plan, ohne Plan)
    "hold": ("wait_exec", "wait_plan"),
    "failed": ("wait_exec", "wait_plan"),
}


async def adopt_orphans(db: AsyncSession) -> int:
    """Bestandstickets ohne Prozess-Instanz einsammeln (Umstieg auf die Prozess-Engine).

    Wartende Tickets werden an der passenden Stelle des Graphen abgesetzt, damit die
    gewohnten Knöpfe (Plan freigeben, Abnehmen, Kommentar) sofort wieder greifen. Läuft
    idempotent bei jedem Start — ein Ticket, dessen Instanz fehlt, würde sonst stumm liegen
    bleiben.
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
            # planning/approved/in_progress → regulär neu einsteigen
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
            # Angepasster Graph ohne diesen Knoten: der reguläre Einstieg muss reichen.
            adopted += 1
            continue
        # Token auf den Warteknoten setzen und den passenden Schritt anlegen.
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
        log.info("Umstieg: %d Bestandsticket(s) in den Lebenszyklus-Prozess übernommen", adopted)
    return adopted


async def cancel_lifecycle(db: AsyncSession, issue: Issue) -> bool:
    """Laufenden Lebenszyklus abbrechen (Agent abgezogen, Ticket gelöscht/archiviert)."""
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
    """Aufteilungs-Kette: nächstes geparktes Geschwister starten; sind alle fertig, geht das
    Sammelticket in die Abnahme.

    Läuft nach dem Merge eines Teils (Worker), damit Teil n+1 auf dem Ergebnis von Teil n
    aufbaut — genau wie vor der Migration, nur startet jetzt eine Instanz statt eines
    Status-Sprungs.
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
        # Teilaufgaben haben ihren Plan schon → direkt in die Umsetzung.
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
