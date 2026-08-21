"""Redis-Schnittstellenvertrag Backend ↔ Runner (Queue, Result, Events, Flags)."""
from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from ..config import settings

log = logging.getLogger(__name__)

PREFIX = "traccoon:"
QUEUE = PREFIX + "task_queue"
# Reliable queue: jobs lie here during processing (blmove QUEUE to PROCESSING) so that a
# crashed worker can fetch them back from PROCESSING into QUEUE on its next start instead of
# losing them (see worker/__main__.py: pull_loop recovery plus ACK).
PROCESSING = QUEUE + ":processing"
# What the worker currently has in hand (hash issue_key to job info). The worker clears the
# hash at start; as the only sign of life it is therefore no good (a hard killed worker
# leaves its entries standing), and that is what the pulse with an expiry is for.
ACTIVE = PREFIX + "active_processes"
# Pulse per assignment: the worker refreshes `alive:<task_id>` during processing. If it goes
# away, the run is demonstrably dead, and exactly that separates "still working for a long
# time" from "has disappeared". The expiry is clearly above the refresh beat so that a GC
# hiccup in the worker does not raise a false alarm.
PULS_TAKT = 15
PULS_TTL = 90
# Results stay for a day. Formerly one hour, which was too short: a result fetched only after
# a backend outage was gone with it and the work lost.
RESULT_TTL = 86400
# This long a run may stay without ANY sign of life before it counts as disappeared.
GNADENFRIST = 300
# Beat of the liveness check (the result poll runs faster but costs only one query). As a
# module constant so that tests can pull it to 0.
PRUEF_TAKT = 5.0

_redis: Redis | None = None


def get_redis() -> Redis:
    """The shared Redis client, with the same safeguards as in the worker.

    Without `socket_keepalive`/`health_check_interval`/`socket_timeout` the client waits
    endlessly for an answer on a half dead connection: no error, no timeout, no log. The
    worker has always known that (`_REDIS_KW`), the backend did not, and the watchers waiting
    for the result of an agent run hang there.

    On 2026-08-07 that cost an hour of standstill: the result for ABC-31 lay finished in
    Redis at 19:54, the watcher hung in a `get` that never came back, and nobody fetched it.
    From the outside it looked as if the agent were still working; it had long finished.
    """
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_keepalive=True, health_check_interval=30,
            # Hard upper bound per command: better an error the caller sees than a wait
            # nobody notices. The watchers poll every second, so 30 s are generous for a
            # `get`.
            socket_timeout=30, socket_connect_timeout=10, retry_on_timeout=True)
    return _redis


async def enqueue_task(payload: dict) -> None:
    await get_redis().lpush(QUEUE, json.dumps(payload))


def puls_key(task_id: str) -> str:
    """Key of the sign of life. The worker writes it (with its own connection) and the backend
    reads it, so the name belongs here, not in either of the two."""
    return f"{PREFIX}alive:{task_id}"


async def lauf_lebt(task_id: str) -> bool:
    """Does this assignment still exist, no matter how long it has been running?

    Three sources, each sufficient on its own:
    * pulse of the worker (it is processing the assignment right now),
    * queue (not picked up yet),
    * processing list (worker crashed; the recovery puts it back into the queue on the next
      start, so the assignment is not lost, only delayed).

    The hash of active processes counts in addition but only helps with a worker that is
    still running; a killed worker leaves stale entries there (hence the pulse).
    """
    r = get_redis()
    if await r.get(puls_key(task_id)) is not None:
        return True
    for listing in (QUEUE, PROCESSING):
        for raw in await r.lrange(listing, 0, -1):
            if task_id in raw:
                return True
    for raw in (await r.hvals(ACTIVE)) or []:
        if task_id in raw:
            return True
    return False


async def wait_result(task_id: str, timeout: float | None = None, poll: float = 0.4,
                      gnadenfrist: float = GNADENFRIST) -> dict | None:
    """Waits for result:{task_id}, as long as the run lives, not by the clock.

    An agent run may take hours (implementation plus review rounds hang off ONE assignment).
    A fixed wall clock limit broke exactly that: the watcher gave up after 30 minutes, the
    ticket stood on "failed" while the agent kept working and committed its work cleanly.
    That is why a SIGN OF LIFE is checked here instead of the elapsed time: giving up happens
    only when the assignment turns up nowhere for `gnadenfrist` seconds (worker gone,
    assignment not in the queue).

    `timeout` is an optional hard cap (None/0 = none) for nodes that deliberately should not
    wait forever. None in both cases: disappeared run or cap reached; which case it was is
    told apart by the caller over the elapsed time.
    """
    r = get_redis()
    key = f"{PREFIX}result:{task_id}"
    uhr = asyncio.get_running_loop().time
    start = uhr()
    tot_seit: float | None = None
    naechste_check = 0.0
    last_notice = start
    while True:
        try:
            raw = await r.get(key)
            if raw is not None:
                await r.delete(key)
                return json.loads(raw)
        except Exception:  # noqa: BLE001 - a disturbance must not kill the watcher
            # An outage (timeout, connection loss) is no reason to give up waiting: the
            # result comes later anyway, and a dead watcher would leave the ticket standing
            # forever. Try again on the next round.
            log.warning("Fetching the result for %s failed, trying again", task_id,
                        exc_info=True)
        now = uhr()
        if timeout and now - start >= timeout:
            return None
        # Check the sign of life only every few seconds: the result poll runs fast, while the
        # check costs several Redis queries.
        if now >= naechste_check:
            naechste_check = now + PRUEF_TAKT
            if await lauf_lebt(task_id):
                tot_seit = None
            elif tot_seit is None:
                tot_seit = now
            elif now - tot_seit >= gnadenfrist:
                log.warning("Run %s without a sign of life for %.0fs, counts as disappeared",
                            task_id, now - tot_seit)
                return None
        if now - last_notice >= 1800:
            last_notice = now
            log.info("still waiting for %s (%.0f min, the run is alive)", task_id, (now - start) / 60)
        await asyncio.sleep(poll)


async def peek_result(task_id: str) -> bool:
    """True when a result for task_id lies in Redis (without consuming it).
    For the reattach after a backend restart: detects runs that are already finished."""
    return await get_redis().get(f"{PREFIX}result:{task_id}") is not None


async def publish_kill(issue_key: str) -> None:
    """Abort a running agent run (the worker listens on traccoon:kill)."""
    await get_redis().publish(PREFIX + "kill", issue_key)


async def publish_event(project_id: int, event: dict) -> None:
    await get_redis().publish(f"{PREFIX}events:{project_id}", json.dumps(event))


async def runner_connected() -> bool:
    hb = await get_redis().get(f"{PREFIX}runner:heartbeat")
    if hb is None:
        return False
    try:
        import time
        return (time.time() * 1000 - float(hb)) < 15000
    except (ValueError, TypeError):
        return False


async def get_flag(name: str, default: bool = False) -> bool:
    v = await get_redis().get(PREFIX + name)
    if v is None:
        return default
    return v == "1"


async def set_flag(name: str, value: bool) -> None:
    if value:
        await get_redis().set(PREFIX + name, "1")
    else:
        await get_redis().delete(PREFIX + name)


def _user_key(base: str, user_id: int | None) -> str:
    return f"{base}:{user_id or 1}"


async def get_user_flag(base: str, user_id: int | None, default: bool = False) -> bool:
    return await get_flag(_user_key(base, user_id), default)


async def set_user_flag(base: str, user_id: int | None, value: bool) -> None:
    await set_flag(_user_key(base, user_id), value)
