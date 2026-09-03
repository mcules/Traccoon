"""Older versions of a note.

Reading only, out of a repository beside the vault. What is measured here is
mostly what the version list must *not* contain.
"""
from __future__ import annotations

import subprocess

import pytest

from app.notes import history as h
from app.notes import paths


def run(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    """A small repository with a note that changes twice."""
    work = tmp_path / "vault"
    work.mkdir()
    run(work, "init", "-q", "-b", "main")
    run(work, "config", "user.email", "backup@example.invalid")
    run(work, "config", "user.name", "backup")
    note = work / "Eine Notiz.md"
    note.write_text("erste Fassung\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "eins")
    note.write_text("zweite Fassung\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "zwei")
    return h.History(git_dir=work / ".git")


def test_a_missing_repository_is_no_history() -> None:
    with pytest.raises(h.NoHistory):
        h.open_history("")
    empty = h.open_history("/nowhere/at/all")
    assert empty.available is False
    assert empty.info() == {"has": False, "last": None}
    assert empty.log("Eine Notiz.md") == []


def test_the_versions_of_a_note_come_newest_first(repo) -> None:
    versions = repo.log("Eine Notiz.md")
    assert [c.message for c in versions] == ["zwei", "eins"]
    assert repo.show(versions[0].hash, "Eine Notiz.md") == "zweite Fassung\n"
    assert repo.show(versions[1].hash, "Eine Notiz.md") == "erste Fassung\n"


def test_a_note_with_no_history_yet_is_not_an_error(repo) -> None:
    """Every note written since the last backup run is in that state."""
    assert repo.log("Ganz neu.md") == []


def test_the_freshness_of_the_history_is_reported(repo) -> None:
    info = repo.info()
    assert info["has"] is True
    assert info["last"] and info["last"].startswith("20")


def test_a_path_out_of_the_vault_is_refused(repo) -> None:
    newest = repo.log("Eine Notiz.md")[0].hash
    with pytest.raises(paths.OutsideVault):
        repo.log("../etc/passwd")
    with pytest.raises(paths.OutsideVault):
        repo.show(newest, "../../etc/passwd")
    # A leading slash is not an escape but a client writing the path its own
    # way: it is taken off, and what is left can only name something inside the
    # repository — which holds the vault and nothing else.
    with pytest.raises(h.NoHistory):
        repo.show(newest, "/etc/passwd")


def test_only_a_hash_addresses_a_version(repo) -> None:
    """The value goes to a program, so what it may be is decided here."""
    assert h.is_hash("a3f9") and h.is_hash("A3F9")
    assert not h.is_hash("HEAD") and not h.is_hash("v1.0") and not h.is_hash("")
    with pytest.raises(h.NoHistory):
        repo.show("HEAD~1", "Eine Notiz.md")


def test_a_note_is_not_confused_with_a_similar_one(tmp_path) -> None:
    """Daily notes are near copies of each other — same template, same blocks.

    Git's own rename following sees a similarity of ninety-six percent and
    decides one is the other renamed, so the version list of one day carries six
    versions that are a different day. Clicking one shows that other day's text
    as an older version of this one, and restoring it would overwrite the note
    with somebody else's day. That is why nothing here follows renames.
    """
    work = tmp_path / "vault"
    work.mkdir()
    run(work, "init", "-q", "-b", "main")
    run(work, "config", "user.email", "backup@example.invalid")
    run(work, "config", "user.name", "backup")
    body = "\n".join(f"Zeile {i}" for i in range(60)) + "\n"
    (work / "Montag.md").write_text(body, encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "Montag")
    (work / "Montag.md").write_text(body + "noch etwas\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "Montag geändert")
    (work / "Dienstag.md").write_text(body + "etwas anderes\n", encoding="utf-8")
    run(work, "add", "-A")
    run(work, "commit", "-q", "-m", "Dienstag")

    repo = h.History(git_dir=work / ".git")
    assert [c.message for c in repo.log("Dienstag.md")] == ["Dienstag"]
