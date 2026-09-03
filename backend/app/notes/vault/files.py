"""Reading the vault: the files themselves, and the tree over them.

Layer one of the note package. It knows the disk and nothing else — no markdown,
no queries, no HTTP. Everything above it takes what this hands back.

The answers here are shaped like the ones the service being replaced gives,
deliberately and for exactly as long as the port runs: that is what lets the two
be asked the same question and their answers compared. When the old service is
gone, the shape is free again.
"""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths

# What is read as text. `.base` is YAML, and reading it as text is what lets one
# be opened and shown at all.
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".json", ".csv", ".canvas", ".base",
    ".excalidraw", ".css", ".js", ".yml", ".yaml",
}

# Types the standard table does not know or gets wrong for our purposes.
EXTRA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".canvas": "application/json; charset=utf-8",
    ".base": "text/yaml; charset=utf-8",
    ".excalidraw": "application/json; charset=utf-8",
    ".webp": "image/webp",
    ".avif": "image/avif",
}


def is_text(rel: str) -> bool:
    return Path(rel).suffix.lower() in TEXT_SUFFIXES


def mime_for(rel: str) -> str:
    suffix = Path(rel).suffix.lower()
    if suffix in EXTRA_TYPES:
        return EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(rel)
    return guessed or "application/octet-stream"


def content_hash(text: str) -> str:
    """The identity of a version of a note.

    Short on purpose, and sha1 rather than something stronger: this answers "is
    this still the text I read a minute ago", not "did somebody forge it". It
    travels with every read and comes back with every write, which is how a save
    tells a quiet conflict from a normal one. Sixteen characters of it are what
    the other side sends, and both have to agree or every save looks like a
    conflict.
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Node:
    """One entry of the tree. `children` is None for a file, a list for a folder."""

    name: str
    path: str
    type: str                      # "file" | "folder"
    ext: str | None = None
    size: int | None = None
    mtime: int | None = None       # milliseconds, like the other side sends
    ctime: int | None = None
    children: list["Node"] | None = None

    def as_json(self) -> dict:
        out: dict = {"name": self.name, "path": self.path, "type": self.type}
        if self.ext is not None:
            out["ext"] = self.ext
        if self.size is not None:
            out["size"] = self.size
        if self.mtime is not None:
            out["mtime"] = self.mtime
        if self.ctime is not None:
            out["ctime"] = self.ctime
        if self.children is not None:
            out["children"] = [c.as_json() for c in self.children]
        return out


@dataclass
class Vault:
    """One vault, one person. Everything below takes the root from here."""

    root: Path
    name: str = ""

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if not self.name:
            self.name = self.root.name

    # ---------------------------------------------------------------- reading

    def exists(self, rel: str) -> bool:
        try:
            return paths.resolve(self.root, rel).exists()
        except paths.OutsideVault:
            return False

    def read_text(self, rel: str) -> str:
        """A note as text, byte for byte as it lies on the disk.

        `newline=""` is not a detail. Python translates line endings on the way
        in by default, so a file written on a machine that uses CRLF comes back
        shorter than it is: every `\r` gone. Reading is then wrong by a few
        bytes per line, the hash of that text does not match what the other side
        computed, every save looks like somebody else's edit, and writing the
        text back would quietly rewrite the file for every machine it syncs to.
        Measured on this vault before the fix: 49 of 200 notes differed, always
        by exactly the number of their lines.

        `errors="replace"` for the other case: a shared vault picks up the odd
        file that is not clean UTF-8, and one of those must not be able to make a
        folder unreadable.
        """
        abs_path = paths.resolve(self.root, rel, must_exist=True)
        with abs_path.open(encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()

    def read_bytes(self, rel: str) -> bytes:
        return paths.resolve(self.root, rel, must_exist=True).read_bytes()

    def stat(self, rel: str) -> dict:
        st = paths.resolve(self.root, rel, must_exist=True).stat()
        return {"size": st.st_size,
                "mtime": int(st.st_mtime * 1000),
                "ctime": int(getattr(st, "st_birthtime", st.st_ctime) * 1000)}

    # ------------------------------------------------------------------- tree

    def tree(self) -> Node:
        """The whole vault as one nested answer.

        Built in one walk with the folders sorted before the files and both by
        name, because that is the order a person expects and because a stable
        order is what makes two answers comparable at all.
        """
        return self._folder(self.root, "", self.name)

    def _folder(self, abs_path: Path, rel: str, name: str) -> Node:
        folders: list[Node] = []
        files: list[Node] = []
        try:
            entries = sorted(abs_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            entries = []
        for entry in entries:
            if entry.name.startswith(".") or entry.name in paths.SKIP_DIRS:
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir() and not entry.is_symlink():
                folders.append(self._folder(entry, child_rel, entry.name))
            elif entry.is_file():
                files.append(self._file(entry, child_rel))
        return Node(name=name, path=rel, type="folder", children=folders + files)

    def _file(self, abs_path: Path, rel: str) -> Node:
        try:
            st = abs_path.stat()
            size, mtime = st.st_size, int(st.st_mtime * 1000)
            ctime = int(getattr(st, "st_birthtime", st.st_ctime) * 1000)
        except OSError:
            size = mtime = ctime = None
        return Node(name=abs_path.name, path=rel, type="file",
                    # With the dot, which is what the other side sends.
                    ext=abs_path.suffix.lower() or None,
                    size=size, mtime=mtime, ctime=ctime)

    # ------------------------------------------------- finding a file by name

    def by_basename(self) -> dict[str, list[str]]:
        """Every file under its bare name, for resolving `![[image.png]]`.

        A link in a note names the file and not where it lies. Several files can
        share a name, so this keeps all of them and the caller decides; taking
        the first would silently pick one and be right most of the time, which is
        the worst kind of wrong.
        """
        out: dict[str, list[str]] = {}
        for abs_path in paths.walk(self.root):
            rel = paths.relative(self.root, abs_path)
            out.setdefault(abs_path.name.lower(), []).append(rel)
            stem = abs_path.stem.lower()
            if stem != abs_path.name.lower():
                out.setdefault(stem, []).append(rel)
        return out
