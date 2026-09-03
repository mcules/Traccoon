"""Older versions of a note.

The versions do not live in the vault. They live in a repository beside it,
which the backup writes to once an hour, and this side only ever reads: no work
tree, no remote, no writing of any kind. That is a deliberate shape, not a
limitation — a repository inside the vault is carried between machines by the
synchronisation, and a vault of six thousand notes with an hour's worth of
versions duplicated onto every phone is exactly what that arrangement avoids.

It was not hypothetical. The side this replaces has six routes that write —
init, clone, pull, commit, push, sync — and although the setting that drives
them was off, one of them ran once: a 246 MB repository appeared inside the
vault, 222 MB of it second copies of every picture and video, and the
synchronisation carried it to five devices before anybody noticed. Those six
routes are not ported. What is ported is the three that read.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger("notes.history")

# A commit is addressed by its hash and nothing else. The check is here rather
# than at the route because this is the place that hands the value to a program.
HEX = "0123456789abcdef"
TIMEOUT = 20.0
DEFAULT_LIMIT = 50
# Fields of one commit, in the order they are asked for, separated by a byte
# that cannot occur in any of them.
SEPARATOR = "\x1f"
FORMAT = SEPARATOR.join(["%H", "%aI", "%an", "%s"])


@dataclass
class Commit:
    hash: str
    date: str
    author: str
    message: str

    def as_json(self) -> dict:
        return {"hash": self.hash, "date": self.date,
                "author": self.author, "message": self.message}


class NoHistory(Exception):
    """No repository is configured, or the one configured is not there."""


def is_hash(value: str) -> bool:
    return bool(value) and len(value) <= 40 and all(c in HEX for c in value.lower())


@dataclass
class History:
    """One vault's versions, read out of a bare repository."""

    git_dir: Path

    @property
    def available(self) -> bool:
        # A bare repository has these two; a path that merely exists does not.
        return (self.git_dir / "HEAD").exists() and (self.git_dir / "objects").is_dir()

    def _run(self, *args: str) -> str:
        """One read against the repository.

        `--git-dir` without a work tree is what keeps this incapable of changing
        anything: every command that would write needs one, and there is none.
        """
        if not self.available:
            raise NoHistory("no version history")
        try:
            done = subprocess.run(
                ["git", "--git-dir", str(self.git_dir), *args],
                capture_output=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise NoHistory(str(err)) from None
        if done.returncode != 0:
            raise NoHistory(done.stderr.decode("utf-8", "replace").strip() or "git failed")
        return done.stdout.decode("utf-8", "replace")

    def info(self) -> dict:
        """Whether there is a history, and how fresh it is."""
        if not self.available:
            return {"has": False, "last": None}
        try:
            newest = self._run("log", "-1", f"--format={FORMAT}")
        except NoHistory:
            # The repository is there but says nothing — an empty one, or one
            # still being written. That is "no versions yet", not an error.
            return {"has": True, "last": None}
        parts = newest.rstrip("\n").split(SEPARATOR)
        return {"has": True, "last": parts[1] if len(parts) > 1 else None}

    def log(self, rel: str, limit: int = DEFAULT_LIMIT) -> list[Commit]:
        """The commits that touched one note, newest first.

        A note with no history yet gives an empty list rather than an error:
        every note written since the last backup run is in that state, and it is
        not a failure to look at one.
        """
        rel = paths.checked(rel)
        try:
            raw = self._run("log", f"--max-count={max(1, min(limit, 200))}",
                            f"--format={FORMAT}", "--", rel)
        except NoHistory:
            return []
        out = []
        for line in raw.split("\n"):
            if not line.strip():
                continue
            parts = line.split(SEPARATOR)
            if len(parts) == 4:
                out.append(Commit(*parts))
        return out

    def show(self, commit: str, rel: str) -> str:
        """The text of one note as it stood in one commit."""
        if not is_hash(commit):
            raise NoHistory("not a commit")
        rel = paths.checked(rel)
        return self._run("show", f"{commit}:{rel}")


def open_history(git_dir: str) -> History:
    if not git_dir:
        raise NoHistory("no version history")
    return History(git_dir=Path(git_dir))
