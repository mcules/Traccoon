"""Keeping the index in step with the disk."""
from __future__ import annotations

from pathlib import Path

import pytest
from watchfiles import Change

from app.notes.index.links import LinkGraph
from app.notes.index.watch import apply


def into(v, g):
    """What taking a file into the index means, for these tests: the link graph
    and nothing else. The workspace hands the watcher a callback that updates
    every index at once; here one of them is enough."""
    return lambda rel, gone: g.update(v, rel, removed=gone)
from app.notes.vault.files import Vault


@pytest.fixture()
def paar(tmp_path: Path) -> tuple[Vault, LinkGraph]:
    root = tmp_path / "v"
    root.mkdir()
    (root / "A.md").write_text("[[B]]\n", encoding="utf-8")
    (root / "B.md").write_text("nichts #alt\n", encoding="utf-8")
    v = Vault(root)
    g = LinkGraph()
    g.build(v)
    return v, g


def test_a_changed_note_reaches_the_index(paar) -> None:
    v, g = paar
    (Path(v.root) / "B.md").write_text("jetzt [[A]] #neu\n", encoding="utf-8")
    assert apply(v, {(Change.modified, str(Path(v.root) / "B.md"))}, into(v, g)) == 1
    assert g.backlinks("A.md") == ["B.md"]
    assert {t["tag"] for t in g.all_tags()} == {"neu"}


def test_a_new_note_becomes_linkable(paar) -> None:
    v, g = paar
    (Path(v.root) / "C.md").write_text("neu\n", encoding="utf-8")
    apply(v, {(Change.added, str(Path(v.root) / "C.md"))}, into(v, g))
    assert g.resolve("C") == "C.md"


def test_a_deleted_note_leaves(paar) -> None:
    v, g = paar
    (Path(v.root) / "B.md").unlink()
    apply(v, {(Change.deleted, str(Path(v.root) / "B.md"))}, into(v, g))
    assert g.resolve("B") is None


def test_a_save_that_arrives_as_delete_plus_add_does_not_lose_the_note(paar) -> None:
    """Writing to a temporary file and renaming over the target is how careful
    programs save. It arrives as two events, in either order, and taking the
    delete at face value would drop a note that is right there on the disk."""
    v, g = paar
    ziel = Path(v.root) / "B.md"
    ziel.write_text("immer noch da #neu\n", encoding="utf-8")
    apply(v, {(Change.deleted, str(ziel)), (Change.added, str(ziel))}, into(v, g))
    assert g.resolve("B") == "B.md"
    assert {t["tag"] for t in g.all_tags()} == {"neu"}


def test_the_hidden_is_not_watched(paar) -> None:
    v, g = paar
    versteckt = Path(v.root) / ".trash"
    versteckt.mkdir()
    (versteckt / "weg.md").write_text("x", encoding="utf-8")
    assert apply(v, {(Change.added, str(versteckt / "weg.md"))}, into(v, g)) == 0
    assert g.resolve("weg") is None


def test_a_file_from_another_folder_is_ignored(paar, tmp_path: Path) -> None:
    v, g = paar
    fremd = tmp_path / "woanders.md"
    fremd.write_text("x", encoding="utf-8")
    assert apply(v, {(Change.added, str(fremd))}, into(v, g)) == 0


def test_something_that_is_not_a_note_does_not_enter_the_graph(paar) -> None:
    v, g = paar
    bild = Path(v.root) / "bild.png"
    bild.write_bytes(b"\x89PNG")
    apply(v, {(Change.added, str(bild))}, into(v, g))
    assert "bild.png" not in g.outgoing
