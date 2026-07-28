"""Ereignisse — der lose Weg, einen Prozess zu starten.

Bisher musste jeder Auslöser einen Ablauf **fest benennen** (Webhook mit
`workflow_definition_id`, Job mit derselben Spalte). Wer einen zweiten Ablauf auf dasselbe
Ereignis hängen wollte, musste den Auslöser ändern. Hier ist es umgekehrt: Etwas passiert
(„issue.created", „mail.received", ein selbst benanntes Ereignis), und **der Ablauf
entscheidet**, ob er darauf hört — über den Trigger an seinem Start-Knoten:

    start.config.trigger = {
        "event": "issue.created",     # worauf gelauscht wird
        "project_id": 27,             # optional: nur für dieses Projekt
        "filter": {...JSONLogic...},  # optional: nur wenn der Inhalt passt
    }

Damit hängen an einem Ereignis beliebig viele Abläufe, und ein Projekt kann einen eigenen
danebenstellen, ohne irgendetwas umzuverdrahten.
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

# Ereignisse, die Traccoon selbst meldet. Eigene Namen sind erlaubt (Webhook/API) — diese
# Liste füttert nur die Auswahl im Editor.
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
    """Veröffentlichte Abläufe, deren Start-Knoten auf dieses Ereignis hört.

    Es wird über die veröffentlichten Definitionen gelesen statt über eine eigene
    Trigger-Tabelle: der Graph ist damit die einzige Wahrheit und kann nicht mit einem
    Index auseinanderlaufen. Bei der Größenordnung (Dutzende Abläufe) ist das billig.
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
        # Am Trigger gesetztes Projekt grenzt zusätzlich ein.
        gewuenscht = t.get("project_id")
        if gewuenscht and int(gewuenscht) != (project_id or 0):
            continue
        treffer.append(d)
    return treffer


async def emit(db: AsyncSession, event: str, *, project_id: int | None = None,
               payload: dict | None = None, issue_id: int | None = None,
               hardware_asset_id: int | None = None, actor_id: int | None = None,
               source_ref: str | None = None) -> list[int]:
    """Ereignis melden und alle passenden Abläufe starten. Liefert die Instanz-IDs.

    Fehler eines einzelnen Ablaufs brechen nichts ab — ein Ereignis ist eine Meldung, kein
    Auftrag: der Aufrufer (Ticket anlegen, Kommentar schreiben …) darf davon nicht abhängen.
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
        except Exception:  # noqa: BLE001 — ein kaputter Ablauf darf den Auslöser nicht stören
            log.exception("Ereignis %s: Ablauf %s konnte nicht starten", event, d.key)
    return gestartet
