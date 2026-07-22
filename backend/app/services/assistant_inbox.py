"""Freigabe/Verwerfen von Assistent-Eingängen — geteilt von Web-API und Telegram-Bot,
damit beide Oberflächen exakt dasselbe tun (parallel nutzbar, immer synchron)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask
from .assistant_policy import parse_sender, upsert_policy


async def approve_assistant_task(db: AsyncSession, task: AssistantTask, *, scope: str = "once",
                                 redaction: str = "redacted", action_note: str = "") -> None:
    """Item freigeben (Freigabe = Volltext-Freigabe) und starten. `scope` != once lernt eine
    AssistantPolicy (immer für Absender/Domain/Kategorie). Committet + enqueued selbst."""
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
