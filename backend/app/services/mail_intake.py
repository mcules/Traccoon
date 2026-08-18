"""Eingehende E-Mail → Ereignis `mail.received`. Der Auslöser, nicht der Ablauf.

Was mit der Mail geschieht — einordnen, auf Spam prüfen, nachfragen, wegräumen oder dem
Assistenten geben — steht seit dem Umstieg im Graphen des Slots `mail_intake`
(`workflow_seed.build_mail_intake`, Schritte in `mail_actions.py`). Hier bleibt nur, was
ein Auslöser können muss: dieselbe Mail nicht zweimal melden und die Einstellungen des
Webhooks mitgeben, damit die Schritte sie lesen können.

Der Ablauf entscheidet selbst, ob er zuhört (Trigger am Start-Knoten). Damit kann neben
dem ausgelieferten Ablauf ein zweiter stehen, ohne dass hier etwas umverdrahtet wird.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask
from .events import emit

log = logging.getLogger("traccoon.mail")


async def intake_mail(db: AsyncSession, owner_id: int | None, payload: dict, *,
                      source: str, classify_agent: str = "", agent: str = "assistent",
                      prompt_tmpl: str = "", ref_field: str = "",
                      auto_run: bool = False) -> list[int]:
    """Eine eingegangene Mail melden. → IDs der gestarteten Abläufe. Committet selbst.

    Idempotent über den Bezug (konfiguriertes Feld, sonst Konto:UID): sowohl gegen ein
    bereits angelegtes Assistent-Item (Bestand aus der Zeit vor dem Prozess) als auch
    gegen einen bereits gestarteten Ablauf — `emit` prüft den Bezug selbst.
    """
    account = str(payload.get("account") or "")
    uid = payload.get("uid")
    if ref_field:
        rv = payload.get(ref_field)
        src_ref = str(rv) if rv not in (None, "") else None
    else:
        src_ref = f"{account}:{uid}" if uid is not None else None

    if src_ref:
        dup = (await db.execute(select(AssistantTask).where(
            AssistantTask.source == source,
            AssistantTask.source_ref == src_ref))).scalar_one_or_none()
        if dup is not None:
            log.info("Mail %s bereits als Item #%s bearbeitet — kein zweiter Lauf",
                     src_ref, dup.id)
            return []

    # Die Einstellungen des Auslösers reisen im Kontext mit: der Ablauf gehört dem
    # Standard-Satz und darf nichts über den einzelnen Webhook wissen müssen.
    ids = await emit(
        db, "mail.received", payload={
            "mail": payload,
            "eingang": {"source": source, "source_ref": src_ref,
                        "classify_agent": classify_agent or "",
                        "agent": agent or "assistent", "prompt_tmpl": prompt_tmpl or "",
                        "ref_field": ref_field or "", "auto_run": bool(auto_run),
                        "owner_id": owner_id},
        }, actor_id=owner_id, source_ref=src_ref)
    await db.commit()
    if not ids:
        log.warning("Mail %s: kein Ablauf hört auf mail.received — nichts geschehen", src_ref)
    return ids
