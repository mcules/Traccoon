"""Traccoon Telegram-Bot (aiogram v3). Teilt das Backend-Image (python -m app.bot).

Notifier-Poll (Notification → Telegram), Reply→Kommentar (geteilte apply_user_comment),
Inline-Buttons (approve/reject/accept/perm), Commands /tasks /comment.
No-op (stabiler Sleep) wenn kein TELEGRAM_BOT_TOKEN gesetzt.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re

from sqlalchemy import select

from ..db import SessionLocal
from ..models.assistant import AssistantTask
from ..models.enums import GlobalRole, TicketAgentStatus
from ..models.notification import Notification
from ..models.nexus import PermAction, PermGrant, Permission, PermRequest
from ..models.ticket import Issue
from ..models.user import User
from ..services.assistant_inbox import approve_assistant_task, reject_assistant_task
from ..services.comments import add_system_comment, apply_user_comment
from .mdtg import safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


async def _acting_user(db, chat_id: str) -> User | None:
    u = (await db.execute(select(User).where(User.telegram_chat_id == str(chat_id)))).scalar_one_or_none()
    if u:
        return u
    return (await db.execute(select(User).where(User.global_role == GlobalRole.admin).order_by(User.id))).scalars().first()


async def run_bot() -> None:
    if not TOKEN:
        log.warning("Kein TELEGRAM_BOT_TOKEN — Bot im Ruhemodus.")
        while True:
            await asyncio.sleep(3600)

    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

    bot = Bot(TOKEN)
    dp = Dispatcher()

    async def _allowed(uid: int) -> bool:
        # Erlaubt: Env-Bootstrap (TELEGRAM_ALLOWED_IDS) ODER jeder User, der seine
        # Chat-ID in der WebUI hinterlegt hat. DB-Lookup pro Aufruf → neue User
        # greifen sofort, ohne Bot-Neustart / Env-Edit.
        if uid in ALLOWED:
            return True
        async with SessionLocal() as db:
            u = (await db.execute(
                select(User).where(User.telegram_chat_id == str(uid)))).scalar_one_or_none()
            return u is not None

    def _kb_for(kind: str, issue_key: str, req_id: int | None) -> InlineKeyboardMarkup | None:
        if kind == "plan_review":
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Freigeben", callback_data=f"approve:{issue_key}"),
                InlineKeyboardButton(text="✖ Ablehnen", callback_data=f"reject:{issue_key}")]])
        if kind == "to_test":
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Abnehmen", callback_data=f"accept:{issue_key}")]])
        if kind == "blocked" and req_id:
            return InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Einmal", callback_data=f"perm:once:{req_id}"),
                InlineKeyboardButton(text="Immer", callback_data=f"perm:always:{req_id}"),
                InlineKeyboardButton(text="Nie", callback_data=f"perm:never:{req_id}")]])
        return None

    def _atask_kb(tid: int) -> InlineKeyboardMarkup:
        # Freigabe eines Assistent-Eingangs. Schnellfreigabe ist geschwärzt (sicher);
        # ungeschwärzt/feineres regelt die Web-Inbox.
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Freigeben", callback_data=f"atask:approve:{tid}"),
             InlineKeyboardButton(text="❌ Verwerfen", callback_data=f"atask:reject:{tid}")],
            [InlineKeyboardButton(text="♾️ Immer Absender", callback_data=f"atask:sender:{tid}"),
             InlineKeyboardButton(text="♾️ Immer Kategorie", callback_data=f"atask:category:{tid}")]])

    async def notifier() -> None:
        while True:
            try:
                async with SessionLocal() as db:
                    rows = (
                        await db.execute(select(Notification).where(
                            Notification.chat_id.isnot(None), Notification.notified_at.is_(None))
                            .order_by(Notification.id).limit(10))
                    ).scalars().all()
                    for n in rows:
                        issue_key = ""
                        req_id = None
                        if n.issue_id:
                            iss = await db.get(Issue, n.issue_id)
                            issue_key = iss.key if iss else ""
                            if n.kind == "blocked":
                                pr = (await db.execute(select(PermRequest).where(
                                    PermRequest.issue_id == n.issue_id, PermRequest.status == "pending")
                                    .order_by(PermRequest.id.desc()))).scalars().first()
                                req_id = pr.id if pr else None
                        text = f"<b>{safe(n.title)}</b>\n{safe(n.body)}" + (f"\n[{issue_key}]" if issue_key else "")
                        if n.kind == "assistant_review" and n.assistant_task_id:
                            markup = _atask_kb(n.assistant_task_id)
                        else:
                            markup = _kb_for(n.kind, issue_key, req_id)
                        try:
                            await bot.send_message(int(n.chat_id), text, parse_mode="HTML",
                                                   reply_markup=markup)
                            n.notified_at = _now()
                        except Exception:  # noqa: BLE001
                            log.exception("Send an %s fehlgeschlagen", n.chat_id)
                            n.notified_at = _now()  # nicht endlos retry
                    await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("notifier-Fehler")
            await asyncio.sleep(3)

    @dp.message(Command("start"))
    async def _start(m: Message):
        await m.answer("🦝 Traccoon-Bot. /tasks · /comment &lt;KEY&gt; &lt;Text&gt;")

    @dp.message(Command("tasks"))
    async def _tasks(m: Message):
        if not await _allowed(m.from_user.id):
            return
        async with SessionLocal() as db:
            rows = (await db.execute(select(Issue).where(Issue.assigned_agent.isnot(None))
                    .order_by(Issue.updated_at.desc()).limit(15))).scalars().all()
        if not rows:
            await m.answer("Keine zugewiesenen Tickets.")
            return
        await m.answer("\n".join(f"[{i.key}] {i.agent_status} — {i.summary}" for i in rows))

    @dp.message(Command("comment"))
    async def _comment(m: Message):
        if not await _allowed(m.from_user.id):
            return
        parts = (m.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await m.answer("Nutzung: /comment KEY Text")
            return
        key, text = parts[1], parts[2]
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                await m.answer("Ticket nicht gefunden.")
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, text, user.id if user else None, "Telegram")
        await m.answer(f"Kommentar zu {key} gespeichert.")

    @dp.message(F.reply_to_message)
    async def _reply(m: Message):
        if not await _allowed(m.from_user.id):
            return
        rt = m.reply_to_message.text or m.reply_to_message.html_text or ""
        match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", rt)
        if not match:
            return
        key = match.group(1)
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, m.text or "", user.id if user else None, "Telegram")
        await m.answer(f"↳ Kommentar zu {key} gespeichert.")

    @dp.message(F.text)
    async def _assistant_chat(m: Message):
        # Klartext (kein Command, keine Ticket-Antwort) → Chat mit dem persönlichen Assistenten.
        # Er bedient Traccoon (traccoon_*-Tools, in deinen Rechten) und deine MCP; Antwort kommt
        # als Notification zurück. Registriert NACH Commands/Reply → die haben Vorrang.
        if not await _allowed(m.from_user.id):
            return
        text = (m.text or "").strip()
        if not text or text.startswith("/"):
            return
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            t = AssistantTask(owner_user_id=user.id if user else None, kind="chat",
                              source="telegram", title=text[:200], status="approved",
                              meta={"chat_text": text, "chat_id": str(m.chat.id)})
            db.add(t)
            await db.commit()
            await db.refresh(t)
        from ..core.redis import enqueue_task
        await enqueue_task({"kind": "assistant", "task_id": f"assistant-{t.id}",
                            "assistant_task_id": t.id})
        await m.answer("🤖 …")

    @dp.callback_query()
    async def _cb(cq: CallbackQuery):
        if not await _allowed(cq.from_user.id):
            await cq.answer("Nicht erlaubt")
            return
        data = cq.data or ""
        async with SessionLocal() as db:
            if data.startswith("approve:") or data.startswith("reject:"):
                key = data.split(":", 1)[1]
                iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
                if iss and iss.agent_status == TicketAgentStatus.plan_review:
                    who = f"{cq.from_user.first_name or cq.from_user.id} (Telegram)"
                    if data.startswith("approve:"):
                        iss.agent_status = TicketAgentStatus.approved
                        await add_system_comment(db, iss.id, f"✅ Plan freigegeben von {who}")
                    else:
                        iss.plan = None
                        iss.agent_status = None
                        await add_system_comment(db, iss.id, f"✖ Plan abgelehnt von {who}")
                    iss.hold_reason = None
                    await db.commit()
                    await cq.answer("OK")
            elif data.startswith("accept:"):
                key = data.split(":", 1)[1]
                iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
                if iss and iss.agent_status in (TicketAgentStatus.to_test, TicketAgentStatus.testing):
                    iss.agent_status = TicketAgentStatus.done
                    iss.resolved_at = _now()
                    iss.hold_reason = None
                    await db.commit()
                    from ..core.redis import enqueue_task
                    await enqueue_task({"kind": "accept", "task_id": f"accept-{iss.key}",
                                        "issue_id": iss.id, "project_id": iss.project_id})
                    await cq.answer("Abgenommen")
            elif data.startswith("perm:"):
                _, dec, rid = data.split(":", 2)
                pr = await db.get(PermRequest, int(rid))
                if pr and pr.status == "pending":
                    iss = await db.get(Issue, pr.issue_id)
                    if dec == "once":
                        db.add(PermGrant(issue_id=pr.issue_id, tool=pr.tool, resource=pr.resource))
                    elif dec == "always":
                        db.add(Permission(project_id=iss.project_id, tool=pr.tool, resource="*", action=PermAction.allow))
                    elif dec == "never":
                        db.add(Permission(project_id=iss.project_id, tool=pr.tool, resource="*", action=PermAction.deny))
                    pr.status = "decided"
                    pr.decision = dec
                    pr.decided_at = _now()
                    if iss and iss.agent_status == TicketAgentStatus.hold and dec != "never":
                        iss.agent_status = TicketAgentStatus.approved
                        iss.hold_reason = None
                        iss.continuation_count += 1
                    await db.commit()
                    await cq.answer(f"Berechtigung: {dec}")
            elif data.startswith("atask:"):
                _, action, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None:
                    await cq.answer("Nicht gefunden")
                elif t.status not in ("new", "error"):
                    await cq.answer(f"schon erledigt ({t.status})")
                elif action == "reject":
                    await reject_assistant_task(db, t)
                    await cq.answer("Verworfen")
                else:
                    scope = {"sender": "sender", "category": "category"}.get(action, "once")
                    # Schnellfreigabe per Telegram ist geschwärzt (sicher); ungeschwärzt regelt die Web-Inbox.
                    await approve_assistant_task(db, t, scope=scope, redaction="redacted")
                    await cq.answer("Freigegeben" + ("" if scope == "once" else " + gemerkt"))
        await cq.answer()

    log.info("Traccoon-Bot gestartet (allowed=%s)", ALLOWED or "alle")
    asyncio.create_task(notifier())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
