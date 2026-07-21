"""Geteilte Kommentar-Logik (Dashboard-API + Telegram-Bot).

Legt den Kommentar an und löst — bei comment_triggers_agent — eine Status-Transition
aus (kein Direkt-Spawn; die Dispatcher-Gates greifen). Nur echte User-Kommentare
(author_id gesetzt, kind=agent) triggern.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import HoldReason, TicketAgentStatus
from ..models.project import Project
from ..models.ticket import Comment, Issue

RUNNING = (TicketAgentStatus.planning, TicketAgentStatus.approved,
           TicketAgentStatus.in_progress, TicketAgentStatus.testing)


async def apply_user_comment(db: AsyncSession, issue: Issue, text: str,
                             user_id: int | None, label: str, kind: str = "agent") -> None:
    db.add(Comment(issue_id=issue.id, author_id=user_id, author_label=label, body=text, kind=kind))
    if kind == "internal" or user_id is None or issue.assigned_agent is None:
        await db.commit()
        return
    project = await db.get(Project, issue.project_id)
    if project and project.comment_triggers_agent:
        st = issue.agent_status
        if st in RUNNING:
            pass  # läuft bereits — nichts
        elif st == TicketAgentStatus.hold:
            issue.agent_status = TicketAgentStatus.planning
            issue.hold_reason = None
            issue.continuation_count += 1
        elif st == TicketAgentStatus.plan_review:
            issue.agent_status = TicketAgentStatus.planning  # Plan revidieren
        elif st in (TicketAgentStatus.done, TicketAgentStatus.failed, TicketAgentStatus.to_test) and issue.plan:
            issue.agent_status = TicketAgentStatus.approved  # Continuation liest Kommentar
            issue.hold_reason = None
            issue.continuation_count += 1
        else:
            issue.agent_status = TicketAgentStatus.planning
    await db.commit()
