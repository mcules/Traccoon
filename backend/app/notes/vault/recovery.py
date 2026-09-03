"""Snapshots of a note, taken as it is written.

Three things can save somebody from an edit they did not mean, and they cover
different accidents. The version history covers "what did this look like an hour
ago". The trash covers "I deleted it". Neither covers the common one: half a
note replaced twenty minutes ago and saved over ever since.

Kept outside the vault, never in it. Snapshots inside the vault would be carried
to every device by the file sync and would turn up in every search, and the
thing that is supposed to be a safety net would become noise.

Off unless a folder is configured. That is deliberate: writing somebody's notes
to a second place is not a thing to start doing by default.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("notes.recovery")

# One snapshot a minute at most — the same interval the program this replaces
# used, so a note edited continuously does not fill the folder.
MIN_GAP_MS = 60_000
KEEP_PER_FILE = 25
KEEP_DAYS = 14

WORTH_KEEPING = re.compile(r"\.(md|markdown|txt|canvas|base)$", re.I)


def _key(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]


def _dir(root: Path, rel: str) -> Path:
    return root / _key(rel)


def _stamps(folder: Path) -> list[int]:
    try:
        return sorted(int(p.stem) for p in folder.glob("*.snap") if p.stem.isdigit())
    except OSError:
        return []


def snapshot(root: Path | None, rel: str, content: str) -> bool:
    """Keep the text that is about to be replaced. Says whether it kept one.

    Never raises. A snapshot that cannot be written must not stop the save it
    was taken for — the note is the point, the copy is the net.
    """
    if root is None or not WORTH_KEEPING.search(rel):
        return False
    try:
        folder = _dir(root, rel)
        folder.mkdir(parents=True, exist_ok=True)
        stamps = _stamps(folder)
        if stamps:
            newest = stamps[-1]
            if time.time() * 1000 - newest < MIN_GAP_MS:
                return False
            with (folder / f"{newest}.snap").open(encoding="utf-8", newline="") as fh:
                previous = fh.read()
            if previous == content:
                return False                       # nothing new to keep
        now = int(time.time() * 1000)
        with (folder / f"{now}.snap").open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        (folder / "path.txt").write_text(rel, encoding="utf-8")
        _prune(folder)
        return True
    except OSError:
        log.warning("notes: could not keep a snapshot of %s", rel)
        return False


def _prune(folder: Path) -> None:
    """Keep the most recent few and nothing older than a fortnight — but always
    the newest, whatever the rules say."""
    stamps = _stamps(folder)
    if len(stamps) <= 1:
        return
    cutoff = time.time() * 1000 - KEEP_DAYS * 86_400_000
    keep_from = max(0, len(stamps) - KEEP_PER_FILE)
    for i, stamp in enumerate(stamps[:-1]):
        if i < keep_from or stamp < cutoff:
            (folder / f"{stamp}.snap").unlink(missing_ok=True)


def snapshots(root: Path | None, rel: str) -> list[dict]:
    """What is kept of this note, newest first."""
    if root is None:
        return []
    folder = _dir(root, rel)
    out: list[dict] = []
    for stamp in _stamps(folder):
        try:
            out.append({"ts": stamp, "size": (folder / f"{stamp}.snap").stat().st_size})
        except OSError:
            continue
    out.sort(key=lambda s: -s["ts"])
    return out


def read(root: Path | None, rel: str, ts: int) -> str | None:
    if root is None:
        return None
    try:
        with (_dir(root, rel) / f"{ts}.snap").open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError:
        return None
