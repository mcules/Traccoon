"""Reading the vault into the page index, and keeping it read.

The index above knows how to hold pages; this is what fills it from a folder of
files and what a single changed file goes through afterwards.

One thing here is deliberately different from the service being replaced, and it
shows up in every answer that has no `SORT`: the files are walked in sorted
order, not in the order the file system happens to hand them over. There, the
order of a table without a sort clause depends on where a note landed on the
disk, and it changes when a neighbouring file is created or deleted. Here it is
the same twice, which is both easier to trust and the only way two answers can
be compared at all.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import paths
from ..vault.files import Vault
from .pages import PageIndex, parse_page

log = logging.getLogger("notes.dv")

NOTE_SUFFIXES = (".md", ".markdown")


def is_note(rel: str) -> bool:
    return rel.lower().endswith(NOTE_SUFFIXES)


def stat_of(root: Path, rel: str) -> dict:
    """Size and times, in the units the value model uses.

    Whole milliseconds, and cut rather than rounded. A moment carries no
    fraction of a millisecond on the other side — the number goes through a
    date, which drops it — so a file changed at 1786440339442.8 is a file
    changed at 1786440339442. Keeping the fraction would make every single
    `file.mtime` in every answer differ by a number nobody can see.

    A file that cannot be read still gets an entry: leaving it out would make it
    look like a note nobody wrote, which is a different statement from one whose
    times are unknown.
    """
    try:
        s = (root / rel).stat()
        return {"size": s.st_size,
                "ctimeMs": s.st_ctime_ns // 1_000_000,
                "mtimeMs": s.st_mtime_ns // 1_000_000}
    except OSError:
        import time
        now = time.time_ns() // 1_000_000
        return {"size": 0, "ctimeMs": now, "mtimeMs": now}


def build(vault: Vault) -> PageIndex:
    root = Path(vault.root)
    entries: list[tuple[str, str, dict]] = []
    for p in paths.walk(root):
        rel = paths.relative(root, p)
        if not is_note(rel):
            continue
        try:
            entries.append((rel, vault.read_text(rel), stat_of(root, rel)))
        except (OSError, paths.OutsideVault):
            continue
    index = PageIndex()
    index.build(entries)
    log.info("notes: %d notes in the page index of %s", len(index.pages), root)
    return index


def update(vault: Vault, index: PageIndex, rel: str, *, removed: bool = False) -> None:
    """One file changed. Everything else stays as it is."""
    if paths.hidden(rel) or not is_note(rel):
        return
    if removed:
        index.remove(rel)
        return
    root = Path(vault.root)
    try:
        raw = vault.read_text(rel)
    except (OSError, paths.OutsideVault):
        index.remove(rel)
        return
    try:
        index.put(parse_page(rel, raw, stat_of(root, rel)))
    except Exception:                             # noqa: BLE001
        # A note this cannot parse is dropped from the index rather than left in
        # it half-read: half a page answers queries wrongly and says nothing.
        log.exception("notes: could not read %s into the page index", rel)
        index.remove(rel)
