"""Freigabe/Verwerfen von Assistent-Eingängen — geteilt von Web-API und Telegram-Bot,
damit beide Oberflächen exakt dasselbe tun (parallel nutzbar, immer synchron)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask
from .assistant_policy import parse_sender, upsert_policy


async def _finde_quelle(db: AsyncSession, owner_user_id: int | None, chat_id: str,
                        bezug: str) -> int | None:
    """Zu welchem Eingang gehört die zitierte Nachricht? → assistant_task_id oder None.

    Telegram-Nachrichten des Assistenten entstehen aus Notification-Zeilen
    (`<b>{title}</b>\n{body}`). Die erste Zeile des Zitats ist also der Titel — darüber
    findet sich die Benachrichtigung und mit ihr die Aufgabe."""
    from sqlalchemy import select

    from ..models.notification import Notification
    titel = (bezug.strip().splitlines() or [""])[0].strip()
    if not titel:
        return None
    row = (await db.execute(
        select(Notification).where(
            Notification.chat_id == str(chat_id),
            Notification.assistant_task_id.isnot(None),
            Notification.title == titel,
        ).order_by(Notification.id.desc()).limit(1))).scalars().first()
    return row.assistant_task_id if row else None


async def create_chat_task(db: AsyncSession, owner_user_id: int | None, text: str,
                           chat_id: str, agent: str = "", bezug: str = "") -> AssistantTask:
    """Chat-Nachricht an den Assistenten übergeben (angelegt + eingereiht).

    Geteilt, damit jeder Einstieg dasselbe tut — im Bot hing das an einem einzigen
    Handler, und eine ANTWORT auf eine Assistenten-Nachricht fiel dadurch aus dem Chat
    heraus und wurde kommentarlos verworfen.

    `bezug` ist die zitierte Nachricht des Assistenten. Sie wird mitgeführt und im Lauf
    vorangestellt: eine Antwort meint genau diese eine Nachricht, nicht das Gespräch im
    Allgemeinen. Gehört sie zu einem früheren Eingang (Mail, Freigabe), wird auch dessen
    Aufgabe verlinkt, damit der Assistent dort weiterarbeitet statt neu anzufangen."""
    meta = {"chat_text": text, "chat_id": str(chat_id)}
    if agent:
        meta["agent"] = agent        # sonst fällt der Lauf auf 'assistent' zurück
    if bezug.strip():
        meta["bezug_text"] = bezug.strip()[:2000]
        quelle = await _finde_quelle(db, owner_user_id, str(chat_id), bezug)
        if quelle is not None:
            meta["bezug_task_id"] = quelle
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
