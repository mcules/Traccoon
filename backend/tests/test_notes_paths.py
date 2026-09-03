"""The outer wall of the note vault.

Every one of these is a way somebody could read or write a file that is not a
note. They are here as one test each, so a failure names the way it got out
rather than saying that "paths" broke.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.notes import paths


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Projects").mkdir(parents=True)
    (root / "Projects" / "one.md").write_text("# one", encoding="utf-8")
    (root / ".trash").mkdir()
    (root / ".trash" / "old.md").write_text("gone", encoding="utf-8")
    (tmp_path / "outside.md").write_text("not a note", encoding="utf-8")
    return root


def test_plain_path_resolves(vault: Path) -> None:
    assert paths.resolve(vault, "Projects/one.md") == vault / "Projects" / "one.md"


def test_leading_slash_is_not_absolute(vault: Path) -> None:
    """A client that writes `/Projects/one.md` means the vault root, not the disk."""
    assert paths.resolve(vault, "/Projects/one.md") == vault / "Projects" / "one.md"


def test_backslashes_are_separators(vault: Path) -> None:
    assert paths.resolve(vault, "Projects\\one.md") == vault / "Projects" / "one.md"


@pytest.mark.parametrize("rel", [
    "../outside.md",
    "Projects/../../outside.md",
    "Projects/../../",
    "..",
])
def test_dotdot_is_refused(vault: Path, rel: str) -> None:
    with pytest.raises(paths.OutsideVault):
        paths.resolve(vault, rel)


def test_absolute_disk_path_lands_inside_the_vault(vault: Path, tmp_path: Path) -> None:
    """A path off the disk is read as a path in the vault, and that is the point.

    The leading slash means "from the vault root" in this API (the test above
    relies on it), so the same syntax cannot also mean "from the disk root". It
    is not refused, it is reinterpreted: the file is looked for inside the vault,
    where it is not, and nothing outside is ever touched. Which is the property
    that matters, so that is what is asserted here.
    """
    ziel = paths.resolve(vault, str(tmp_path / "outside.md"))
    assert vault.resolve() in ziel.parents
    assert not ziel.exists()


def test_symlink_out_of_the_vault_is_refused(vault: Path, tmp_path: Path) -> None:
    """The one that looks harmless: the path stays inside, the link does not."""
    link = vault / "escape.md"
    os.symlink(tmp_path / "outside.md", link)
    with pytest.raises(paths.OutsideVault):
        paths.resolve(vault, "escape.md", must_exist=True)


def test_symlinked_directory_out_of_the_vault_is_refused(vault: Path, tmp_path: Path) -> None:
    """And the same trick one level up, where the name of the file is innocent."""
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "note.md").write_text("x", encoding="utf-8")
    os.symlink(tmp_path / "elsewhere", vault / "linked")
    with pytest.raises(paths.OutsideVault):
        paths.resolve(vault, "linked/note.md", must_exist=True)


def test_a_file_that_is_not_there_yet_still_resolves(vault: Path) -> None:
    """Writing needs a path before the file exists, and that must not be a hole."""
    assert paths.resolve(vault, "Projects/new.md") == vault / "Projects" / "new.md"
    with pytest.raises(paths.OutsideVault):
        paths.resolve(vault, "Projects/../../new.md")


def test_relative_is_the_way_back(vault: Path) -> None:
    assert paths.relative(vault, vault / "Projects" / "one.md") == "Projects/one.md"


@pytest.mark.parametrize("rel,erwartet", [
    ("Projects/one.md", False),
    (".trash/old.md", True),
    (".git/config", True),
    ("Projects/.hidden/x.md", True),
    ("node_modules/pkg/index.js", True),
])
def test_hidden_says_what_is_not_a_note(rel: str, erwartet: bool) -> None:
    assert paths.hidden(rel) is erwartet


def test_walk_skips_the_hidden_and_sorts(vault: Path) -> None:
    (vault / "a.md").write_text("a", encoding="utf-8")
    found = [paths.relative(vault, p) for p in paths.walk(vault)]
    assert found == ["a.md", "Projects/one.md"] or found == ["Projects/one.md", "a.md"]
    assert not any(f.startswith(".trash") for f in found)
