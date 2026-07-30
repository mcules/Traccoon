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
from ..models.ops import PermAction, PermGrant, Permission, PermRequest
from ..models.ticket import Issue
from ..models.user import User
from ..services.assistant_inbox import (
    approve_assistant_task, create_chat_task, reject_assistant_task,
)
from ..services.artifacts import set_ticket_status
from ..services.comments import add_system_comment, apply_user_comment
from ..worker.assistant_gate import apply_perm_decision
from .mdtg import safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
OWNER_CHAT = os.getenv("TELEGRAM_OWNER_CHAT", "")


# Entscheidungen der Freigabe-Knöpfe im Klartext, für den Vermerk an der Nachricht.
_DEC_TEXT = {"once": "einmal", "always": "immer", "never": "nie"}


async def _erledigt(cq: CallbackQuery, vermerk: str) -> None:
    """Tastatur entfernen und den Ausgang an die Frage schreiben.

    Damit sieht man im Verlauf sofort, was noch offen ist: beantwortete Fragen tragen
    keine Knöpfe mehr, sondern eine Zeile mit Entscheidung und Zeitpunkt. Auch bei
    „schon erledigt" (anderswo entschieden) müssen die Knöpfe weg — sonst laden sie
    weiter zum Drücken ein.
    """
    msg = cq.message
    if msg is None:
        return
    zeile = f"<i>{safe(vermerk)} · {_now().strftime('%d.%m. %H:%M')}</i>"
    try:
        # html_text erhält die Formatierung der Ursprungsnachricht (Fettung, Zeilen).
        await msg.edit_text(f"{msg.html_text}\n\n{zeile}", parse_mode="HTML")
        return
    except Exception:  # noqa: BLE001
        # Zu alt zum Bearbeiten, ohne Text (Foto) oder unverändert — dann wenigstens
        # die Knöpfe abräumen, das ist der eigentliche Zweck.
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            log.warning("Konnte Tastatur an Nachricht %s nicht entfernen", msg.message_id)


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

    def _aperm_kb(tid: int) -> InlineKeyboardMarkup:
        # Tool-Freigabe des Assistenten (einmal|immer|nie).
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Einmal", callback_data=f"aperm:once:{tid}"),
            InlineKeyboardButton(text="Immer", callback_data=f"aperm:always:{tid}"),
            InlineKeyboardButton(text="Nie", callback_data=f"aperm:never:{tid}")]])

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
                        elif n.kind == "assistant_perm" and n.assistant_task_id:
                            markup = _aperm_kb(n.assistant_task_id)
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
        await m.answer("🦝 Traccoon-Bot. /tasks · /comment &lt;KEY&gt; &lt;Text&gt; · /uniwar &lt;Text&gt;")

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

    @dp.message(Command("uniwar"))
    async def _uniwar_chat(m: Message):
        # Chat mit dem UniWar-Operator statt mit dem persönlichen Assistenten. Gleicher Weg wie
        # `_assistant_chat`, nur mit gesetztem meta.agent — `_handle_assistant_task` löst den
        # Agenten daraus auf (sonst fällt es auf 'assistent' zurück).
        if not await _allowed(m.from_user.id):
            return
        text = (m.text or "").split(maxsplit=1)
        text = text[1].strip() if len(text) > 1 else ""
        if not text:
            await m.answer("Nutzung: /uniwar &lt;Frage oder Auftrag&gt;")
            return
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id),
                                   agent="uniwar-operator")
        await m.answer("🛰 …")

    @dp.message(F.reply_to_message)
    async def _reply(m: Message):
        if not await _allowed(m.from_user.id):
            return
        rt = m.reply_to_message.text or m.reply_to_message.html_text or ""

        # Banking-2FA: Antwort auf die „Banking-Sync braucht einen 2FA-Code für <source>"-Karte
        # → OTP über das banking-MCP an submit_auth weiterreichen (ersetzt den Hermes-Relay).
        bm = re.search(r"2FA-Code für (.+?) \(Auth-Request", rt)
        if bm and "Banking-Sync" in rt:
            source, code = bm.group(1).strip(), (m.text or "").strip()
            try:
                from ..worker.mcp_client import mcp_session
                spec = {"name": "banking", "transport": "http",
                        "url": "http://banking-mcp:3010/mcp", "headers": {}}
                async with mcp_session("bot", servers=[spec], gateway_url="", gateway_token="") as mcp:
                    await mcp.call("banking__submit_auth", {"source": source, "code": code})
                await m.answer(f"✅ 2FA-Code an {source} weitergeleitet.")
            except Exception as exc:  # noqa: BLE001
                await m.answer(f"⚠ Weiterleitung an {source} fehlgeschlagen: {exc}")
            return

        # Ohne Ticket-Bezug ist die Antwort schlicht eine Chat-Nachricht an den Assistenten.
        match = re.search(r"\[([A-Z][A-Z0-9]*-\d+)\]", rt)
        if not match:
            await _chat_auftrag(m)
            return
        key = match.group(1)
        async with SessionLocal() as db:
            iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
            if iss is None:
                await _chat_auftrag(m)
                return
            user = await _acting_user(db, m.chat.id)
            await apply_user_comment(db, iss, m.text or "", user.id if user else None, "Telegram")
        await m.answer(f"↳ Kommentar zu {key} gespeichert.")

    async def _chat_auftrag(m: Message) -> bool:
        """Klartext an den persönlichen Assistenten übergeben. True = angenommen.

        Auch der Reply-Zweig landet hier: eine Antwort auf eine Assistenten-Nachricht ist im
        Chat das Natürlichste — vorher fiel sie durch alle Handler und wurde kommentarlos
        verworfen, von außen nicht von „ignoriert" zu unterscheiden.
        """
        text = (m.text or "").strip()
        if not text or text.startswith("/"):
            return False
        async with SessionLocal() as db:
            user = await _acting_user(db, m.chat.id)
            await create_chat_task(db, user.id if user else None, text, str(m.chat.id))
        await m.answer("🤖 …")
        return True

    @dp.message(F.text)
    async def _assistant_chat(m: Message):
        # Klartext (kein Command, keine Ticket-Antwort) → Chat mit dem Assistenten.
        # Registriert NACH Commands/Reply → die haben Vorrang.
        if not await _allowed(m.from_user.id):
            return
        await _chat_auftrag(m)

    @dp.message()
    async def _unsupported(m: Message):
        # Alles ohne Text (Sprachnachricht, Foto, Sticker, Dokument) fiel bisher durch alle
        # Handler und wurde KOMMENTARLOS verworfen — von außen nicht von „ignoriert" zu
        # unterscheiden. Lieber ehrlich absagen.
        if not m.from_user or not await _allowed(m.from_user.id):
            return
        await m.answer("🙉 Damit kann ich noch nichts anfangen — schick es mir als Text.")

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
                        await set_ticket_status(db, iss, TicketAgentStatus.approved)
                        await add_system_comment(db, iss.id, f"✅ Plan freigegeben von {who}")
                    else:
                        iss.plan = None
                        await set_ticket_status(db, iss, None, board=False)
                        await add_system_comment(db, iss.id, f"✖ Plan abgelehnt von {who}")
                    iss.hold_reason = None
                    await db.commit()
                    await cq.answer("OK")
                    await _erledigt(cq, "✅ Plan freigegeben" if data.startswith("approve:")
                                    else "✖ Plan abgelehnt")
                else:
                    await cq.answer("nicht mehr offen")
                    await _erledigt(cq, "⏭ nicht mehr offen (anderswo entschieden)")
            elif data.startswith("accept:"):
                key = data.split(":", 1)[1]
                iss = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
                if iss and iss.agent_status in (TicketAgentStatus.to_test, TicketAgentStatus.testing):
                    await set_ticket_status(db, iss, TicketAgentStatus.done)
                    iss.resolved_at = _now()
                    iss.hold_reason = None
                    await db.commit()
                    from ..core.redis import enqueue_task
                    await enqueue_task({"kind": "accept", "task_id": f"accept-{iss.key}",
                                        "issue_id": iss.id, "project_id": iss.project_id})
                    await cq.answer("Abgenommen")
                    await _erledigt(cq, "✅ Abgenommen")
                else:
                    await cq.answer("nicht mehr offen")
                    await _erledigt(cq, "⏭ nicht mehr offen (anderswo entschieden)")
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
                        await set_ticket_status(db, iss, TicketAgentStatus.approved)
                        iss.hold_reason = None
                        iss.continuation_count += 1
                    await db.commit()
                    await cq.answer(f"Berechtigung: {dec}")
                    await _erledigt(cq, f"🔑 Berechtigung: {_DEC_TEXT.get(dec, dec)}")
                else:
                    await cq.answer("schon entschieden")
                    await _erledigt(cq, "⏭ schon entschieden")
            elif data.startswith("atask:"):
                _, action, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None:
                    await cq.answer("Nicht gefunden")
                    await _erledigt(cq, "⏭ Aufgabe nicht mehr vorhanden")
                elif t.status not in ("new", "error"):
                    await cq.answer(f"schon erledigt ({t.status})")
                    await _erledigt(cq, f"⏭ schon erledigt ({t.status})")
                elif action == "reject":
                    await reject_assistant_task(db, t)
                    await cq.answer("Verworfen")
                    await _erledigt(cq, "❌ Verworfen")
                else:
                    scope = {"sender": "sender", "category": "category"}.get(action, "once")
                    # Schnellfreigabe per Telegram ist geschwärzt (sicher); ungeschwärzt regelt die Web-Inbox.
                    await approve_assistant_task(db, t, scope=scope, redaction="redacted")
                    await cq.answer("Freigegeben" + ("" if scope == "once" else " + gemerkt"))
                    await _erledigt(cq, "✅ Freigegeben" + {
                        "sender": " · Absender künftig automatisch",
                        "category": " · Kategorie künftig automatisch"}.get(scope, ""))
            elif data.startswith("aperm:"):
                _, dec, sid = data.split(":", 2)
                t = await db.get(AssistantTask, int(sid))
                if t is None or t.status != "awaiting":
                    await cq.answer("schon entschieden")
                    await _erledigt(cq, "⏭ schon entschieden")
                else:
                    await apply_perm_decision(db, t, dec)
                    await cq.answer(f"Freigabe: {dec}")
                    await _erledigt(cq, f"🔑 Freigabe: {_DEC_TEXT.get(dec, dec)}")
        await cq.answer()

    log.info("Traccoon-Bot gestartet (allowed=%s)", ALLOWED or "alle")
    asyncio.create_task(notifier())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
