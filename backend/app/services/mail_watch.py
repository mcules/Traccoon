"""Mailboxes that report by themselves — IMAP IDLE (RFC 2177).

Before, the UI asked every minute. That is fine for a counter and annoying for mail: a
message that arrived 55 seconds ago is not there yet, and whoever sits next to it hits
refresh. IDLE turns the direction around — the connection stays open, and
der Server sagt Bescheid.

How it runs here:

* One watcher per active mailbox, in a thread of its own, because `imapclient` blocks.
* It reports only THAT something has happened. What exactly, the browser fetches afterwards
  through the normal ways — otherwise we would have two sources for the same state.
* Nobody there, no watcher: as long as no window of this person is open, nobody keeps a
  connection open. A mailbox is not a subscription that draws power in the background.
* A connection that dies is rebuilt — with a growing pause, so that a server that does not
  feel like it right now is not asked once a second.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select

from ..db import SessionLocal
from ..models.mail import MailAccount

log = logging.getLogger("mail_watch")

AN = os.getenv("MAIL_IDLE", "1") not in ("0", "false", "no")
# After 29 minutes at the latest IDLE has to be renewed (RFC 2177 names 29 as the safe limit;
# viele Server werfen früher raus).
RENEW = 20 * 60
# How many mailboxes are watched at once. Every one is an open connection.
MAX_WATCHDOG = int(os.getenv("MAIL_IDLE_MAX", "20"))

_running: dict[int, asyncio.Task] = {}
_overseer: asyncio.Task | None = None


def _idle_round(account: MailAccount, duration: int) -> list:
    """One round of IDLE, blocking — which is why it belongs in a thread."""
    # Deliberately a connection of ITS OWN, not one from the pool: this one stands in IDLE for
    # twenty minutes. Putting it back would mean handing the next call a line that waits for
    # an announcement instead of for its question.
    from ..services.mailbox import _join

    client = _join(account)
    try:
        client.select_folder("INBOX", readonly=True)
        client.idle()
        try:
            return client.idle_check(timeout=duration) or []
        finally:
            try:
                client.idle_done()
            except Exception:  # noqa: BLE001 — the connection is being closed anyway
                pass
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def _watchdog(account_id: int, user_id: int) -> None:
    from ..api.ws import persons

    pause = 5
    already_warmed = False
    while True:
        try:
            async with SessionLocal() as db:
                account = await db.get(MailAccount, account_id)
                if account is None or not account.enabled:
                    return
                # Keep using it detached from the session: the thread must not hang on a
                # Datenbankverbindung hängen, während er minutenlang wartet.
                db.expunge(account)

            if not persons.somebody_there(user_id):
                await asyncio.sleep(20)
                continue

            if not already_warmed:
                # Once while watching: whoever is logged in has the watcher running before
                # they even open the mailbox. Then the first look is warm too.
                from .mailbox_cache import prewarm
                asyncio.create_task(prewarm(account))
                already_warmed = True

            events = await asyncio.to_thread(_idle_round, account, RENEW)
            pause = 5
            # EXISTS (neue Nachricht), EXPUNGE (weg), FETCH (Flag geändert) — welches davon,
            # does not matter to the UI: it fetches the state fresh anyway.
            if events:
                # Forget first, then report: otherwise the UI asks in the same moment and gets
                # the state from before the new mail.
                from .mailbox_cache import invalidate, prewarm
                await invalidate(account_id)
                # And fetch again right away: the report reaches the person while the state is
                # already on its way — otherwise they wait for the second we
                # gerade erst weggeworfen haben.
                asyncio.create_task(prewarm(account))
                await persons.send(user_id, {
                    "type": "mail",
                    "data": {"account_id": account_id, "folder": "INBOX"},
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Wächter für Konto %s: %s — neuer Versuch in %ss",
                     account_id, str(exc)[:120], pause)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 300)


async def _overseer_loop() -> None:
    """Starts and ends watchers when mailboxes appear or disappear."""
    while True:
        try:
            async with SessionLocal() as db:
                accounts = (await db.execute(select(MailAccount).where(
                    MailAccount.enabled.is_(True),
                    MailAccount.imap_host != ""))).scalars().all()
                wanted = {k.id: k.owner_user_id for k in accounts[:MAX_WATCHDOG]}

            for kid, uid in wanted.items():
                task = _running.get(kid)
                if task is None or task.done():
                    _running[kid] = asyncio.create_task(_watchdog(kid, uid))
            for kid in list(_running):
                if kid not in wanted:
                    _running.pop(kid).cancel()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Aufseher der Postfach-Wächter gestolpert")
        await asyncio.sleep(60)


async def start() -> None:
    global _overseer
    if not AN or _overseer is not None:
        return
    _overseer = asyncio.create_task(_overseer_loop())
    log.info("Postfach-Wächter (IMAP IDLE) gestartet")


async def stop() -> None:
    global _overseer
    if _overseer is not None:
        _overseer.cancel()
        _overseer = None
    for task in _running.values():
        task.cancel()
    _running.clear()
