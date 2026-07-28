"""Notification-Erzeugung (In-App-Glocke + Telegram-Zustellung durch den Bot)."""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.notification import Notification
from ..models.ticket import Issue
from ..models.user import User

OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")


async def notify_issue(db: AsyncSession, issue: Issue, kind: str, title: str, body: str = "") -> None:
    owner_id = issue.assigned_by_user_id or issue.assignee_user_id or issue.reporter_id
    chat = None
    if owner_id:
        u = await db.get(User, owner_id)
        chat = (u.telegram_chat_id if u else None) or OWNER_CHAT or None
    else:
        chat = OWNER_CHAT or None
    db.add(Notification(user_id=owner_id, project_id=issue.project_id, issue_id=issue.id,
                        kind=kind, title=title, body=body[:4000], chat_id=chat))
