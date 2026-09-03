"""Writing into the vault: saving, moving, deleting, and what follows along.

These are somebody's notes and the folder has other writers, so the tests here
are about the ways a write can quietly lose a sentence rather than about the
happy path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.notes import paths
from app.notes.settings.options import Options
from app.notes.vault import write
from app.notes.vault.files import Vault, content_hash
from app.notes.workspace import Conflict, Workspace

NOTES = {
    "02 Projekte/Alpha.md": "# Alpha\n\nSiehe [[Ziel]] und [[Ordner/Ziel|Label]].\n",
    "02 Projekte/Beta.md": "# Beta\n\n```\n[[Ziel]] im Code\n```\n\nUnd `[[Ziel]]`.\n",
    "Ordner/Ziel.md": "# Ziel\n",
    "Ordner/Anderes.md": "# Anderes\n",
}


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "vault"
    for rel, text in NOTES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / ".trash").mkdir()
    return Workspace.open(Vault(root), Options(), recovery_root=tmp_path / "recovery")


def read(ws: Workspace, rel: str) -> str:
    return ws.vault.read_text(rel)


# ------------------------------------------------------------------- saving

def test_a_save_lands_and_says_which_version_it_made(ws) -> None:
    out = ws.save("Ordner/Ziel.md", "# Ziel\n\nNeu.\n")
    assert out["hash"] == content_hash("# Ziel\n\nNeu.\n")
    assert read(ws, "Ordner/Ziel.md") == "# Ziel\n\nNeu.\n"


def test_a_save_against_an_old_version_is_refused_and_hands_back_what_is_there(ws) -> None:
    """Otherwise the later writer wins and what the earlier one wrote is gone,
    with nothing anywhere saying so."""
    old = content_hash(read(ws, "Ordner/Ziel.md"))
    ws.save("Ordner/Ziel.md", "von der anderen Seite\n")
    with pytest.raises(Conflict) as err:
        ws.save("Ordner/Ziel.md", "meins\n", base_hash=old)
    assert err.value.current == "von der anderen Seite\n"
    assert read(ws, "Ordner/Ziel.md") == "von der anderen Seite\n"


def test_a_save_with_the_right_version_goes_through(ws) -> None:
    ws.save("Ordner/Ziel.md", "eins\n", base_hash=content_hash(read(ws, "Ordner/Ziel.md")))
    assert read(ws, "Ordner/Ziel.md") == "eins\n"


def test_carriage_returns_are_written_through(ws) -> None:
    """Rewriting them would make every device see the whole file as changed for
    a change nobody made."""
    ws.save("Ordner/Ziel.md", "eins\r\nzwei\r\n")
    assert (Path(ws.vault.root) / "Ordner/Ziel.md").read_bytes() == b"eins\r\nzwei\r\n"


def test_a_save_leaves_no_scratch_file_behind(ws) -> None:
    ws.save("Ordner/Ziel.md", "x\n")
    leftovers = list((Path(ws.vault.root) / "Ordner").glob(".*wo-tmp*"))
    assert leftovers == []


def test_the_scratch_file_is_named_so_the_sync_ignores_it(tmp_path) -> None:
    """The ignore list on this machine matches `**/.*.wo-tmp-*`. Changing the
    name means changing that list in the same step, or every peer picks up
    half-written files."""
    name = write._temp_sibling(tmp_path / "Note.md").name
    assert name.startswith(".Note.md.wo-tmp-")


def test_a_path_that_leaves_the_vault_is_refused(ws) -> None:
    with pytest.raises(paths.OutsideVault):
        ws.save("../draussen.md", "nein\n")


# ------------------------------------------------------------ the indexes

def test_a_new_note_is_findable_at_once(ws) -> None:
    ws.save("02 Projekte/Gamma.md", "# Gamma\n\nEin Wort: Zwiebelkuchen\n")
    assert "02 Projekte/Gamma.md" in ws.pages.pages
    assert any(rel == "02 Projekte/Gamma.md"
               for rel, _ in ws.words.search("Zwiebelkuchen"))


def test_a_deleted_note_stops_being_found(ws) -> None:
    ws.save("02 Projekte/Gamma.md", "Zwiebelkuchen\n")
    ws.delete("02 Projekte/Gamma.md")
    assert "02 Projekte/Gamma.md" not in ws.pages.pages
    assert ws.words.search("Zwiebelkuchen") == []
    assert ws.graph.resolve("Gamma") is None


def test_deleting_a_folder_takes_every_note_in_it_out(ws) -> None:
    ws.delete("Ordner")
    assert "Ordner/Ziel.md" not in ws.pages.pages
    assert "Ordner/Anderes.md" not in ws.pages.pages


# ------------------------------------------------------------------- trash

def test_deleting_moves_into_the_trash_and_keeps_the_layout(ws) -> None:
    out = ws.delete("Ordner/Ziel.md")
    assert out["trashed"] == ".trash/Ordner/Ziel.md"
    assert not write.exists(ws.vault, "Ordner/Ziel.md")


def test_deleting_the_same_name_twice_does_not_overwrite_the_first(ws) -> None:
    ws.delete("Ordner/Ziel.md")
    ws.save("Ordner/Ziel.md", "die zweite\n")
    second = ws.delete("Ordner/Ziel.md")["trashed"]
    assert second != ".trash/Ordner/Ziel.md"
    assert len(ws.trash_items()) == 2


def test_permanent_means_permanent(tmp_path, ws) -> None:
    ws.options = Options(delete_mode="permanent")
    ws.delete("Ordner/Ziel.md")
    assert ws.trash_items() == []
    assert not write.exists(ws.vault, "Ordner/Ziel.md")


def test_restoring_puts_it_back_where_it_was(ws) -> None:
    trashed = ws.delete("Ordner/Ziel.md")["trashed"]
    assert ws.restore(trashed)["restored"] == "Ordner/Ziel.md"
    assert read(ws, "Ordner/Ziel.md") == "# Ziel\n"
    assert ws.graph.resolve("Ziel") == "Ordner/Ziel.md"


def test_restoring_never_writes_over_newer_work(ws) -> None:
    """A note recreated at the same path after the deletion is somebody's newer
    work; restoring on top of it would be a second deletion dressed as an undo."""
    trashed = ws.delete("Ordner/Ziel.md")["trashed"]
    ws.save("Ordner/Ziel.md", "inzwischen neu geschrieben\n")
    restored = ws.restore(trashed)["restored"]
    assert restored != "Ordner/Ziel.md"
    assert read(ws, "Ordner/Ziel.md") == "inzwischen neu geschrieben\n"


def test_a_trash_route_cannot_reach_outside_the_trash(ws) -> None:
    from app.notes.vault.write import NotInTrash
    with pytest.raises(NotInTrash):
        ws.delete_from_trash("Ordner/Anderes.md")
    assert write.exists(ws.vault, "Ordner/Anderes.md")


def test_emptying_the_trash_leaves_the_vault_alone(ws) -> None:
    ws.delete("Ordner/Ziel.md")
    ws.empty_trash()
    assert ws.trash_items() == []
    assert write.exists(ws.vault, "Ordner/Anderes.md")


# ---------------------------------------------------------------- renaming

def test_a_rename_takes_the_links_with_it(ws) -> None:
    out = ws.rename("Ordner/Ziel.md", "Ordner/Neu.md")
    assert out["linkUpdates"]["applied"] is True
    text = read(ws, "02 Projekte/Alpha.md")
    assert "[[Neu]]" in text and "[[Neu|Label]]" in text
    assert "[[Ziel]]" not in text


def test_a_rename_leaves_code_and_comments_alone(ws) -> None:
    ws.rename("Ordner/Ziel.md", "Ordner/Neu.md")
    beta = read(ws, "02 Projekte/Beta.md")
    assert "[[Ziel]] im Code" in beta
    assert "`[[Ziel]]`" in beta


def test_a_dry_run_changes_nothing_and_still_says_what_it_would(ws) -> None:
    out = ws.rename("Ordner/Ziel.md", "Ordner/Neu.md", dry_run=True)
    assert out["linkUpdates"]["links"] == 2
    assert out["linkUpdates"]["applied"] is False
    assert write.exists(ws.vault, "Ordner/Ziel.md")
    assert "[[Ziel]]" in read(ws, "02 Projekte/Alpha.md")


def test_a_bare_name_two_notes_share_is_not_rewritten(tmp_path) -> None:
    """Pointing it somewhere by guesswork would move a link the writer never
    touched."""
    root = tmp_path / "vault"
    for rel, text in {
        "A/Ziel.md": "", "B/Ziel.md": "",
        "Quelle.md": "[[Ziel]] und [[A/Ziel]]\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    ws.rename("A/Ziel.md", "A/Neu.md")
    text = read(ws, "Quelle.md")
    # `Neu` is now unique, so the shortest form that still names it is the bare
    # one — that is the form the vault is written in.
    assert "[[Neu]]" in text
    assert "[[Ziel]]" in text            # untouched: it could mean either


def test_renaming_a_folder_moves_every_note_and_the_links_follow(ws) -> None:
    ws.rename("Ordner", "Anderswo")
    assert write.exists(ws.vault, "Anderswo/Ziel.md")
    assert not write.exists(ws.vault, "Ordner/Ziel.md")
    assert "[[Ziel]]" in read(ws, "02 Projekte/Alpha.md")   # the bare name still fits
    assert ws.graph.resolve("Ziel") == "Anderswo/Ziel.md"


def test_a_markdown_link_is_rewritten_and_escaped(tmp_path) -> None:
    root = tmp_path / "vault"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Alt Name.md").write_text("", encoding="utf-8")
    (root / "Quelle.md").write_text("[[Alt Name]] und [Text](Alt%20Name.md)\n",
                                    encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    ws.rename("Alt Name.md", "Neu Name.md")
    assert read(ws, "Quelle.md") == "[[Neu Name]] und [Text](Neu%20Name.md)\n"


def test_a_note_that_only_links_the_markdown_way_is_not_reached(tmp_path) -> None:
    """The link graph is built from wikilinks alone, so a note that refers to
    another only as `[text](note.md)` is not counted as pointing at it and is
    therefore never visited on a rename. Copied rather than fixed: the graph is
    what the backlinks panel shows as well, and widening it here would make the
    two disagree. Worth widening, both at once, and not in passing."""
    root = tmp_path / "vault"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Alt.md").write_text("", encoding="utf-8")
    (root / "Quelle.md").write_text("[Text](Alt.md)\n", encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    ws.rename("Alt.md", "Neu.md")
    assert read(ws, "Quelle.md") == "[Text](Alt.md)\n"


# ------------------------------------------------------------- attachments

def test_an_upload_lands_where_the_vault_keeps_its_attachments(ws) -> None:
    ws.options = Options(attachment_folder="07 Anhänge")
    out = ws.upload("bild.png", b"\x89PNG", note="02 Projekte/Alpha.md")
    assert out["path"] == "07 Anhänge/bild.png"


def test_an_upload_beside_the_note_follows_the_note(ws) -> None:
    ws.options = Options(attachment_folder=".")
    assert ws.upload("bild.png", b"x", note="02 Projekte/Alpha.md")["path"] \
        == "02 Projekte/bild.png"


def test_a_second_file_of_the_same_name_counts_up(ws) -> None:
    ws.options = Options(attachment_folder="Anhang")
    first = ws.upload("bild.png", b"a")["path"]
    second = ws.upload("bild.png", b"b")["path"]
    assert first == "Anhang/bild.png"
    assert second == "Anhang/bild 1.png"


def test_an_existing_folder_of_another_casing_is_reused(ws) -> None:
    """Otherwise the vault ends up with two attachment folders and the sync
    carries the mistake to every device."""
    write.create_folder(ws.vault, "Anhänge")
    ws.options = Options(attachment_folder="anhänge")
    assert ws.upload("bild.png", b"x")["path"] == "Anhänge/bild.png"


# --------------------------------------------------------------- snapshots

def test_what_a_save_replaces_is_kept(ws) -> None:
    ws.save("Ordner/Ziel.md", "zweite Fassung\n")
    kept = ws.snapshots("Ordner/Ziel.md")
    assert len(kept) == 1
    assert ws.snapshot_text("Ordner/Ziel.md", kept[0]["ts"]) == "# Ziel\n"


def test_a_kept_version_can_be_put_back(ws) -> None:
    ws.save("Ordner/Ziel.md", "zweite Fassung\n")
    ts = ws.snapshots("Ordner/Ziel.md")[0]["ts"]
    assert ws.restore_snapshot("Ordner/Ziel.md", ts) is not None
    assert read(ws, "Ordner/Ziel.md") == "# Ziel\n"


def test_without_a_folder_nothing_is_kept_and_nothing_breaks(tmp_path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "A.md").write_text("eins\n", encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    ws.save("A.md", "zwei\n")
    assert ws.snapshots("A.md") == []
    assert read(ws, "A.md") == "zwei\n"
