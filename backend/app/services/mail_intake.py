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
from .assistant_policy import agent_laeuft_lokal, match_policy, note_hit, parse_sender
from .mail_classify import classify_email

log = logging.getLogger("traccoon.mail")

def _fill_prompt(tmpl: str, payload: dict) -> str:
    """{platzhalter} aus dem Payload füllen — JEDES Payload-Feld (mail ODER paperless-linked:
    {document_id}/{url}/{note}/{hinweis}…). Fehlende bleiben leer. Sichere Ersetzung (kein
    str.format, damit Code-Beispiele mit { } im Prompt nicht brechen)."""
    out = tmpl
    for k, v in (payload or {}).items():
        out = out.replace("{" + k + "}", "" if v is None else str(v))
    # body_text als Alias für body (Mail-Prompt).
    out = out.replace("{body_text}", str(payload.get("body_text", payload.get("body", "")) or ""))
    # Übrige, nicht gelieferte Platzhalter neutralisieren (leeren).
    import re
    return re.sub(r"\{[a-z_]+\}", "", out)


async def intake_mail(db: AsyncSession, owner_id: int | None, payload: dict, *,
                      source: str, classify_agent: str = "", agent: str = "assistent",
                      prompt_tmpl: str = "", ref_field: str = "",
                      auto_run: bool = False) -> tuple[AssistantTask | None, bool]:
    """(task, auto). Idempotent über (source, account:uid). Committet selbst; enqueued NICHT.
    Ohne `classify_agent` = 1:1-Passthrough (KEINE Klassifizierung; der Agent liest die
    Mail selbst per IMAP und handelt). Mit `classify_agent` = lokale Vorklassifizierung/Schwärzung.
    `agent` = welcher Agent die Mail bearbeitet (aus dem Webhook, Default 'assistent')."""
    account = str(payload.get("account") or "")
    subject = str(payload.get("subject") or "")
    sender = str(payload.get("from") or "")
    uid = payload.get("uid")
    body = str(payload.get("body") or "")

    # Idempotenz-Schlüssel: konfiguriertes Feld (z. B. document_id für paperless-linked) → sonst account:uid.
    if ref_field:
        rv = payload.get(ref_field)
        src_ref = str(rv) if rv not in (None, "") else None
    else:
        src_ref = f"{account}:{uid}" if uid is not None else None
    if src_ref:
        from sqlalchemy import select
        dup = (await db.execute(select(AssistantTask).where(
            AssistantTask.source == source,
            AssistantTask.source_ref == src_ref))).scalar_one_or_none()
        if dup is not None:
            return dup, False

    if classify_agent:
        cls = await classify_email(db, owner_id, account=account, sender=sender,
                                   subject=subject, body=body, classify_agent=classify_agent)
    else:
        # Passthrough wie im Vorläufer: keine Klassifizierung, keine Schwärzung.
        cls = {"category": "", "priority": "normal", "sensitive": False, "redacted_summary": ""}

    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=cls["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve
    # Schwärzen schützt davor, dass Rohtext das Haus verlässt. Bearbeitet die Mail ein
    # Modell auf dem eigenen Endpoint, verlässt nichts das Haus — dann ist die Schwärzung
    # kein Schutz mehr, sondern nur noch Informationsverlust (und ein Umweg über die
    # IMAP-Tools, der denselben Text ohnehin wieder heranholt).
    if await agent_laeuft_lokal(db, owner_id, agent or "assistent"):
        redaction = "unredacted"
    if auto_run:  # Webhook erzwingt chatlosen Sofortlauf (z. B. paperless-linked Link-back).
        auto = True

    task = AssistantTask(
        owner_user_id=owner_id, kind="email", source=source, source_ref=src_ref,
        title=(subject or "(kein Betreff)")[:500],
        category=cls["category"], priority=cls["priority"], redacted_summary=cls["redacted_summary"],
        meta={"account": account, "uid": uid, "from": sender, "subject": subject,
              "sensitive": cls["sensitive"], "agent": agent or "assistent",
              # Voller Task-Prompt aus dem Webhook (Mail-Wissen), Platzhalter gefüllt.
              **({"prompt": _fill_prompt(prompt_tmpl, payload)} if prompt_tmpl else {})},
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
