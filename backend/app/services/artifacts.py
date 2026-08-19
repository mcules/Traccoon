"""Artifacts: looking up and setting states, whether ticket, hardware or a type of one's own.

The process editor asks here which states a flow can set at all (depending on the subject),
and the action `set_status` lands here as well. That gives ONE status action instead of
three, and the selection only shows what fits the subject.

The built-in types keep writing into their grown columns (`issues.agent_status`,
`hardware_assets.purchase_status`); the register only describes them. That way the type is
maintainable in the admin area without touching board, sprints or the AI lifecycle.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artifact import Artifact, ArtifactType

log = logging.getLogger("artifacts")

# Subject of a flow mapped to the key of the artifact type.
SUBJECT_TYPE = {"issue": "ticket", "hardware_asset": "hardware"}

# Shipped types. The keys MUST correspond to the enum values of the respective column: they
# are stored unchanged.
BUILTIN: dict[str, dict] = {
    "ticket": {
        "name": "Ticket", "plural": "Tickets", "icon": "🎫", "color": "#58a6ff",
        "backing": "issue",
        "description": "Vorgang mit KI-Lebenszyklus, Board-Spalte und Agenten-Zuweisung.",
    },
    "hardware": {
        "name": "Hardware-Exemplar", "plural": "Hardware", "icon": "🖥️", "color": "#a371f7",
        "backing": "hardware_asset",
        "description": "Gerät im Bestand — von der Bestellung bis zur Ausmusterung.",
    },
}


async def ensure_builtin_types(db: AsyncSession) -> None:
    """Create ticket and hardware in the register, including their built-in fields.

    Since the merge the states are the value list of the field `status` and no longer a model
    of their own; they therefore come into being together with the other built-in fields
    (priority, issue type, sprint, serial number …). Existing entries are never overwritten:
    whoever changes a label keeps it.
    """
    for key, spec in BUILTIN.items():
        t = (await db.execute(select(ArtifactType).where(ArtifactType.key == key))
             ).scalar_one_or_none()
        if t is None:
            t = ArtifactType(
                key=key, name=spec["name"], plural=spec["plural"], icon=spec["icon"],
                color=spec["color"], backing=spec["backing"], builtin=True,
                description=spec["description"],
            )
            db.add(t)
            await db.flush()
    await db.commit()
    from .artifact_fields import ensure_builtin_fields
    await ensure_builtin_fields(db)


async def type_by_key(db: AsyncSession, key: str) -> ArtifactType | None:
    return (await db.execute(select(ArtifactType).where(ArtifactType.key == key))
            ).scalar_one_or_none()


async def type_for_subject(db: AsyncSession, subject_kind) -> ArtifactType | None:
    """Artefakt-Typ, den ein Ablauf mit diesem Subjekt bearbeitet."""
    key = SUBJECT_TYPE.get(getattr(subject_kind, "value", str(subject_kind)))
    return await type_by_key(db, key) if key else None


async def statuses(db: AsyncSession, type_id: int):
    """The possible states of an artifact, the value list of the field `status`.

    Delivers `ArtifactFieldOption`; `value` is the stored state (formerly `key`).
    """
    from .artifact_fields import status_options
    return await status_options(db, type_id)


async def ensure_for_asset(db: AsyncSession, asset) -> Artifact:
    """Gemeinsame Artefakt-Zeile eines Hardware-Exemplars — anlegen, falls sie fehlt.

    It carries identity, project and state; the hardware-owned fields (model, place, cost …)
    stay in `hardware_assets`. That way processes, references and approvals point at the same
    object as with every other artifact.
    """
    if asset.artifact_id:
        vorhanden = await db.get(Artifact, asset.artifact_id)
        if vorhanden is not None:
            return vorhanden
    typ = await type_by_key(db, "hardware")
    if typ is None:                       # register not seeded yet
        await ensure_builtin_types(db)
        typ = await type_by_key(db, "hardware")
    from ..models.hardware import HardwareModel
    modell = await db.get(HardwareModel, asset.model_id) if asset.model_id else None
    titel = " · ".join(x for x in [modell.name if modell else "Exemplar",
                                   asset.serial_number or ""] if x)
    art = Artifact(
        type_id=typ.id, project_id=asset.project_id, title=titel[:500],
        status_key=getattr(asset.purchase_status, "value", str(asset.purchase_status or "")),
    )
    db.add(art)
    await db.flush()
    asset.artifact_id = art.id
    return art


async def ensure_for_issue(db: AsyncSession, issue) -> Artifact:
    """Artefakt-Zeile eines Tickets — anlegen, falls sie fehlt."""
    if issue.artifact_id:
        vorhanden = await db.get(Artifact, issue.artifact_id)
        if vorhanden is not None:
            return vorhanden
    typ = await type_by_key(db, "ticket")
    if typ is None:
        await ensure_builtin_types(db)
        typ = await type_by_key(db, "ticket")
    art = Artifact(
        type_id=typ.id, project_id=issue.project_id, title=(issue.summary or issue.key)[:500],
        status_key=getattr(issue.agent_status, "value", "") or "",
    )
    db.add(art)
    await db.flush()
    issue.artifact_id = art.id
    return art


async def sync_issue_artifact(db: AsyncSession, issue) -> None:
    """Update title, project and state of the artifact row on the ticket."""
    art = await ensure_for_issue(db, issue)
    art.project_id = issue.project_id
    art.title = (issue.summary or issue.key)[:500]
    art.status_key = getattr(issue.agent_status, "value", "") or ""


async def sync_asset_artifact(db: AsyncSession, asset) -> None:
    """Update title, project and state of the artifact row on the unit."""
    art = await ensure_for_asset(db, asset)
    art.project_id = asset.project_id
    art.status_key = getattr(asset.purchase_status, "value", str(asset.purchase_status or ""))


async def set_ticket_status(db: AsyncSession, issue, status, *, reason=None,
                            board: bool = True) -> None:
    """The ONE way to change the state of a ticket.

    Writes the artifact row and the ticket column in one go, so no drift can come into being
    instead of being caught afterwards by a reconciliation. `status`/`reason` may be an enum
    or a string; `board=False` leaves the board column untouched (for instance when pulling an
    agent off, where the ticket deliberately stays where it is).
    """
    from ..models.enums import HoldReason, TicketAgentStatus

    if status is None:
        issue.agent_status = None
    else:
        issue.agent_status = (status if isinstance(status, TicketAgentStatus)
                              else TicketAgentStatus(str(status)))
    if reason is None:
        if issue.agent_status != TicketAgentStatus.hold:
            issue.hold_reason = None
    elif reason == "":
        issue.hold_reason = None
    else:
        issue.hold_reason = (reason if isinstance(reason, HoldReason)
                             else HoldReason(str(reason)))
    if board and issue.agent_status is not None:
        from .dispatcher import sync_board_status
        await sync_board_status(db, issue)
    await sync_issue_artifact(db, issue)


async def set_asset_status(db: AsyncSession, asset, status) -> None:
    """The ONE way to change the state of a hardware unit (with date fields)."""
    import datetime as _dt

    from ..models.enums import PurchaseStatus
    ps = status if isinstance(status, PurchaseStatus) else PurchaseStatus(str(status))
    asset.purchase_status = ps
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    if ps == PurchaseStatus.ordered and asset.order_date is None:
        asset.order_date = now
    elif ps == PurchaseStatus.delivered and asset.delivery_date is None:
        asset.delivery_date = now
    elif ps == PurchaseStatus.installed and asset.install_date is None:
        asset.install_date = now
    await sync_asset_artifact(db, asset)


async def apply_status(db: AsyncSession, *, subject_kind, issue=None, asset=None,
                       artifact: Artifact | None = None, status_key: str,
                       reason: str = "", notify: bool = True) -> dict:
    """Set the state of an artifact, the one way for all types.

    Ticket and hardware write into their grown columns and trigger the same consequences as
    before (board column, message, date fields). A generic type simply stores its key.
    """
    art = getattr(subject_kind, "value", str(subject_kind))

    if art == "issue" and issue is not None:
        from ..models.enums import HoldReason, TicketAgentStatus
        try:
            status = TicketAgentStatus(status_key)
        except ValueError:
            raise ValueError(f"'{status_key}' is not a state of a ticket")
        grund = reason or (HoldReason.question if status == TicketAgentStatus.hold else None)
        try:
            await set_ticket_status(db, issue, status, reason=grund)
        except ValueError:
            await set_ticket_status(db, issue, status, reason=HoldReason.question)
        if status == TicketAgentStatus.done and issue.resolved_at is None:
            import datetime as _dt
            issue.resolved_at = _dt.datetime.now(tz=_dt.timezone.utc)
        return {"artifact": "ticket", "status": status_key}

    if art == "hardware_asset" and asset is not None:
        from ..models.enums import PurchaseStatus
        try:
            PurchaseStatus(status_key)
        except ValueError:
            raise ValueError(f"'{status_key}' is not a state of a hardware unit")
        await set_asset_status(db, asset, status_key)
        return {"artifact": "hardware", "status": status_key}

    if artifact is not None:
        artifact.status_key = status_key
        return {"artifact": "generic", "status": status_key}

    return {"artifact": art, "status": status_key, "applied": False,
            "reason": "kein Artefakt an diesem Ablauf"}


async def backfill_hardware_artifacts(db: AsyncSession) -> int:
    """Hang existing units off their artifact row (idempotent, at start).

    Also sets the artifact binding of running procurement processes so that they no longer
    hang off `hardware_asset_id` alone.
    """
    from ..models.hardware import HardwareAsset
    from ..models.workflow import WorkflowInstance

    offen = (await db.execute(
        select(HardwareAsset).where(HardwareAsset.artifact_id.is_(None)))).scalars().all()
    for asset in offen:
        await ensure_for_asset(db, asset)
    if offen:
        await db.flush()

    instanzen = (await db.execute(
        select(WorkflowInstance).where(
            WorkflowInstance.hardware_asset_id.isnot(None),
            WorkflowInstance.artifact_id.is_(None)))).scalars().all()
    for inst in instanzen:
        asset = await db.get(HardwareAsset, inst.hardware_asset_id)
        if asset is not None and asset.artifact_id:
            inst.artifact_id = asset.artifact_id
    if offen or instanzen:
        await db.commit()
        log.info("Artefakte nachgetragen: %d Exemplare, %d Prozess-Instanzen",
                 len(offen), len(instanzen))
    return len(offen)


async def reconcile(db: AsyncSession) -> dict:
    """Align artifact rows with the detail tables, for tickets AND hardware.

    Why a reconciliation pass instead of calls at every write site: `agent_status` is set in
    21 places in 10 files (endpoints, Telegram bot, PM chat, worker,
    process actions). Updating each one separately would be an open source of errors; this
    pass covers them by construction. It runs at start and in the 30 second tick of the
    process engine; the frequent paths (`apply_status`) write along immediately as well.

    Deliberately in dialect-neutral SQLAlchemy instead of raw SQL: that way the same code runs
    under Postgres as under the SQLite of the tests, which makes it testable at all. The
    deviations are filtered by the database, and only affected rows are loaded.
    """
    from sqlalchemy import String, cast, func, or_

    from ..models.hardware import HardwareAsset
    from ..models.ticket import Issue

    ergebnis = {"tickets_neu": 0, "tickets_angeglichen": 0, "hardware_angeglichen": 0}

    # 1) Fehlende Artefakt-Zeilen anlegen.
    offen = (await db.execute(
        select(Issue).where(Issue.artifact_id.is_(None)))).scalars().all()
    for issue in offen:
        await ensure_for_issue(db, issue)
    ergebnis["tickets_neu"] = len(offen)

    def _titel(text: str | None, ersatz: str) -> str:
        return (text or ersatz)[:500]

    # 2) Abweichungen angleichen — Zustand, Titel, Projekt.
    ticket_drift = (await db.execute(
        select(Issue, Artifact)
        .join(Artifact, Artifact.id == Issue.artifact_id)
        .where(or_(
            Artifact.status_key != func.coalesce(cast(Issue.agent_status, String), ""),
            Artifact.title != Issue.summary,
            Artifact.project_id.is_distinct_from(Issue.project_id),
        )))).all()
    for issue, art in ticket_drift:
        art.status_key = getattr(issue.agent_status, "value", "") or ""
        art.title = _titel(issue.summary, issue.key)
        art.project_id = issue.project_id
    ergebnis["tickets_angeglichen"] = len(ticket_drift)

    hardware_drift = (await db.execute(
        select(HardwareAsset, Artifact)
        .join(Artifact, Artifact.id == HardwareAsset.artifact_id)
        .where(or_(
            Artifact.status_key != cast(HardwareAsset.purchase_status, String),
            Artifact.project_id.is_distinct_from(HardwareAsset.project_id),
        )))).all()
    for asset, art in hardware_drift:
        art.status_key = getattr(asset.purchase_status, "value", "") or ""
        art.project_id = asset.project_id
    ergebnis["hardware_angeglichen"] = len(hardware_drift)

    # 3) Whoever works stands on "in progress".
    #
    # The rule is simple and holds without exception: if an agent is running for a ticket,
    # then it is NOT "waiting". Enforcing it here instead of at every start site is the same
    # consideration as with the rest of this reconciliation: a run starts over several paths
    # (process step, review round in the worker, follow-up of the reliable queue after a
    # restart), and only the last of them goes through the graph.
    #
    # On 2026-08-07 exactly that went wrong: after a worker restart the recovery fetched two
    # assignments back, the agents worked, and the board showed "waiting", because nobody had
    # touched the state.
    # What is checked is the STATE AND THE COLUMN. The rule concerns the board, and that can
    # be wrong even when `agent_status` has long been right: ABC-32 on 2026-08-07 was
    # continued out of the disturbance branch, the agent ran with `in_progress`, and the
    # column stayed on "waiting" because it was set while parking and never touched again. A
    # reconciliation that only looks at `agent_status` sees nothing wrong with that.
    from ..models.agents import Run
    from ..models.enums import TicketAgentStatus as _TS
    from .dispatcher import sync_board_status
    laufend = (await db.execute(
        select(Issue, Run.phase)
        .join(Run, Run.issue_id == Issue.id)
        .where(Run.status == "running", Run.finished_at.is_(None),
               # `done` stays untouched: an accepted ticket is not pulled back just because
               # a run is still trailing.
               Issue.agent_status.is_distinct_from(_TS.done)))).all()
    gesehen: set[int] = set()
    for issue, phase in laufend:
        if issue.id in gesehen:
            continue
        gesehen.add(issue.id)
        ziel = _TS.planning if phase == "planning" else _TS.in_progress
        vorher = (getattr(issue.agent_status, "value", "—"), issue.status_id)
        if issue.agent_status in (_TS.planning, _TS.in_progress):
            await sync_board_status(db, issue)   # the state is right, only the column lags
        else:
            await set_ticket_status(db, issue, ziel)
        issue.agent_working = True
        if vorher != (getattr(issue.agent_status, "value", "—"), issue.status_id):
            log.info("Reconciliation: %s is running (%s), stood on %s/column %s -> %s/column %s",
                     issue.key, phase, vorher[0], vorher[1],
                     getattr(issue.agent_status, "value", "—"), issue.status_id)
    ergebnis["laufende_richtiggestellt"] = len(gesehen)

    # 4) Bind process instances to their artifact (superseding the special columns).
    from ..models.workflow import WorkflowInstance
    lose = (await db.execute(
        select(WorkflowInstance, Issue.artifact_id)
        .join(Issue, Issue.id == WorkflowInstance.issue_id)
        .where(WorkflowInstance.artifact_id.is_(None),
               Issue.artifact_id.isnot(None)))).all()
    for inst, art_id in lose:
        inst.artifact_id = art_id
    ergebnis["instanzen_gebunden"] = len(lose)

    if any(ergebnis.values()):
        await db.commit()
        log.info("Artefakte abgeglichen: %s", ergebnis)
    return ergebnis
