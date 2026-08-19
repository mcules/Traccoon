"""Shared comment logic (dashboard API plus Telegram bot).

Creates the comment and, with `comment_triggers_agent`, reports it as an event to the
lifecycle process of the ticket. Where the process continues from there (planning anew,
implementing on, resolving a conflict) stands in the graph at the `wait_event` nodes and no
longer here. Only real user comments (author_id set, kind=agent) trigger it.
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

    kind="system" is rendered in the history as a neutral entry. No commit; the caller commits
    (the endpoints and the dispatcher do that anyway).
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
    await db.commit()   # commit first: the process reads the comment right away

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
        log.info("Ticket %s: the comment continued the process", issue_key)
        return
    # No waiting event node: either no process is running at all (then we start one) or it is
    # waiting for an approval, and a comment must NOT skip that, because otherwise human
    # sovereignty would be circumventable.
    if await live_instance(db, issue) is None:
        await start_lifecycle(db, issue, user_id,
                              entry="exec" if issue.plan else "plan")
        await db.commit()
