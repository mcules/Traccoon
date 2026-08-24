"""Conversations of the personal assistant: create, load, switch, close, delete.

Shared by all three ways in — the web interface and the Obsidian plugin over the API, and the
Telegram bot — so that loading a conversation means the same thing everywhere. The bot has no
choice but to go through the pointer (a chat message carries no parameter), the API clients
pass `session_id`; underneath, both end up here.

The one rule that runs through the whole file: a session belongs to exactly ONE agent. The
GameProj operator keeps a conversation of its own, and a message that lands in the wrong one
poisons both threads at once.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantChannelSession, AssistantSession, AssistantTask

log = logging.getLogger("traccoon.assistant.sessions")

# What counts as "something is still going on in there". Same set as the chat archive uses:
# a message that is being worked on must not be swept away underneath the answer.
RUNNING = ("new", "approved", "running", "awaiting")

# Channels that may hold a pointer. Deliberately a closed list: a typo would otherwise create
# a second, silently unused pointer instead of failing.
CHANNELS = ("telegram", "web")

TITLE_MAX = 60


def now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def title_from(text: str) -> str:
    """A heading out of the first message: up to 60 characters, cut at a word boundary.

    A list of conversations all called "Neue Unterhaltung" is a list nobody can navigate, and
    the first sentence is nearly always what the conversation turned out to be about.
    """
    line = " ".join((text or "").split())
    if len(line) <= TITLE_MAX:
        return line
    cut = line[:TITLE_MAX]
    space = cut.rfind(" ")
    # Only cut at the space when it leaves something to read; a first "word" of sixty
    # characters (a URL, a path) is better shown truncated than as nothing.
    return (cut[:space] if space >= TITLE_MAX // 2 else cut).rstrip() + "…"


def payload_of(s: AssistantSession, **extra) -> dict:
    """What an event carries about a session. Taken while the row still exists, so that the
    deletion event can be reported AFTER the row is gone."""
    return {"id": s.id, "agent": s.agent, "title": s.title,
            "owner_user_id": s.owner_user_id, **extra}


async def emit(db: AsyncSession, event: str, session: dict) -> None:
    """Report a session event, in the same shape as the issue events.

    A failing flow must not break the click that caused it, exactly as with `events.emit`
    itself — which is why this is wrapped once more here: creating a conversation is not
    allowed to fail because somebody's flow is broken.
    """
    from .events import emit as emit_event
    try:
        await emit_event(db, event, actor_id=session.get("owner_user_id"),
                         payload={"session": session})
    except Exception:  # noqa: BLE001
        log.exception("Event %s for session %s could not be reported", event, session.get("id"))


async def create(db: AsyncSession, owner_user_id: int | None, *, title: str = "",
                 agent: str = "assistent", commit: bool = True) -> AssistantSession:
    """A new conversation. Without a title it stays empty until the first message names it."""
    s = AssistantSession(owner_user_id=owner_user_id, agent=(agent or "assistent").strip(),
                         title=(title or "").strip()[:200], meta={})
    db.add(s)
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(s)
    await emit(db, "assistant.session_created", payload_of(s))
    return s


async def get_owned(db: AsyncSession, session_id: int,
                    owner_user_id: int | None) -> AssistantSession | None:
    s = await db.get(AssistantSession, int(session_id))
    if s is None or s.owner_user_id != owner_user_id:
        return None
    return s


async def pointer(db: AsyncSession, owner_user_id: int | None,
                  channel: str) -> AssistantChannelSession | None:
    return (await db.execute(select(AssistantChannelSession).where(
        AssistantChannelSession.owner_user_id == owner_user_id,
        AssistantChannelSession.channel == channel))).scalars().first()


async def load(db: AsyncSession, owner_user_id: int | None, channel: str,
               session_id: int | None, *, commit: bool = True) -> None:
    """Point a channel at a conversation. Loading IS this, and nothing else."""
    row = await pointer(db, owner_user_id, channel)
    if row is None:
        row = AssistantChannelSession(owner_user_id=owner_user_id, channel=channel)
        db.add(row)
    row.session_id = session_id
    if commit:
        await db.commit()


async def current(db: AsyncSession, owner_user_id: int | None, channel: str,
                  agent: str = "assistent") -> AssistantSession | None:
    """The conversation this channel is in — or None when it is in none.

    A pointer at a session that has been deleted meanwhile answers None as well, so the
    caller creates a fresh one instead of running into a dangling number. The agent is
    checked too: `/agent gameproj …` must not land in the assistant's conversation just because
    that is the one the person had loaded.
    """
    row = await pointer(db, owner_user_id, channel)
    if row is None or row.session_id is None:
        return None
    s = await db.get(AssistantSession, row.session_id)
    if s is None or s.owner_user_id != owner_user_id:
        return None
    if agent and s.agent != agent:
        return None
    return s


async def for_message(db: AsyncSession, owner_user_id: int | None, channel: str, text: str,
                      agent: str = "assistent",
                      session_id: int | None = None) -> AssistantSession:
    """The session a new message belongs in — the heart of the whole thing.

    Named explicitly it is that one (a closed conversation included: whoever loads it again
    wants to carry on in it). Otherwise the pointer of the channel decides, and when that
    points at nothing a fresh conversation comes into being. A client that knows nothing of
    sessions must still be able to send.

    The first message gives an untitled conversation its heading, here and not in the caller,
    so that all three ways in do it the same.
    """
    s: AssistantSession | None = None
    if session_id:
        s = await get_owned(db, session_id, owner_user_id)
    if s is None:
        s = await current(db, owner_user_id, channel, agent=agent)
    if s is None:
        s = await create(db, owner_user_id, agent=agent, commit=False)
    if not s.title:
        s.title = title_from(text)[:200]
    s.last_message_at = now()
    await load(db, owner_user_id, channel, s.id, commit=False)
    return s


async def close(db: AsyncSession, s: AssistantSession, *, commit: bool = True) -> None:
    if s.closed_at is None:
        s.closed_at = now()
        if commit:
            await db.commit()
        await emit(db, "assistant.session_closed", payload_of(s))


async def reopen(db: AsyncSession, s: AssistantSession, *, commit: bool = True) -> None:
    s.closed_at = None
    if commit:
        await db.commit()


async def running_ids(db: AsyncSession, session_ids: list[int]) -> set[int]:
    """Which of these conversations still have something going on.

    One query for the whole list: a switcher draws the mark next to every entry, and one
    query per row would be a dozen round trips for a dropdown.
    """
    if not session_ids:
        return set()
    rows = (await db.execute(select(AssistantTask.session_id).where(
        AssistantTask.session_id.in_(session_ids),
        AssistantTask.status.in_(RUNNING)))).scalars().all()
    return {r for r in rows if r is not None}


async def message_counts(db: AsyncSession, session_ids: list[int]) -> dict[int, int]:
    if not session_ids:
        return {}
    from sqlalchemy import func
    rows = (await db.execute(
        select(AssistantTask.session_id, func.count(AssistantTask.id))
        .where(AssistantTask.session_id.in_(session_ids))
        .group_by(AssistantTask.session_id))).all()
    return {sid: n for sid, n in rows if sid is not None}


async def listing(db: AsyncSession, owner_user_id: int | None, *, closed: bool = False,
                  agent: str = "") -> list[AssistantSession]:
    """The conversations of a person, newest activity first.

    Ordered by `last_message_at` and not by `created_at`: a conversation picked up again after
    three weeks belongs at the top, not back where it started. A conversation that never got a
    message falls back to when it came into being, otherwise a freshly created one would sort
    to the very bottom.
    """
    q = select(AssistantSession).where(AssistantSession.owner_user_id == owner_user_id)
    q = q.where(AssistantSession.closed_at.isnot(None) if closed
                else AssistantSession.closed_at.is_(None))
    if agent:
        q = q.where(AssistantSession.agent == agent)
    rows = (await db.execute(q)).scalars().all()
    return sorted(rows, key=recency, reverse=True)


def recency(s: AssistantSession) -> dt.datetime:
    """When something last happened in this conversation, always comparable.

    A value just assigned in this process carries a time zone, one read back from SQLite does
    not — and comparing the two raises. Postgres never hands back a naive value, so this is a
    seat belt for the tests and for anybody who runs this against another database.
    """
    when = s.last_message_at or s.created_at
    if when is None:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def out(s: AssistantSession, *, message_count: int = 0, running: bool = False) -> dict:
    return {"id": s.id, "agent": s.agent, "title": s.title,
            "created_at": s.created_at, "last_message_at": s.last_message_at,
            "closed_at": s.closed_at, "message_count": message_count, "running": running}


async def context_of(db: AsyncSession, session_ids: list[int]) -> dict[int, dict]:
    """How full the window was last time — per session, in a fixed number of queries.

    A client drawing the chat has no other way to ask. The numbers exist, but behind
    `/office/*` and the model endpoints, which the `assistant` scope deliberately does not
    reach. So they are answered where a chat client already looks: on the session.

    Two of the four numbers are not about the size but about the MECHANISM. The percentage
    alone hides that this context is compacted: it plateaus instead of filling up, and
    somebody watching a number that never reaches 100 deserves to see why. Hence
    `verbatim_exchanges` (what still travels word for word) and `summary_chars` (what has
    already been folded into the memory).

    Deliberately no cache. It is a handful of queries, and a cache would be a second truth
    about a number that changes with every run.
    """
    from sqlalchemy import and_, func

    from ..models.agents import Run
    from ..models.assistant import ChatSummary
    from ..models.ops import ProviderModel

    if not session_ids:
        return {}

    # The NEWEST finished run, not an average: the question is "how full was it last time",
    # and a mean would smooth away exactly the peak that matters. One window function instead
    # of a subquery per row — this list is polled by every open chat.
    ranked = (
        select(AssistantTask.session_id.label("sid"),
               Run.input_tokens.label("input_tokens"),
               Run.model.label("model"), Run.provider.label("provider"),
               func.coalesce(Run.finished_at, Run.started_at).label("measured_at"),
               func.row_number().over(partition_by=AssistantTask.session_id,
                                      order_by=Run.id.desc()).label("rank"))
        .join(Run, Run.id == AssistantTask.run_id)
        .where(AssistantTask.session_id.in_(session_ids), Run.status != "running")
    ).subquery()

    runs = (await db.execute(
        select(ranked.c.sid, ranked.c.input_tokens, ranked.c.model, ranked.c.measured_at,
               ProviderModel.context_tokens)
        .select_from(ranked)
        # An unknown model leaves the window empty instead of guessing one. A wrong
        # denominator is worse than no percentage, because it looks authoritative.
        .outerjoin(ProviderModel, and_(ProviderModel.provider == ranked.c.provider,
                                       ProviderModel.model == ranked.c.model))
        .where(ranked.c.rank == 1))).all()
    if not runs:
        return {}

    # What `_chat_history` would still take word for word: everything after the point the
    # summary reaches. The cut-off differs per session, so it comes out of the join and not
    # out of a second round.
    open_rows = (await db.execute(
        select(AssistantTask.session_id, func.count(AssistantTask.id))
        .outerjoin(ChatSummary, ChatSummary.session_id == AssistantTask.session_id)
        .where(AssistantTask.session_id.in_(session_ids),
               AssistantTask.kind == "chat", AssistantTask.status == "done",
               AssistantTask.id > func.coalesce(ChatSummary.to_task_id, 0))
        .group_by(AssistantTask.session_id))).all()
    open_ones = {sid: n for sid, n in open_rows}

    summaries = {sid: length for sid, length in (await db.execute(
        select(ChatSummary.session_id, func.length(ChatSummary.text))
        .where(ChatSummary.session_id.in_(session_ids)))).all() if sid is not None}

    # The same two numbers the history reckons with, so "verbatim" here means exactly what
    # travels there. Imported late: the worker module pulls the whole run machinery along,
    # and the API must not carry that for one constant.
    from ..worker.__main__ import CHAT_HISTORY_MAX, CHAT_SUMMARY_BLOCK

    out_map: dict[int, dict] = {}
    for sid, input_tokens, model, measured_at, window in runs:
        fresh = open_ones.get(sid, 0)
        verbatim = min(fresh, CHAT_HISTORY_MAX) if fresh > CHAT_HISTORY_MAX + CHAT_SUMMARY_BLOCK \
            else fresh
        out_map[sid] = {
            "input_tokens": int(input_tokens or 0),
            "model": model or "",
            "context_tokens": int(window) if window else None,
            "pct": round((input_tokens or 0) / window * 100) if window else None,
            "verbatim_exchanges": verbatim,
            "summary_chars": int(summaries.get(sid) or 0),
            "measured_at": measured_at,
        }
    return out_map


async def out_many(db: AsyncSession, rows: list[AssistantSession]) -> list[dict]:
    ids = [s.id for s in rows]
    counts = await message_counts(db, ids)
    busy = await running_ids(db, ids)
    context = await context_of(db, ids)
    return [{**out(s, message_count=counts.get(s.id, 0), running=s.id in busy),
             # None when the conversation has never run: there is nothing to report yet, and
             # a zero would read as "empty window" instead of "not measured".
             "context": context.get(s.id)}
            for s in rows]


async def delete(db: AsyncSession, sessions: list[AssistantSession], *,
                 commit: bool = True) -> list[int]:
    """Delete conversations for good, together with everything hanging off them.

    The guard rail lives here and not only in the caller: a session with a running task is
    never deleted, whatever the selector said. The rest goes down the foreign keys — the chat
    tasks by `assistant_tasks.session_id`, and their notifications and summaries after them.

    The notifications are cleared explicitly instead of being left to the cascade: SQLite
    enforces `ON DELETE CASCADE` only with `PRAGMA foreign_keys=ON`, and a bell that opens
    nothing is exactly the leftover this is about.
    """
    from sqlalchemy import delete as sql_delete

    from ..models.assistant import ChatSummary
    from ..models.notification import Notification

    ids = [s.id for s in sessions]
    busy = await running_ids(db, ids)
    gone: list[int] = []
    reports: list[dict] = []
    for s in sessions:
        if s.id in busy:
            log.info("Session %s is not deleted: something is still running in it", s.id)
            continue
        task_ids = (await db.execute(select(AssistantTask.id).where(
            AssistantTask.session_id == s.id))).scalars().all()
        if task_ids:
            await db.execute(sql_delete(Notification).where(
                Notification.assistant_task_id.in_(task_ids)))
            await db.execute(sql_delete(AssistantTask).where(AssistantTask.id.in_(task_ids)))
        await db.execute(sql_delete(ChatSummary).where(ChatSummary.session_id == s.id))
        await db.execute(sql_delete(AssistantChannelSession).where(
            AssistantChannelSession.session_id == s.id))
        reports.append(payload_of(s, message_count=len(task_ids)))
        await db.delete(s)
        gone.append(s.id)
    if commit:
        await db.commit()
    # Reported only afterwards: a flow listening for it starts and commits on its own, and
    # would otherwise see a half deleted conversation.
    for report in reports:
        await emit(db, "assistant.session_deleted", report)
    return gone
