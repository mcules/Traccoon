"""Approving and discarding assistant inbox items, shared by the web API and the Telegram
bot so that both interfaces do exactly the same (usable in parallel, always in sync)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask
from .assistant_policy import parse_sender, upsert_policy


async def _finde_source(db: AsyncSession, owner_user_id: int | None, chat_id: str,
                        reference: str) -> int | None:
    """Which inbox item does the quoted message belong to? Returns assistant_task_id or None.

    Telegram messages of the assistant come into being from notification rows
    (`<b>{title}</b>\n{body}`). The first line of the quote is therefore the title, and over
    it the notification and with it the task can be found."""
    from sqlalchemy import select

    from ..models.notification import Notification
    title = (reference.strip().splitlines() or [""])[0].strip()
    if not title:
        return None
    row = (await db.execute(
        select(Notification).where(
            Notification.chat_id == str(chat_id),
            Notification.assistant_task_id.isnot(None),
            Notification.title == title,
        ).order_by(Notification.id.desc()).limit(1))).scalars().first()
    return row.assistant_task_id if row else None


async def create_chat_task(db: AsyncSession, owner_user_id: int | None, text: str,
                           chat_id: str, agent: str = "", reference: str = "") -> AssistantTask:
    """Hand a chat message to the assistant (created plus queued).

    Shared so that every entry does the same. In the bot it hung off a single handler, and an
    ANSWER to a message of the assistant thereby fell out of the chat and was discarded
    without a word.

    `bezug` is the quoted message of the assistant. It is carried along and put in front in
    the run: an answer means exactly this one message, not the conversation in general. If it
    belongs to an earlier inbox item (mail, approval), that task is linked as well, so that
    the assistant works on there instead of starting anew."""
    meta = {"chat_text": text, "chat_id": str(chat_id)}
    if agent:
        meta["agent"] = agent        # otherwise the run falls back to 'assistent'
    if reference.strip():
        meta["bezug_text"] = reference.strip()[:2000]
        source = await _finde_source(db, owner_user_id, str(chat_id), reference)
        if source is not None:
            meta["bezug_task_id"] = source
    task = AssistantTask(owner_user_id=owner_user_id, kind="chat", source="telegram",
                         title=text[:200], status="approved", meta=meta)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task.id}",
                        "assistant_task_id": task.id})
    return task


async def approve_assistant_task(db: AsyncSession, task: AssistantTask, *, scope: str = "once",
                                 redaction: str = "redacted", action_note: str = "") -> None:
    """Approve an item (approval = full text approval) and start it. `scope` != once learns an
    AssistantPolicy (always for the sender, domain or category). Commits and enqueues itself."""
    redaction = redaction if redaction in ("redacted", "unredacted") else "redacted"
    task.redaction = redaction
    if action_note:
        task.action_hint = action_note
    task.error = ""

    if scope in ("sender", "domain", "category"):
        sender_email, domain = parse_sender((task.meta or {}).get("from") or "")
        value = {"sender": sender_email, "domain": domain, "category": task.category}.get(scope, "")
        if value:
            await upsert_policy(db, task.owner_user_id, match_kind=scope, match_value=value,
                                auto_approve=True, redaction=redaction,
                                action_hint=action_note or task.action_hint or "")

    task.status = "approved"
    await db.commit()
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task.id}",
                        "assistant_task_id": task.id})


async def reject_assistant_task(db: AsyncSession, task: AssistantTask) -> None:
    task.status = "done"
    task.result = "(verworfen)"
    task.finished_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
