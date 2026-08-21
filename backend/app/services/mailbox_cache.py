"""What the mailbox already knows it need not ask again.

Building the page cost a measured 1.9 seconds, and almost all of that were questions to the
mailbox whose answer does not change between two clicks: the folder list (33 folders) and the
unread counts (one STATUS per folder, 900 ms on their own).

Zwei Dinge halten den Cache ehrlich:

* A **short shelf life**, because a mail can be read on the phone too, and Traccoon learns of
  that only on the next look.
* A **generation counter** per account. Whoever changes something (read, move, delete, send)
  or receives an announcement from the mailbox (IDLE) bumps it — and all entries of the old
  generation are thereby unreachable without anyone having to search for keys.

If Redis fails that is no error: then everything is asked every time, as before.
"""
from __future__ import annotations

import json
import logging

from ..core.redis import get_redis

log = logging.getLogger("traccoon.mailbox")

PREFIX = "traccoon:mailbox"
# Folders change rarely, lists constantly. Both survive a click, neither a pause.
TTL_FOLDER = 120
TTL_LISTING = 45
TTL_UNREAD = 60


async def _generation(account_id: int) -> int:
    try:
        value = await get_redis().get(f"{PREFIX}:{account_id}:gen")
        return int(value) if value else 0
    except Exception:  # noqa: BLE001 — without Redis everything runs as before, only slower
        return -1


async def invalidate(account_id: int) -> None:
    """Forget everything about this account. One INCR, no searching for keys."""
    try:
        await get_redis().incr(f"{PREFIX}:{account_id}:gen")
    except Exception:  # noqa: BLE001
        log.debug("Cache für Konto %s nicht entwertet", account_id)


async def fetch_part(account_id: int, part: str):
    gen = await _generation(account_id)
    if gen < 0:
        return None
    try:
        raw = await get_redis().get(f"{PREFIX}:{account_id}:{gen}:{part}")
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


async def put(account_id: int, part: str, value, ttl: int) -> None:
    gen = await _generation(account_id)
    if gen < 0:
        return
    try:
        await get_redis().set(f"{PREFIX}:{account_id}:{gen}:{part}",
                              json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001
        log.debug("Cache für Konto %s nicht geschrieben (%s)", account_id, part)


async def cached(account_id: int, part: str, ttl: int, fetch):
    """From the cache, otherwise fetch and put down.

    `fetch` is the expensive question to the mailbox; it runs only when the cache has nothing.
    """
    existing = await fetch_part(account_id, part)
    if existing is not None:
        return existing
    fresh = await fetch()
    await put(account_id, part, fresh, ttl)
    return fresh


async def prewarm(account) -> None:
    """Den Stand holen, bevor jemand danach fragt.

    Without this the cache would be cold exactly when it is needed: a new mail invalidates it,
    and the next look into the mailbox pays the full second again. Whoever is watching has a
    watcher running anyway — it knows first and can ask the questions right away.

    Errors stay here: prewarming is a convenience, not an assignment. What does not work is
    simply fetched normally on the next call.
    """
    import asyncio

    from . import mailbox

    async def one(part: str, ttl: int, fetch):
        try:
            await put(account.id, part, await fetch(), ttl)
        except Exception:  # noqa: BLE001
            log.debug("Vorwärmen (%s) für Konto %s ging nicht", part, account.id)

    await asyncio.gather(
        one("folders:1", TTL_FOLDER, lambda: mailbox.folder(account, True)),
        one("list:INBOX:0:50", TTL_LISTING, lambda: mailbox.listing(account, "INBOX", "", 0, 50)),
        one("unread", TTL_UNREAD, lambda: mailbox.unread(account)),
    )
