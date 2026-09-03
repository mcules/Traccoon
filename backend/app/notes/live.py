"""Telling the open windows that a note changed.

A vault has several writers. Somebody types in one browser tab and the note is
open in a second; the file sync delivers what was written on a phone; an agent
writes into it. Without a word from the server, the other window keeps showing
what it read minutes ago and, on the next save, writes that back over the newer
text. The channel is what stops that, and it is why the editor can merge instead
of clobbering.

One channel per vault, not per person: the vault is the thing that changes, and
everybody looking at it wants to hear about it.

Deliberately one-way. What comes back over the socket is never read — receiving
happens only so a disconnect is noticed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("notes.live")

# vault root -> the sockets watching it
_watching: dict[str, set] = {}


def join(key: str, socket: Any) -> None:
    _watching.setdefault(key, set()).add(socket)


def leave(key: str, socket: Any) -> None:
    listeners = _watching.get(key)
    if not listeners:
        return
    listeners.discard(socket)
    if not listeners:
        del _watching[key]


def listeners(key: str) -> int:
    return len(_watching.get(key, ()))


def announce(key: str, message: dict) -> None:
    """Tell everyone watching this vault. Never raises, never waits.

    Called from the watcher, which runs while a file is being taken into the
    index — a slow or dead socket there would hold up the index itself, so the
    sending is handed to the loop and the result is not waited for.
    """
    if not _watching.get(key):
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return                      # no loop: a test, or a script
    asyncio.create_task(_send(key, message))


async def _send(key: str, message: dict) -> None:
    for socket in list(_watching.get(key, ())):
        try:
            await socket.send_json(message)
        except Exception:           # noqa: BLE001
            # A socket that cannot be written to is gone, whatever it says about
            # itself. Dropping it here is what keeps a dead tab from being tried
            # again on every single change.
            leave(key, socket)
