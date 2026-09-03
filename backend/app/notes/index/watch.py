"""Keep the index in step with the disk.

A vault has several writers. The person typing in the browser is one; the file
sync is another, and it delivers what was written on a phone or a laptop; an
agent writes into it, and so do a handful of scheduled jobs. Whatever holds an
index of that has to be told, or it slowly stops describing the vault.

That is not a theory. The service being replaced builds its index at startup and
keeps it up by hand afterwards, and after one day of writing 52 of its tag counts
had drifted away from what was actually in the notes. Nothing was broken and
nothing said so: queries simply answered with slightly the wrong set.

So this watches, and it does two things that a naive watcher does not:

  * **It waits for quiet.** The file sync writes a hundred files in a burst, and
    a rebuild per file would spend the whole burst rebuilding. Events are
    collected and worked off once the disk has been still for a moment.
  * **It updates rather than rebuilds.** Six thousand notes must not be re-read
    because one of them was saved. Only a rename touches two entries, and it
    arrives as one gone and one new.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from watchfiles import Change, awatch

from .. import paths
from ..vault.files import Vault

log = logging.getLogger("notes.watch")

# How long the disk has to be still before the collected events are worked off.
# Long enough that a burst from the file sync is one round, short enough that a
# note saved in the browser is in the index before somebody searches for it.
QUIET_MS = 250


async def watch(vault: Vault, change: Callable[[str, bool], None], *,
                stop: asyncio.Event | None = None) -> None:
    """Follow the vault until told to stop. Never raises upwards.

    A watcher that dies takes the freshness of the index with it and says
    nothing, which is the failure that is hardest to notice, so everything is
    caught and logged and the loop goes on.
    """
    root = Path(vault.root)
    log.info("notes: watching %s", root)
    try:
        async for batch in awatch(root, stop_event=stop, debounce=QUIET_MS,
                                  recursive=True, ignore_permission_denied=True):
            try:
                apply(vault, batch, change)
            except Exception:                      # noqa: BLE001 - see docstring
                log.exception("notes: could not work off a batch of changes")
    except asyncio.CancelledError:
        raise
    except Exception:                              # noqa: BLE001
        log.exception("notes: the watcher stopped")


def apply(vault: Vault, batch: set[tuple[Change, str]],
          change: Callable[[str, bool], None]) -> int:
    """Work off one batch. Returns how many files were actually touched.

    What "taking a file into the index" means is not decided here. There are
    several indexes over the same vault, and two of them refreshed at different
    moments answer differently about the same note without anything saying so —
    so this hands each file to exactly one place and that place updates all of
    them together.
    """
    touched = 0
    for _kind, full in batch:
        try:
            rel = paths.relative(Path(vault.root), Path(full))
        except (ValueError, OSError):
            continue                                # not in this vault after all
        if paths.hidden(rel):
            continue
        # `deleted` is not trusted on its own: a save that writes to a temporary
        # file and renames over the target arrives as delete plus add, and on a
        # busy disk the two can be in the same batch in either order. What is on
        # the disk when the batch is worked off is the truth.
        gone = not (Path(vault.root) / rel).exists()
        change(rel, gone)
        touched += 1
    if touched:
        log.debug("notes: %d file(s) taken into the index", touched)
    return touched
