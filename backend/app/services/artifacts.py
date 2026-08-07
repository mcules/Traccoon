"""Artefakte: Zustände nachschlagen und setzen — egal ob Ticket, Hardware oder eigener Typ.

Der Prozess-Editor fragt hier, welche Zustände ein Ablauf überhaupt setzen kann (abhängig
vom Subjekt), und die Aktion `set_status` landet ebenfalls hier. Damit gibt es EINE
Status-Aktion statt dreier — und die Auswahl zeigt nur, was zum Subjekt passt.

Die eingebauten Typen schreiben weiter in ihre gewachsenen Spalten (`issues.agent_status`,
`hardware_assets.purchase_status`); das Register beschreibt sie nur. So ist der Typ im
Admin pflegbar, ohne Board, Sprints oder den KI-Lebenszyklus anzufassen.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artifact import Artifact, ArtifactType

log = logging.getLogger("artifacts")

# Subjekt eines Ablaufs → Schlüssel des Artefakt-Typs.
SUBJECT_TYPE = {"issue": "ticket", "hardware_asset": "hardware"}

# Ausgelieferte Typen. Die Schlüssel MÜSSEN den Enum-Werten der jeweiligen Spalte
# entsprechen — sie werden unverändert gespeichert.
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
    """Ticket und Hardware im Register anlegen — samt ihrer eingebauten Felder.

    Die Zustände sind seit dem Zusammenschluss die Werteliste des Feldes `status` und kein
    eigenes Modell mehr; sie entstehen deshalb zusammen mit den übrigen eingebauten Feldern
    (Priorität, Vorgangsart, Sprint, Seriennummer …). Bestehende Einträge werden nie
    überschrieben — wer eine Beschriftung ändert, behält sie.
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
    """Die möglichen Zustände eines Artefakts — die Werteliste des Feldes `status`.

    Liefert `ArtifactFieldOption`; `value` ist der gespeicherte Zustand (früher `key`).
    """
    from .artifact_fields import status_options
    return await status_options(db, type_id)


async def ensure_for_asset(db: AsyncSession, asset) -> Artifact:
    """Gemeinsame Artefakt-Zeile eines Hardware-Exemplars — anlegen, falls sie fehlt.

    Sie trägt Identität, Projekt und Zustand; die hardware-eigenen Felder (Modell, Ort,
    Kosten …) bleiben in `hardware_assets`. Damit zeigen Prozesse, Verweise und Freigaben
    auf dasselbe Objekt wie bei jedem anderen Artefakt.
    """
    if asset.artifact_id:
        vorhanden = await db.get(Artifact, asset.artifact_id)
        if vorhanden is not None:
            return vorhanden
    typ = await type_by_key(db, "hardware")
    if typ is None:                       # Register noch nicht geseedet
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
    """Titel/Projekt/Zustand der Artefakt-Zeile am Ticket nachziehen."""
    art = await ensure_for_issue(db, issue)
    art.project_id = issue.project_id
    art.title = (issue.summary or issue.key)[:500]
    art.status_key = getattr(issue.agent_status, "value", "") or ""


async def sync_asset_artifact(db: AsyncSession, asset) -> None:
    """Titel/Projekt/Zustand der Artefakt-Zeile am Exemplar nachziehen."""
    art = await ensure_for_asset(db, asset)
    art.project_id = asset.project_id
    art.status_key = getattr(asset.purchase_status, "value", str(asset.purchase_status or ""))


