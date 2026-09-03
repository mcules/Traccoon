"""One vault, with everything that has to be kept in step with it.

Reading a note is one file. Writing one is four more things: the link graph, the
page index the query languages read, the word index the search ranks with, and —
when the note moved — the text of every note that pointed at it. Doing any of
those in the route that happens to need it is how two of them end up refreshed at
different moments, and an index that describes the vault of a minute ago answers
wrongly without ever saying so.

So a workspace is the vault plus its indexes, and every change goes through
here. The routes above hold no state of their own; the tests below build one
over a folder in a temporary directory and get the real thing.

Writing is the part to be careful with: these are somebody's notes, the folder
has other writers, and a mistake here is not a wrong answer but a lost sentence.
Three rules come out of that and each of them is in the code below:

  * **A save says which version it is replacing.** If the file has moved on, the
    save is refused and the caller is handed what is there now. Two writers
    without that means the later one wins and the earlier one is gone.
  * **What is being replaced is kept first**, when a place to keep it has been
    configured.
  * **A rename reads who points at the note before it moves.** Afterwards the
    graph answers for the new path, and the rewrite would find nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import paths
from .dv import index as dv_index
from .dv.pages import PageIndex
from .index.links import LinkGraph
from .model import rewrite
from .query.words import WordIndex
from .settings.options import Options
from .vault import recovery, write
from .vault.files import Vault, content_hash, is_text

log = logging.getLogger("notes.workspace")


class Conflict(Exception):
    """The file is not what the caller last read. Carries what is there now, so
    whoever asked can merge instead of guessing."""

    def __init__(self, current: str) -> None:
        super().__init__("changed on disk")
        self.current = current
        self.hash = content_hash(current)


def is_note(rel: str) -> bool:
    return rel.lower().endswith((".md", ".markdown"))


@dataclass
class Workspace:
    vault: Vault
    options: Options
    graph: LinkGraph = field(default_factory=LinkGraph)
    pages: PageIndex = field(default_factory=PageIndex)
    words: WordIndex = field(default_factory=WordIndex)
    # Where the copies of replaced text go. None means none are kept, which is
    # what a workspace that has not been given a folder does.
    recovery_root: Path | None = None

    # ------------------------------------------------------------- building

    @classmethod
    def open(cls, vault: Vault, options: Options,
             recovery_root: Path | None = None) -> "Workspace":
        ws = cls(vault=vault, options=options, recovery_root=recovery_root)
        ws.rebuild()
        return ws

    def rebuild(self) -> None:
        self.graph.build(self.vault)
        self.words.build(self.graph.docs, self.graph.headings)
        self.pages = dv_index.build(self.vault)

    def touch(self, rel: str, *, removed: bool = False) -> None:
        """One file changed on disk. Every index hears about it, in one place.

        The watcher calls this too, so a note written from a phone and one
        written in the browser land in the same state.
        """
        if paths.hidden(rel):
            return
        self.graph.update(self.vault, rel, removed=removed)
        dv_index.update(self.vault, self.pages, rel, removed=removed)
        if not is_note(rel):
            return
        if removed:
            self.words.forget(rel)
        else:
            doc = self.graph.docs.get(rel)
            if doc is not None:
                self.words.update(rel, doc, self.graph.headings.get(rel, []))

    # -------------------------------------------------------------- writing

    def save(self, rel: str, text: str, base_hash: str | None = None) -> dict:
        """Put text into a note, if it is still the note the caller read.

        `base_hash` is what makes that possible. The vault has several writers —
        the file sync carries what was written on another device, agents write
        into it, jobs write into it — and a plain write would discard whatever
        arrived in between without anybody noticing.
        """
        exists = write.exists(self.vault, rel)
        previous = self.vault.read_text(rel) if exists and is_text(rel) else None
        if base_hash:
            current = previous if previous is not None else ""
            if content_hash(current) != base_hash:
                raise Conflict(current)
        if previous is not None:
            recovery.snapshot(self.recovery_root, rel, previous)
        write.write_text(self.vault, rel, text)
        self.touch(rel)
        return {"ok": True, "path": rel, "hash": content_hash(text)}

    def create_folder(self, rel: str) -> dict:
        write.create_folder(self.vault, rel)
        return {"ok": True, "path": rel}

    def upload(self, name: str, data: bytes, *, folder: str | None = None,
               note: str = "") -> dict:
        """Put a file into the vault where the vault keeps its attachments.

        Without an explicit folder the vault's own setting decides, worked out
        against the note the file is going into.
        """
        wanted = folder if folder is not None else write.attachment_dir_for(
            self.options.attachment_folder, note)
        resolved = write.resolve_dir_case_insensitive(self.vault, wanted) if wanted else ""
        rel = write.free_attachment_path(self.vault, resolved, name)
        write.write_bytes(self.vault, rel, data)
        self.touch(rel)
        return {"ok": True, "path": rel, "size": len(data)}

    def copy(self, source: str, target: str) -> dict:
        for rel in write.copy(self.vault, source, target):
            self.touch(rel)
        return {"ok": True, "from": source, "to": target}

    def delete(self, rel: str) -> dict:
        """Delete, which by default means move into the trash.

        A folder takes everything under it with it, so every note in it leaves
        the indexes as well — otherwise the search keeps finding notes that are
        no longer there and a link to one of them looks alive.
        """
        gone = self._notes_under(rel)
        if self.options.delete_mode == "permanent":
            write.remove(self.vault, rel)
            for note in gone:
                self.touch(note, removed=True)
            return {"ok": True, "deleted": rel}
        dest = write.trash(self.vault, self.options, rel)
        for note in gone:
            self.touch(note, removed=True)
        return {"ok": True, "trashed": dest}

    def restore(self, trash_rel: str) -> dict:
        restored = write.restore_from_trash(self.vault, self.options, trash_rel)
        for note in self._notes_under(restored):
            self.touch(note)
        return {"ok": True, "restored": restored}

    def _notes_under(self, rel: str) -> list[str]:
        """A path as the list of note paths it stands for: itself, or everything
        below it when it is a folder."""
        if is_note(rel):
            return [rel]
        root = Path(self.vault.root)
        try:
            here = paths.resolve(root, rel, must_exist=True)
        except (paths.OutsideVault, OSError):
            return []
        if not here.is_dir():
            return [rel]
        return [p for p in (paths.relative(root, f) for f in paths.walk(here))
                if is_note(p)]

    # -------------------------------------------------------------- renaming

    def rename(self, source: str, target: str, *, dry_run: bool = False) -> dict:
        """Move a note or a folder, and take the links with it.

        Which notes point at the one that moves has to be read **before** it
        moves; afterwards the graph answers for the new path. A folder moves
        every note inside it, so each of them gets its own pair and its own list
        of sources.
        """
        pairs: list[tuple[str, str]]
        if is_note(source):
            pairs = [(source, target)]
        else:
            pairs = [(p, target + p[len(source):]) for p in self._notes_under(source)]
        contexts = [rewrite.collect_context(self.graph, f) for f, _ in pairs]

        if not dry_run:
            write.rename(self.vault, source, target)
            for f, t in pairs:
                self.touch(f, removed=True)
                self.touch(t)

        # The vault's own switch decides whether links follow silently. With it
        # off the caller still learns what would change rather than guessing.
        follow = self.options.follow_links_on_rename and not dry_run
        files: list[dict] = []
        links = 0
        all_notes = [p for p in self.pages.pages] or self._notes_under("")
        for (f, t), context in zip(pairs, contexts):
            # A note that lived inside the moved folder sits somewhere else now.
            sources = [
                (target + p[len(source):])
                if (not is_note(source) and p.startswith(source + "/") and not dry_run)
                else p
                for p in context.sources
            ]
            result = self._rewrite_for(f, t, sources, context, all_notes,
                                       apply=follow)
            files.extend(result.files)
            links += result.links
        return {"ok": True, "from": source, "to": target,
                "linkUpdates": {"files": files, "links": links, "applied": follow}}

    def _rewrite_for(self, source: str, target: str, sources: list[str],
                     context: rewrite.RenameContext, all_notes: list[str],
                     *, apply: bool) -> rewrite.LinkUpdate:
        new_name = (rewrite.shortest_name(target, all_notes)
                    if is_note(target) else target)
        new_href = rewrite.href_for(target)
        decide = rewrite.points_at(source, context, self.graph)
        out = rewrite.LinkUpdate()
        for rel in sources:
            if rel == source:
                continue
            try:
                text = self.vault.read_text(rel)
            except (OSError, paths.OutsideVault):
                continue
            result = rewrite.rewrite_in_text(text, new_name=new_name,
                                             new_href=new_href,
                                             points_at_moved=decide)
            if not result.count or result.text == text:
                continue
            out.files.append({"path": rel, "count": result.count})
            out.links += result.count
            if apply:
                write.write_text(self.vault, rel, result.text)
                self.touch(rel)
        return out

    # ------------------------------------------------------------- the trash

    def trash_items(self) -> list[dict]:
        return write.list_trash(self.vault, self.options)

    def delete_from_trash(self, trash_rel: str) -> dict:
        write.delete_from_trash(self.vault, self.options, trash_rel)
        return {"ok": True}

    def empty_trash(self) -> dict:
        write.empty_trash(self.vault, self.options)
        return {"ok": True}

    # ---------------------------------------------------------- what is kept

    def snapshots(self, rel: str) -> list[dict]:
        return recovery.snapshots(self.recovery_root, rel)

    def snapshot_text(self, rel: str, ts: int) -> str | None:
        return recovery.read(self.recovery_root, rel, ts)

    def restore_snapshot(self, rel: str, ts: int) -> dict | None:
        """Put a kept version back. Restoring is an edit, so what it replaces is
        kept as well — otherwise going back one step too far is a one-way trip."""
        text = recovery.read(self.recovery_root, rel, ts)
        if text is None:
            return None
        if write.exists(self.vault, rel) and is_text(rel):
            recovery.snapshot(self.recovery_root, rel, self.vault.read_text(rel))
        write.write_text(self.vault, rel, text)
        self.touch(rel)
        return {"ok": True, "path": rel}
