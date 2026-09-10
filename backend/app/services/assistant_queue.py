"""What is typed while the assistant is still working.

Sending a second message used to start a second run beside the first. Two runs in
one conversation answer each other's questions, and the second one begins without
what the first is about to find out — so the person gets two half answers to what
they meant as one thought.

So a message that arrives while something is running is put behind it, and when
the running one is done everything waiting is handed over **as one message**. Not
one after another: somebody who writes three sentences in a row is adding to what
they said, not asking three separate questions, and answering them separately
means answering the first two without knowing the third.

The messages stay in the conversation as they were typed — that is the record of
what somebody wrote. Only the work is merged.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantTask

log = logging.getLogger("traccoon.assistant.queue")

# What counts as "there is already something going on in this conversation".
# `queued` is in here as well, so a third message joins the queue instead of
# starting a run beside the second.
BUSY = ("new", "approved", "queued", "running", "awaiting")

# The status of a message whose text has been folded into a later one. Terminal,
# and deliberately not an error: nothing went wrong, it was simply answered
# together with what came after it.
MERGED = "merged"


async def busy(db: AsyncSession, session_id: int | None) -> bool:
    """Is something being worked on in this conversation right now?"""
    if not session_id:
        return False
    row = (await db.execute(select(AssistantTask.id).where(
        AssistantTask.session_id == session_id,
        AssistantTask.status.in_(BUSY)).limit(1))).scalar_one_or_none()
    return row is not None


def text_of(t: AssistantTask) -> str:
    return (t.meta or {}).get("chat_text") or t.title or ""


def join(texts) -> str:
    """The waiting messages as one, without saying the same thing three times.

    Each message carries the context the page offered along with it — which note
    was open, which day. Three messages typed in a row carry it three times, and
    joined verbatim the result reads as three separate requests that each begin
    by introducing themselves. So a paragraph that has already been said is not
    said again; what somebody actually wrote is never dropped.
    """
    out: list[str] = []
    gesagt: set[str] = set()
    for text in texts:
        for absatz in (text or "").split("\n\n"):
            klar = absatz.strip()
            if not klar or klar in gesagt:
                continue
            gesagt.add(klar)
            out.append(klar)
    return "\n\n".join(out)


async def release(db: AsyncSession, session_id: int | None) -> AssistantTask | None:
    """Hand the waiting messages over as one, and say which one carries them.

    The last of them is the one that runs: the answer belongs under the sentence
    that finished the thought, which is where somebody looks for it. The earlier
    ones keep their text and are marked as folded in.
    """
    if not session_id:
        return None
    waiting = list((await db.execute(select(AssistantTask).where(
        AssistantTask.session_id == session_id,
        AssistantTask.status == "queued").order_by(AssistantTask.id))).scalars().all())
    if not waiting:
        return None

    carrier = waiting[-1]
    if len(waiting) > 1:
        joined = join(text_of(x) for x in waiting)
        carrier.meta = {**(carrier.meta or {}), "chat_text": joined,
                        # So the answer can say what it is answering.
                        "merged_from": [x.id for x in waiting[:-1]]}
        for earlier in waiting[:-1]:
            earlier.status = MERGED
            earlier.result = ""
    carrier.status = "approved"
    await db.commit()
    await db.refresh(carrier)

    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{carrier.id}",
                        "assistant_task_id": carrier.id, "is_chat": True})
    log.info("assistant queue: %d message(s) released in session %s as task %s",
             len(waiting), session_id, carrier.id)
    return carrier