async def set_ticket_status(db: AsyncSession, issue, status, *, reason=None,
                            board: bool = True) -> None:
    """Der EINE Weg, den Zustand eines Tickets zu ändern.

    Schreibt Artefakt-Zeile und Ticket-Spalte in einem Zug — damit kann kein Auseinanderlaufen
    entstehen, statt es hinterher per Abgleich einzufangen. `status`/`reason` dürfen Enum oder
    Zeichenkette sein; `board=False` lässt die Board-Spalte unangetastet (z. B. beim Abziehen
    eines Agenten, wo das Ticket bewusst stehen bleibt).
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
    """Der EINE Weg, den Zustand eines Hardware-Exemplars zu ändern (mit Datumsfeldern)."""
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
    """Zustand eines Artefakts setzen — der eine Weg für alle Typen.

    Ticket und Hardware schreiben in ihre gewachsenen Spalten und lösen dieselben
    Folgewirkungen aus wie bisher (Board-Spalte, Meldung, Datumsfelder). Ein generischer
    Typ speichert schlicht seinen Schlüssel.
    """
    art = getattr(subject_kind, "value", str(subject_kind))

    if art == "issue" and issue is not None:
        from ..models.enums import HoldReason, TicketAgentStatus
        try:
            status = TicketAgentStatus(status_key)
        except ValueError:
            raise ValueError(f"'{status_key}' ist kein Zustand eines Tickets")
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
            raise ValueError(f"'{status_key}' ist kein Zustand eines Hardware-Exemplars")
        await set_asset_status(db, asset, status_key)
        return {"artifact": "hardware", "status": status_key}

    if artifact is not None:
        artifact.status_key = status_key
        return {"artifact": "generic", "status": status_key}

    return {"artifact": art, "status": status_key, "applied": False,
            "reason": "kein Artefakt an diesem Ablauf"}


async def backfill_hardware_artifacts(db: AsyncSession) -> int:
    """Bestandsexemplare an ihre Artefakt-Zeile hängen (idempotent, beim Start).

    Setzt zugleich die Artefakt-Bindung laufender Beschaffungs-Prozesse, damit sie nicht
    weiter nur über `hardware_asset_id` hängen.
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
    """Artefakt-Zeilen an die Detailtabellen angleichen — für Tickets UND Hardware.

    Warum ein Abgleichlauf statt Aufrufen an jeder Schreibstelle: `agent_status` wird an 21
    Stellen in 10 Dateien gesetzt (Endpunkte, Telegram-Bot, PM-Chat, Worker,
    Prozess-Aktionen). Jede einzeln nachzuziehen wäre eine offene Fehlerquelle — dieser Lauf
    erfasst sie per Konstruktion. Er läuft beim Start und im 30-Sekunden-Tick der
    Prozess-Engine; die häufigen Wege (`apply_status`) schreiben zusätzlich sofort mit.

    Bewusst in dialekt-neutralem SQLAlchemy statt in rohem SQL: so läuft derselbe Code unter
    Postgres wie unter der SQLite der Tests — und damit ist er überhaupt testbar. Die
    Abweichungen filtert die Datenbank, es werden nur betroffene Zeilen geladen.
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

    # 3) Wer arbeitet, steht auf „In Arbeit".
    #
    # Die Regel ist einfach und gilt ohne Ausnahme: läuft für ein Ticket gerade ein Agent,
    # dann ist es NICHT „Warten". Sie hier durchzusetzen statt an jeder Startstelle ist
    # dieselbe Überlegung wie beim Rest dieses Abgleichs — ein Lauf startet an mehreren
    # Wegen (Prozess-Schritt, Review-Runde im Worker, Wiedervorlage der Reliable-Queue nach
    # einem Neustart), und nur der letzte davon geht durch den Graphen.
    #
    # Am 2026-08-07 lief genau das schief: nach einem Worker-Neustart holte die Recovery zwei
    # Aufträge zurück, die Agenten arbeiteten — und das Board zeigte „Warten", weil den
    # Zustand niemand angefasst hatte.
    # Geprüft wird der ZUSTAND UND DIE SPALTE. Die Regel gilt dem Board, und das kann auch
    # dann falsch stehen, wenn `agent_status` längst stimmt: ABC-32 am 2026-08-07 wurde aus
    # dem Störungs-Zweig heraus fortgesetzt, der Agent lief mit `in_progress` — die Spalte
    # blieb auf „Warten", weil sie beim Parken gesetzt und nie wieder angefasst wurde. Ein
    # Abgleich, der nur auf `agent_status` schaut, sieht daran nichts Falsches.
    from ..models.agents import Run
    from ..models.enums import TicketAgentStatus as _TS
    from .dispatcher import sync_board_status
    laufend = (await db.execute(
        select(Issue, Run.phase)
        .join(Run, Run.issue_id == Issue.id)
        .where(Run.status == "running", Run.finished_at.is_(None),
               # `done` bleibt unangetastet: ein abgenommenes Ticket wird nicht
               # zurückgezogen, nur weil noch ein Lauf nachläuft.
               Issue.agent_status.is_distinct_from(_TS.done)))).all()
    gesehen: set[int] = set()
    for issue, phase in laufend:
        if issue.id in gesehen:
            continue
        gesehen.add(issue.id)
        ziel = _TS.planning if phase == "planning" else _TS.in_progress
        vorher = (getattr(issue.agent_status, "value", "—"), issue.status_id)
        if issue.agent_status in (_TS.planning, _TS.in_progress):
            await sync_board_status(db, issue)   # Zustand stimmt, nur die Spalte hinkt
        else:
            await set_ticket_status(db, issue, ziel)
        issue.agent_working = True
        if vorher != (getattr(issue.agent_status, "value", "—"), issue.status_id):
            log.info("Abgleich: %s läuft (%s), stand auf %s/Spalte %s → %s/Spalte %s",
                     issue.key, phase, vorher[0], vorher[1],
                     getattr(issue.agent_status, "value", "—"), issue.status_id)
    ergebnis["laufende_richtiggestellt"] = len(gesehen)

    # 4) Prozess-Instanzen an ihr Artefakt binden (löst die Spezial-Spalten ab).
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
