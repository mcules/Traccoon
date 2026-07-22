"""Eingehende E-Mail → projektlose AssistantTask. Geteilt vom (normalen) Webhook-Modus
'assistant'. Lokale Vorklassifizierung durch den Klassifizier-Agenten (dessen Modell),
gelernte Policy (Auto-Freigabe/Schwärzung/Aktion), dann Telegram-Freigabekarte.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask
from ..models.notification import Notification
from ..models.user import User
from .assistant_policy import match_policy, note_hit, parse_sender
from .mail_classify import classify_email

log = logging.getLogger("traccoon.mail")


async def intake_mail(db: AsyncSession, owner_id: int | None, payload: dict, *,
                      source: str, classify_agent: str = "") -> tuple[AssistantTask | None, bool]:
    """(task, auto). Idempotent über (source, account:uid). Committet selbst; enqueued NICHT
    (das macht der Aufrufer, um Redis-Import im Request-Pfad zu halten)."""
    account = str(payload.get("account") or "")
    subject = str(payload.get("subject") or "")
    sender = str(payload.get("from") or "")
    uid = payload.get("uid")
    body = str(payload.get("body") or "")

    src_ref = f"{account}:{uid}" if uid is not None else None
    if src_ref:
        from sqlalchemy import select
        dup = (await db.execute(select(AssistantTask).where(
            AssistantTask.source == source,
            AssistantTask.source_ref == src_ref))).scalar_one_or_none()
        if dup is not None:
            return dup, False

    cls = await classify_email(db, owner_id, account=account, sender=sender,
                               subject=subject, body=body, classify_agent=classify_agent)

    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=cls["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve

    task = AssistantTask(
        owner_user_id=owner_id, kind="email", source=source, source_ref=src_ref,
        title=(subject or "(kein Betreff)")[:500],
        category=cls["category"], priority=cls["priority"], redacted_summary=cls["redacted_summary"],
        meta={"account": account, "uid": uid, "from": sender, "subject": subject,
              "sensitive": cls["sensitive"]},
        redaction=redaction, action_hint=action_hint or "",
        raw_body=(body if redaction == "unredacted" else None),
        status=("approved" if auto else "new"),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    if not auto and owner_id:
        owner = await db.get(User, owner_id)
        if owner and owner.telegram_chat_id:
            db.add(Notification(
                user_id=owner_id, assistant_task_id=task.id, kind="assistant_review",
                chat_id=owner.telegram_chat_id, title=f"📥 {task.title}"[:200],
                body=(f"von {sender}\n{cls['redacted_summary']}")[:1000]))
            await db.commit()

    log.info("Mail-Item #%s (%s, prio=%s, regel=%s, auto=%s)",
             task.id, cls["category"], cls["priority"], policy.id if policy else None, auto)
    return task, auto
