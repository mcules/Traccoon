"""Redis-Schnittstellenvertrag Backend ↔ Runner (Queue, Result, Events, Flags)."""
from __future__ import annotations

import asyncio
import json

from redis.asyncio import Redis

from ..config import settings

PREFIX = "traccoon:"
QUEUE = PREFIX + "task_queue"
# Reliable-Queue: Jobs liegen waehrend der Verarbeitung hier (blmove QUEUE→PROCESSING),
# damit ein abgestuerzter Worker sie beim naechsten Start aus PROCESSING zurueck in QUEUE
# holen kann, statt sie zu verlieren (siehe worker/__main__.py: pull_loop-Recovery + ACK).
PROCESSING = QUEUE + ":processing"

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def enqueue_task(payload: dict) -> None:
    await get_redis().lpush(QUEUE, json.dumps(payload))


async def wait_result(task_id: str, timeout: float = 1800.0, poll: float = 0.4) -> dict | None:
    """Pollt result:{task_id}; löscht den Key bei Fund. None bei Timeout."""
    r = get_redis()
    key = f"{PREFIX}result:{task_id}"
    waited = 0.0
    while waited < timeout:
        raw = await r.get(key)
        if raw is not None:
            await r.delete(key)
            return json.loads(raw)
        await asyncio.sleep(poll)
        waited += poll
    return None


async def publish_kill(issue_key: str) -> None:
    """Laufenden Agenten-Lauf abbrechen (Worker hört auf traccoon:kill)."""
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
