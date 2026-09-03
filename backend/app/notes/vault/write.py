"""Writing into the vault.

The other half of layer one. It knows the disk and nothing else, and everything
it does goes through the one gate in `paths.resolve`, so a path that leaves the
vault never becomes a file.

Two things here are not ordinary file handling and both have a reason that costs
something when it is forgotten:

  * **A write goes through a scratch file next to the target and is then renamed
    over it.** A rename inside one directory is atomic, so a crash in the middle
    leaves either the old note or the new one, never half of one. It has to be
    the *same* directory — across a filesystem a rename is a copy and the
    atomicity is gone — which is why the scratch file lives in the vault at all.
    It is hidden and carries a fixed infix, and the file sync on this machine
    ignores exactly that pattern. **Changing the infix means changing that
    ignore list in the same step**, or every peer picks up half-written files.
  * **Deleting moves rather than deletes.** Into a folder in the vault that
    starts with a dot, so nothing indexes it and nothing searches it, and the
    layout under it is kept so a note can go back where it came from.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path, PurePosixPath

from .. import paths
from ..settings.options import Options
from .files import Vault

NOTE_SUFFIX_RE = re.compile(r"\.(md|markdown)$", re.I)


class NotInTrash(Exception):
    """A path that is not in the trash, where one was expected."""


def _temp_sibling(abs_path: Path) -> Path:
    """The scratch file a write goes through.

    Hidden and with the infix the file sync's ignore list matches. See the note
    at the top: this name is not free.
    """
    return abs_path.parent / f".{abs_path.name}.wo-tmp-{int(time.time() * 1000)}"


def write_text(vault: Vault, rel: str, text: str) -> None:
    """Put text into a note. Either all of it or none of it.

    Newlines are written through untouched. A note that came from a machine that
    ends its lines with two characters keeps them, and rewriting them would make
    every device see the whole file as changed for a change nobody made.
    """
    abs_path = paths.resolve(Path(vault.root), rel)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(abs_path)
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, abs_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_bytes(vault: Vault, rel: str, data: bytes) -> None:
    abs_path = paths.resolve(Path(vault.root), rel)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_sibling(abs_path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, abs_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def create_folder(vault: Vault, rel: str) -> None:
    paths.resolve(Path(vault.root), rel).mkdir(parents=True, exist_ok=True)


def exists(vault: Vault, rel: str) -> bool:
    try:
        return paths.resolve(Path(vault.root), rel).exists()
    except paths.OutsideVault:
        return False


def rename(vault: Vault, source: str, target: str) -> None:
    root = Path(vault.root)
    abs_from = paths.resolve(root, source, must_exist=True)
    abs_to = paths.resolve(root, target)
    abs_to.parent.mkdir(parents=True, exist_ok=True)
    os.rename(abs_from, abs_to)


def copy(vault: Vault, source: str, target: str) -> list[str]:
    """Copy a file or a folder. Says which files it created, so they can be read
    into the indexes. Refuses to write over something that is already there."""
    root = Path(vault.root)
    abs_from = paths.resolve(root, source, must_exist=True)
    abs_to = paths.resolve(root, target)
    if abs_to.exists():
        raise FileExistsError(target)
    abs_to.parent.mkdir(parents=True, exist_ok=True)
    if abs_from.is_dir():
        shutil.copytree(abs_from, abs_to)
        return [paths.relative(root, p) for p in paths.walk(abs_to)]
    shutil.copy2(abs_from, abs_to)
    return [paths.relative(root, abs_to)]


def remove(vault: Vault, rel: str) -> None:
    """Delete for good. What the trash is for is one level up."""
    abs_path = paths.resolve(Path(vault.root), rel, must_exist=True)
    if abs_path.is_dir():
        shutil.rmtree(abs_path, ignore_errors=True)
    else:
        abs_path.unlink(missing_ok=True)


# ------------------------------------------------------------------- trash

def _trash_root(vault: Vault, options: Options) -> Path:
    return paths.resolve(Path(vault.root), options.trash)


def _free(dest: Path) -> Path:
    """A name that is not taken. A second deletion of the same note must not
    overwrite the first one in the trash — that is where somebody goes looking."""
    if not dest.exists():
        return dest
    stamp = int(time.time() * 1000)
    return dest.with_name(f"{dest.stem}.{stamp}{dest.suffix}")


def trash(vault: Vault, options: Options, rel: str) -> str:
    """Move a path into the trash, keeping the layout it had."""
    root = Path(vault.root)
    abs_from = paths.resolve(root, rel, must_exist=True)
    dest = _free(_trash_root(vault, options) / PurePosixPath(rel))
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(abs_from, dest)
    return paths.relative(root, dest)


def _in_trash(abs_path: Path, trash_root: Path) -> str:
    """The path below the trash, or a refusal. Everything that acts on a trashed
    item goes through here, so nothing outside the trash can be reached by
    calling a trash route with a different path."""
    try:
        rel = abs_path.resolve().relative_to(trash_root.resolve())
    except ValueError:
        raise NotInTrash(str(abs_path)) from None
    if str(rel) in ("", "."):
        raise NotInTrash(str(abs_path))
    return rel.as_posix()


def _prune_empty(start: Path, stop_at: Path) -> None:
    """Take away the folders a restore left behind, up to but not including the
    trash itself."""
    here = start.resolve()
    stop = stop_at.resolve()
    while here != stop and stop in here.parents:
        try:
            if any(here.iterdir()):
                break
            here.rmdir()
        except OSError:
            break
        here = here.parent


def list_trash(vault: Vault, options: Options) -> list[dict]:
    """Everything in the trash, most recently deleted first."""
    root = Path(vault.root)
    trash_root = _trash_root(vault, options)
    out: list[dict] = []
    if not trash_root.is_dir():
        return out
    for here, dirs, files in os.walk(trash_root):
        for name in files:
            abs_path = Path(here) / name
            try:
                st = abs_path.stat()
            except OSError:
                continue
            out.append({
                "name": name,
                "path": paths.relative(root, abs_path),
                "original": abs_path.relative_to(trash_root).as_posix(),
                "ext": abs_path.suffix.lower(),
                "size": st.st_size,
                "mtime": st.st_mtime_ns // 1_000_000,
            })
    out.sort(key=lambda item: -item["mtime"])
    return out


def restore_from_trash(vault: Vault, options: Options, trash_rel: str) -> str:
    """Put a trashed item back where it came from.

    Never over something that is there again: a note recreated at the same path
    after the deletion is somebody's newer work, and restoring on top of it would
    be a second deletion dressed as an undo.
    """
    root = Path(vault.root)
    trash_root = _trash_root(vault, options)
    abs_from = paths.resolve(root, trash_rel, must_exist=True)
    dest_rel = _in_trash(abs_from, trash_root)
    abs_to = paths.resolve(root, dest_rel)
    if abs_to.exists():
        stamp = int(time.time() * 1000)
        abs_to = abs_to.with_name(f"{abs_to.stem}.restored-{stamp}{abs_to.suffix}")
        dest_rel = paths.relative(root, abs_to)
    abs_to.parent.mkdir(parents=True, exist_ok=True)
    os.rename(abs_from, abs_to)
    _prune_empty(abs_from.parent, trash_root)
    return dest_rel


def delete_from_trash(vault: Vault, options: Options, trash_rel: str) -> None:
    root = Path(vault.root)
    trash_root = _trash_root(vault, options)
    abs_path = paths.resolve(root, trash_rel, must_exist=True)
    _in_trash(abs_path, trash_root)
    if abs_path.is_dir():
        shutil.rmtree(abs_path, ignore_errors=True)
    else:
        abs_path.unlink(missing_ok=True)
    _prune_empty(abs_path.parent, trash_root)


def empty_trash(vault: Vault, options: Options) -> None:
    trash_root = _trash_root(vault, options)
    if not trash_root.is_dir():
        return
    for entry in trash_root.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


# ------------------------------------------------------------- attachments

def attachment_dir_for(setting: str, note_path: str) -> str:
    """Where a file dropped into a note belongs.

    The vault's own rule, followed exactly. Without it every upload landed in a
    folder of this program's choosing while the vault kept its own, and the sync
    carried the second folder to every device.
    """
    s = (setting or "/").strip()
    note_dir = note_path[:note_path.rfind("/")] if "/" in note_path else ""
    if s in ("/", ""):
        return ""
    if s in (".", "./"):
        return note_dir
    if s.startswith("./"):
        rest = s[2:]
        return f"{note_dir}/{rest}" if note_dir else rest
    return s.strip("/")


def free_attachment_path(vault: Vault, folder: str, name: str) -> str:
    """A free name for an upload, counting up like the vault does.

    Silently writing over a file is the one outcome nobody wants from dropping
    something into a note.
    """
    dot = name.rfind(".")
    stem = name[:dot] if dot > 0 else name
    ext = name[dot:] if dot > 0 else ""
    for i in range(1000):
        candidate = f"{stem}{ext}" if i == 0 else f"{stem} {i}{ext}"
        rel = f"{folder}/{candidate}" if folder else candidate
        if not exists(vault, rel):
            return rel
    stamp = int(time.time() * 1000)
    tail = f"{stem} {stamp}{ext}"
    return f"{folder}/{tail}" if folder else tail


def resolve_dir_case_insensitive(vault: Vault, rel: str) -> str:
    """Match each folder of a path against the ones that are already there,
    ignoring case.

    Otherwise a vault that keeps `Attachments` gets a second `attachments` next
    to it on a filesystem that tells the two apart, and from then on the pictures
    are in two places.
    """
    root = Path(vault.root)
    out: list[str] = []
    here = root
    for segment in [s for s in rel.split("/") if s]:
        actual = segment
        try:
            entries = [e for e in here.iterdir() if e.is_dir()]
            exact = next((e for e in entries if e.name == segment), None)
            found = exact or next(
                (e for e in entries if e.name.lower() == segment.lower()), None)
            if found is not None:
                actual = found.name
        except OSError:
            pass                       # not there yet: keep what was asked for
        out.append(actual)
        here = here / actual
    return "/".join(out)
