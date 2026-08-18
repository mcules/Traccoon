"""Incoming e-mail becomes the event `mail.received`. The trigger, not the flow.

What happens to the mail (classifying, checking for spam, asking, clearing away or giving it
to the assistant) has stood in the graph of the slot `mail_intake` since the changeover
(`workflow_seed.build_mail_intake`, steps in `mail_actions.py`). What remains here is what a
trigger has to be able to do: not report the same mail twice and pass the settings of the
webhook along so that the steps can read them.

The flow decides itself whether it listens (the trigger on the start node). That way a second
flow can stand beside the shipped one without anything being rewired here.
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
    """Report an incoming mail. Returns the ids of the started flows. Commits itself.

    Idempotent over the reference (configured field, otherwise account:UID): both against an
    already created assistant item (existing data from the time before the process) and
    against an already started flow; `emit` checks the reference itself.
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

    # The settings of the trigger travel along in the context: the flow belongs to the
    # default set and must not have to know anything about the individual webhook.
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
