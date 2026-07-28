"""Geteilte Kommentar-Logik (Dashboard-API + Telegram-Bot).

Legt den Kommentar an und meldet ihn — bei `comment_triggers_agent` — als Ereignis an den
Lebenszyklus-Prozess des Tickets. Wo der Prozess daraufhin weiterläuft (neu planen, weiter
umsetzen, Konflikt auflösen), steht im Graphen an den `wait_event`-Knoten und nicht mehr
hier. Nur echte User-Kommentare (author_id gesetzt, kind=agent) lösen aus.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import TicketAgentStatus
from ..models.project import Project
from ..models.ticket import Comment, Issue

log = logging.getLogger("comments")

RUNNING = (TicketAgentStatus.planning, TicketAgentStatus.approved,
           TicketAgentStatus.in_progress, TicketAgentStatus.testing)


async def add_system_comment(db: AsyncSession, issue_id: int, text: str,
                             author_label: str = "System") -> None:
    """Ereignis-Notiz im Ticket (Plan-Freigabe/-Ablehnung, Agent-Zwischenstand …).

    kind="system" → im Verlauf als neutraler Eintrag gerendert. Kein Commit;
    der Aufrufer committet (die Endpoints/Dispatcher tun das ohnehin).
    """
    db.add(Comment(issue_id=issue_id, author_id=None, author_label=author_label,
                   body=text, kind="system"))


async def apply_user_comment(db: AsyncSession, issue: Issue, text: str,
                             user_id: int | None, label: str, kind: str = "agent") -> None:
    db.add(Comment(issue_id=issue.id, author_id=user_id, author_label=label, body=text, kind=kind))
    if kind == "internal" or user_id is None or issue.assigned_agent is None:
        await db.commit()
        return
    project = await db.get(Project, issue.project_id)
    trigger = bool(project and project.comment_triggers_agent
                   and issue.agent_status not in RUNNING)
    issue_id, issue_key = issue.id, issue.key
    await db.commit()   # erst festschreiben — der Prozess liest den Kommentar gleich mit

    from .events import emit
    await emit(db, "comment.added", project_id=issue.project_id, issue_id=issue_id,
               actor_id=user_id,
               payload={"comment": {"text": text[:2000], "label": label},
                        "issue": {"key": issue_key}})
    if not trigger:
        return

    from .lifecycle_flow import live_instance, start_lifecycle
    from .workflow_engine import resume_on_event
    if await resume_on_event(issue_id, "comment", {"text": text[:2000], "user_id": user_id}):
        log.info("Ticket %s: Kommentar hat den Prozess fortgesetzt", issue_key)
        return
    # Kein wartender Ereignis-Knoten: entweder läuft gar kein Prozess (dann starten wir
    # einen) oder er wartet gerade auf eine Freigabe — die soll ein Kommentar NICHT
    # überspringen, sonst wäre die Menschenhoheit umgehbar.
    if await live_instance(db, issue) is None:
        await start_lifecycle(db, issue, user_id,
                              entry="exec" if issue.plan else "plan")
        await db.commit()
