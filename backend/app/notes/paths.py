"""Where the notes are, and the one gate every path goes through.

The vault is a folder of files, and every request names a path inside it. That
makes this the outer wall: a path that escapes it reads or writes something that
was never anybody's note. So there is exactly one function that turns a name from
outside into a place on the disk, and everything else in the package takes what
it hands back.

Three ways out of a folder, and all three are closed here:

  * `..`, on its own or buried in the middle of a longer path;
  * an absolute path, which quietly replaces everything in front of it when it is
    joined;
  * a symbolic link inside the vault that points outside it. This is the one that
    is easy to forget, because the path itself looks harmless right up until the
    file system follows it.

The check is done on the resolved path, so all three end up as the same question:
is this still under the root.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

# Folders whose content is not notes. Everything starting with a dot is skipped
# anyway (see `hidden`), so what is listed here are the ones that do not: a
# dependency folder that somebody's tooling left in the vault. A tree that shows
# these is noise, and an index that reads them finds thousands of files nobody
# wrote.
SKIP_DIRS = {"node_modules"}


class OutsideVault(Exception):
    """A path that does not stay inside the vault. Never shown as it is: the
    caller turns it into an error with a key, so a person reads a sentence."""

    def __init__(self, given: str) -> None:
        super().__init__(f"path leaves the vault: {given!r}")
        self.given = given


def _clean(rel: str) -> PurePosixPath:
    """The path as the API speaks it: relative, forward slashes, no tricks.

    Backslashes come in from clients that were written on Windows and mean a
    separator there; taking them as part of a file name would make one path with
    a strange name out of two segments, and the segment check below would then
    look at the wrong thing.
    """
    text = (rel or "").replace("\\", "/").strip()
    while text.startswith("/"):
        text = text[1:]
    return PurePosixPath(text)


def resolve(root: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Turn a vault-relative path into a place on the disk, or refuse.

    `must_exist` is for reading: it also resolves symbolic links, which is what
    catches a link pointing out of the vault. For writing the file is not there
    yet, so the parent is resolved instead and the name is joined onto it.
    """
    parts = _clean(rel)
    if parts.is_absolute() or any(p == ".." for p in parts.parts):
        raise OutsideVault(rel)

    base = root.resolve()
    target = (base / parts).resolve() if must_exist else _resolve_parent(base, parts)
    if target != base and base not in target.parents:
        raise OutsideVault(rel)
    return target


def _resolve_parent(base: Path, parts: PurePosixPath) -> Path:
    """For a file that may not exist yet: resolve as far as there is a path.

    `Path.resolve()` on a missing file walks up on its own, but it also swallows
    a link in the middle of the way. Resolving the deepest existing parent and
    joining the rest keeps that link visible.
    """
    here = base
    rest = list(parts.parts)
    while rest and (here / rest[0]).exists():
        here = (here / rest.pop(0)).resolve()
    return here.joinpath(*rest) if rest else here


def relative(root: Path, abs_path: Path) -> str:
    """The other direction: what the API calls this file."""
    return abs_path.resolve().relative_to(root.resolve()).as_posix()


def hidden(rel: str) -> bool:
    """Is this below a folder that is not notes?

    Any segment starting with a dot counts, not only the ones named above: a
    vault carries the hidden folders of whatever else has touched it, and none
    of them are notes.
    """
    parts = _clean(rel).parts
    return any(p.startswith(".") or p in SKIP_DIRS for p in parts)


def walk(root: Path) -> list[Path]:
    """Every file that is a note or an attachment, in a stable order.

    Sorted, because a tree that changes its order between two calls makes a diff
    of two answers unreadable, and comparing answers is how the port is measured.
    """
    out: list[Path] = []
    base = root.resolve()
    for here, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.startswith("."):
                out.append(Path(here) / name)
    return out
