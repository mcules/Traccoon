"""The routes over the workspace: what they add, which is the answers.

The work itself is tested one layer down. What matters here is what a caller
sees — above all when a save is refused, because the answer has to carry enough
for the editor to merge rather than ask again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.api import notes_native as nn
from app.notes.settings import options as vault_options
from app.notes.vault.files import content_hash


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A vault of its own per test, and a workspace cache that does not outlive it."""
    root = tmp_path / "vault"
    (root / "Ordner").mkdir(parents=True)
    (root / "Ordner/Ziel.md").write_text("# Ziel\n", encoding="utf-8")
    (root / "Quelle.md").write_text("Siehe [[Ziel]].\n", encoding="utf-8")
    monkeypatch.setattr(nn, "_workspaces", {})
    monkeypatch.setattr(nn, "_watchers", {})
    monkeypatch.setattr(nn, "_settings_loaded", True)   # nothing to read from a fresh vault
    vault_options.reset()
    return root


@pytest.fixture()
def user(vault):
    class U:
        vault_path = str(vault)
    return U()


@pytest.mark.asyncio
async def test_a_save_answers_with_the_version_it_made(user) -> None:
    out = await nn.save(nn.SaveIn(path="Ordner/Ziel.md", content="neu\n"), user)
    assert out == {"ok": True, "path": "Ordner/Ziel.md", "hash": content_hash("neu\n")}


@pytest.mark.asyncio
async def test_a_refused_save_hands_back_what_is_there_now(user) -> None:
    """Without the current text the editor can only ask again — and by the time
    that answer arrives the note may have moved on once more."""
    from app.core.error import Error
    stale = content_hash("# Ziel\n")
    await nn.save(nn.SaveIn(path="Ordner/Ziel.md", content="von woanders\n"), user)
    with pytest.raises(Error) as err:
        await nn.save(nn.SaveIn(path="Ordner/Ziel.md", content="meins\n",
                                baseHash=stale), user)
    assert err.value.status_code == 409
    assert err.value.key == "err.notes_changed_on_disk"
    assert err.value.values["current"] == "von woanders\n"


@pytest.mark.asyncio
async def test_a_path_out_of_the_vault_reads_as_not_found(user) -> None:
    """Deliberately the same answer as for a note that is not there. Telling the
    two apart would say whether a path exists outside the vault."""
    from app.core.error import Error
    with pytest.raises(Error) as err:
        await nn.save(nn.SaveIn(path="../draussen.md", content="nein\n"), user)
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_a_rename_reports_the_links_it_moved(user) -> None:
    out = await nn.rename(nn.RenameIn(**{"from": "Ordner/Ziel.md",
                                         "to": "Ordner/Neu.md"}), user)
    assert out["linkUpdates"]["links"] == 1
    assert out["linkUpdates"]["applied"] is True


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(user, vault) -> None:
    out = await nn.rename(nn.RenameIn(**{"from": "Ordner/Ziel.md",
                                         "to": "Ordner/Neu.md", "dryRun": True}), user)
    assert out["linkUpdates"]["applied"] is False
    assert (vault / "Ordner/Ziel.md").exists()


@pytest.mark.asyncio
async def test_delete_restore_and_the_trash_in_between(user) -> None:
    trashed = (await nn.delete("Ordner/Ziel.md", user))["trashed"]
    items = (await nn.trash_list(user))["items"]
    assert [i["original"] for i in items] == ["Ordner/Ziel.md"]
    assert (await nn.trash_restore(nn.PathIn(path=trashed), user))["restored"] \
        == "Ordner/Ziel.md"
    assert (await nn.trash_list(user))["items"] == []


@pytest.mark.asyncio
async def test_a_trash_route_cannot_reach_a_live_note(user, vault) -> None:
    from app.core.error import Error
    with pytest.raises(Error) as err:
        await nn.trash_delete("Ordner/Ziel.md", user)
    assert err.value.status_code == 400
    assert (vault / "Ordner/Ziel.md").exists()


@pytest.mark.asyncio
async def test_a_read_only_vault_says_so_rather_than_failing_oddly(user, vault) -> None:
    """That is the state the live vault is in until the switch is thrown, so it
    has to read as a sentence and not as a stack trace."""
    import errno
    from app.core.error import Error
    from app.notes.vault import write

    def refuse(*_a, **_k):
        raise OSError(errno.EROFS, "Read-only file system")

    original = write.write_text
    write.write_text = refuse
    try:
        with pytest.raises(Error) as err:
            await nn.save(nn.SaveIn(path="Ordner/Ziel.md", content="x\n"), user)
    finally:
        write.write_text = original
    assert err.value.status_code == 503
    assert err.value.key == "err.notes_read_only"
