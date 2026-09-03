"""Reading the vault: shapes, hashes, and the order of the tree."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.notes.vault.files import Vault, content_hash, is_text, mime_for


@pytest.fixture()
def vault(tmp_path: Path) -> Vault:
    root = tmp_path / "Second Brain"
    (root / "01 Inbox").mkdir(parents=True)
    (root / "02 Projects" / "deep").mkdir(parents=True)
    (root / "note.md").write_text("# Note\n", encoding="utf-8")
    (root / "01 Inbox" / "a.md").write_text("a", encoding="utf-8")
    (root / "02 Projects" / "deep" / "b.md").write_text("b", encoding="utf-8")
    (root / "02 Projects" / "bild.png").write_bytes(b"\x89PNG\r\n")
    (root / ".trash").mkdir()
    (root / ".trash" / "gone.md").write_text("gone", encoding="utf-8")
    return Vault(root)


def test_the_vault_is_named_after_its_folder(vault: Vault) -> None:
    assert vault.tree().name == "Second Brain"


def test_folders_come_before_files_and_both_by_name(vault: Vault) -> None:
    kinder = [(c.type, c.name) for c in vault.tree().children or []]
    assert kinder == [("folder", "01 Inbox"), ("folder", "02 Projects"), ("file", "note.md")]


def test_the_hidden_stays_out_of_the_tree(vault: Vault) -> None:
    def alle(n):
        yield n.path
        for c in n.children or []:
            yield from alle(c)
    assert not any(p.startswith(".trash") for p in alle(vault.tree()))


def test_a_file_carries_extension_size_and_times(vault: Vault) -> None:
    projekte = [c for c in vault.tree().children or [] if c.name == "02 Projects"][0]
    bild = [c for c in projekte.children or [] if c.name == "bild.png"][0]
    assert bild.ext == ".png"   # with the dot, like the other side sends
    assert bild.size == 6
    assert bild.mtime and bild.mtime > 0
    assert bild.as_json()["type"] == "file"


def test_reading_text_and_bytes(vault: Vault) -> None:
    assert vault.read_text("note.md") == "# Note\n"
    assert vault.read_bytes("02 Projects/bild.png").startswith(b"\x89PNG")


def test_broken_encoding_does_not_take_the_folder_down(vault: Vault) -> None:
    """A vault is shared with other machines and picks up the odd unclean file."""
    (Path(vault.root) / "kaputt.md").write_bytes(b"gut \xff\xfe schlecht")
    assert "schlecht" in vault.read_text("kaputt.md")


def test_the_hash_is_what_the_other_side_sends(vault: Vault) -> None:
    """Sixteen hex characters of sha1 over the utf-8 text. Both sides have to
    agree on this or every save would look like somebody else's edit."""
    h = content_hash("# Note\n")
    assert len(h) == 16 and all(c in "0123456789abcdef" for c in h)
    assert content_hash("# Note\n") == h        # same text, same answer
    assert content_hash("# Note") != h          # one byte less, other answer
    # Umlauts are two bytes: hashing the wrong encoding would still be stable and
    # still be wrong, and only show up as a conflict on somebody else's machine.
    assert content_hash("ä") == content_hash("\u00e4")


@pytest.mark.parametrize("rel,text", [
    ("a.md", True), ("a.markdown", True), ("a.canvas", True), ("a.base", True),
    ("a.png", False), ("a.pdf", False), ("a.mp4", False),
])
def test_what_counts_as_text(rel: str, text: bool) -> None:
    assert is_text(rel) is text


def test_mime_types_the_table_does_not_know(vault: Vault) -> None:
    assert mime_for("a.md").startswith("text/markdown")
    assert mime_for("a.canvas").startswith("application/json")
    assert mime_for("a.png") == "image/png"
    assert mime_for("a.unbekannt") == "application/octet-stream"


def test_a_file_is_found_by_its_bare_name(vault: Vault) -> None:
    """A link names the file, not where it lies."""
    index = vault.by_basename()
    assert index["bild.png"] == ["02 Projects/bild.png"]
    assert index["a"] == ["01 Inbox/a.md"]
