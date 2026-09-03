"""What was fetched, kept per person until it is fetched again.

An ICS feed is read over the network and expanded into a year of occurrences.
Doing that per request would put a second or two of somebody else's server in
front of every view of a calendar, so it is done on a schedule and on demand,
and what came out is held here.

Per person, because the calendars are: they are that person's subscriptions,
with that person's credentials.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from .fetch import Snapshot, Source, refresh

log = logging.getLogger("notes.calendar")

# How long a fetched set stays good. The feeds change rarely, and the view has
# a button for the moment somebody knows better.
GOOD_FOR = dt.timedelta(minutes=15)

_snapshots: dict[int, Snapshot] = {}
_fetched: dict[int, dt.datetime] = {}
# One fetch at a time per person: opening three tabs at once must not become
# three rounds of network calls against the same five calendars.
_busy: dict[int, asyncio.Lock] = {}


def cached(user_id: int) -> Snapshot | None:
    return _snapshots.get(user_id)


def stale(user_id: int) -> bool:
    when = _fetched.get(user_id)
    return when is None or dt.datetime.now() - when > GOOD_FOR


async def ensure(user_id: int, sources: list[Source], *, force: bool = False) -> Snapshot:
    """The current set, fetched if it is old or if asked for."""
    lock = _busy.setdefault(user_id, asyncio.Lock())
    async with lock:
        if not force and not stale(user_id) and user_id in _snapshots:
            return _snapshots[user_id]
        if not sources:
            # No calendars is a valid state, and an empty answer is the right
            # one for it — not an error and not a stale set from before.
            snapshot = Snapshot(fetched_at=dt.datetime.now().astimezone().isoformat())
        else:
            snapshot = await refresh(sources)
        _snapshots[user_id] = snapshot
        _fetched[user_id] = dt.datetime.now()
        return snapshot


def forget(user_id: int | None = None) -> None:
    """Drop what is held. For tests, and when the calendars change."""
    if user_id is None:
        _snapshots.clear()
        _fetched.clear()
        _busy.clear()
        return
    _snapshots.pop(user_id, None)
    _fetched.pop(user_id, None)
