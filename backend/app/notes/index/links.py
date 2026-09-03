"""Which note points at which, and what is tagged how.

Layer two as well, over the whole vault rather than over one note. Built once by
reading everything, and kept up to date one file at a time afterwards: a vault of
six thousand notes must not be re-read because one of them was saved.

A link names a note, not a place. `[[Meeting]]` finds the note called that
wherever it lies, which is what makes moving a note harmless and what makes two
notes of the same name ambiguous. The ambiguity is real and not resolved here:
the last one read wins, exactly as on the other side, because changing that would
silently move somebody's links.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import paths
from ..model.note import link_key, parse, strip_note_suffix
from ..vault.files import Vault

NOTE_SUFFIXES = (".md", ".markdown")


def _is_note(rel: str) -> bool:
    return rel.lower().endswith(NOTE_SUFFIXES)


@dataclass
class LinkGraph:
    """Everything the graph knows, and nothing that has to be recomputed."""

    # rel -> the keys this note points at
    outgoing: dict[str, set[str]] = field(default_factory=dict)
    # key -> rel, for turning a link into a file
    key_to_path: dict[str, str] = field(default_factory=dict)
    # rel -> the targets as they were written, for rewriting them on a rename
    raw_links: dict[str, list[str]] = field(default_factory=dict)
    # rel -> tags declared in that note
    tags: dict[str, list[str]] = field(default_factory=dict)

    def keys_of(self, rel: str) -> tuple[str, str]:
        """The two names a note can be linked by: its bare name and its path."""
        return (strip_note_suffix(PurePosixPath(rel).name).lower(),
                strip_note_suffix(rel).lower())

    # ------------------------------------------------------------------ build

    def build(self, vault: Vault) -> None:
        notes = [paths.relative(vault.root, p) for p in paths.walk(vault.root)]
        notes = [rel for rel in notes if _is_note(rel)]

        # Names first, all of them, and only then the links. A note that links to
        # one written later would otherwise not resolve, and the graph would
        # depend on the order the disk hands the files over.
        self.key_to_path.clear()
        for rel in notes:
            for key in self.keys_of(rel):
                self.key_to_path[key] = rel

        self.outgoing.clear()
        self.raw_links.clear()
        self.tags.clear()
        for rel in notes:
            self._read(vault, rel)

    def update(self, vault: Vault, rel: str, *, removed: bool = False) -> None:
        """One file changed. Everything else stays as it is."""
        if paths.hidden(rel) or not _is_note(rel):
            return
        bare, full = self.keys_of(rel)
        if removed:
            self.outgoing.pop(rel, None)
            self.raw_links.pop(rel, None)
            self.tags.pop(rel, None)
            # Only drop a name that still points here: two notes can share a
            # bare name, and dropping it blindly would unresolve the other one.
            for key in (bare, full):
                if self.key_to_path.get(key) == rel:
                    del self.key_to_path[key]
            return
        self.key_to_path[bare] = rel
        self.key_to_path[full] = rel
        self._read(vault, rel)

    def _read(self, vault: Vault, rel: str) -> None:
        try:
            note = parse(rel, vault.read_text(rel))
        except (OSError, paths.OutsideVault):
            # A file that cannot be read is still in the graph, with nothing in
            # it. Leaving it out would make it look like a note nobody links
            # from, which is a different statement.
            self.outgoing[rel] = set()
            self.raw_links[rel] = []
            self.tags[rel] = []
            return
        self.outgoing[rel] = {link_key(t) for t in note.links}
        self.raw_links[rel] = note.links
        self.tags[rel] = note.tags

    # ------------------------------------------------------------------ asking

    def resolve(self, target: str) -> str | None:
        return self.key_to_path.get(link_key(target))

    def backlinks(self, rel: str) -> list[str]:
        """The notes pointing at this one, by either of its two names."""
        bare, full = self.keys_of(rel)
        out = [source for source, targets in self.outgoing.items()
               if source != rel and (bare in targets or full in targets)]
        return sorted(out)

    def all_tags(self) -> list[dict]:
        """Every tag with how often it is used, the most used first."""
        counts: dict[str, int] = {}
        for tags in self.tags.values():
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [{"tag": tag, "count": count}
                for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def build_for(root: Path) -> LinkGraph:
    graph = LinkGraph()
    graph.build(Vault(root))
    return graph
