"""The channel that tells the open windows a note changed.

Without it a second window keeps showing what it read minutes ago and, on the
next save, writes that back over the newer text. So what is tested here is not
that a message arrives, but that it arrives with what a window needs to decide
whether it has to re-read at all.
"""
from __future__ import annotations

import asyncio

import pytest

from app.notes import live


class Socket:
    """A socket that remembers, and one that can be told to fail."""

    def __init__(self, broken: bool = False) -> None:
        self.seen: list[dict] = []
        self.broken = broken

    async def send_json(self, message: dict) -> None:
        if self.broken:
            raise RuntimeError("gone")
        self.seen.append(message)


@pytest.fixture(autouse=True)
def clean():
    live._watching.clear()
    yield
    live._watching.clear()


def test_a_vault_nobody_watches_costs_nothing() -> None:
    live.announce("/vault", {"type": "fs", "path": "A.md"})
    assert live.listeners("/vault") == 0


@pytest.mark.asyncio
async def test_everybody_watching_the_same_vault_hears_it() -> None:
    """One channel per vault, not per person: the vault is what changes, and
    everybody looking at it wants to know."""
    a, b = Socket(), Socket()
    live.join("/vault", a)
    live.join("/vault", b)
    live.announce("/vault", {"type": "fs", "path": "A.md"})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert a.seen == b.seen == [{"type": "fs", "path": "A.md"}]


@pytest.mark.asyncio
async def test_another_vault_hears_nothing() -> None:
    mine, theirs = Socket(), Socket()
    live.join("/mine", mine)
    live.join("/theirs", theirs)
    live.announce("/mine", {"type": "fs", "path": "A.md"})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert theirs.seen == []


@pytest.mark.asyncio
async def test_a_socket_that_cannot_be_written_to_is_dropped() -> None:
    """Whatever it says about itself. Keeping it would mean trying a dead tab
    again on every single change."""
    dead, alive = Socket(broken=True), Socket()
    live.join("/vault", dead)
    live.join("/vault", alive)
    live.announce("/vault", {"type": "fs", "path": "A.md"})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert live.listeners("/vault") == 1
    assert alive.seen


def test_leaving_takes_the_vault_with_the_last_one() -> None:
    s = Socket()
    live.join("/vault", s)
    live.leave("/vault", s)
    assert "/vault" not in live._watching


def test_announcing_without_a_loop_does_not_raise() -> None:
    """The watcher can run in a process that has none — a test, a script. It
    must not turn a missing loop into a failed index update."""
    live.join("/vault", Socket())
    live.announce("/vault", {"type": "fs", "path": "A.md"})     # no loop here


@pytest.mark.asyncio
async def test_a_change_carries_the_version_it_made(tmp_path) -> None:
    """A window that made the change itself recognises its own hash and does not
    go and read the note again."""
    from app.notes import registry
    from app.notes.settings.options import Options
    from app.notes.vault.files import Vault, content_hash
    from app.notes.workspace import Workspace

    root = tmp_path / "vault"
    root.mkdir()
    (root / "A.md").write_text("eins\n", encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    watcher = Socket()
    live.join(str(root), watcher)

    change = registry._follow(str(root), ws)
    (root / "A.md").write_text("zwei\n", encoding="utf-8")
    change("A.md", False)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert watcher.seen == [{"type": "fs", "event": "change", "path": "A.md",
                             "hash": content_hash("zwei\n")}]


@pytest.mark.asyncio
async def test_a_deleted_note_says_so_without_a_version(tmp_path) -> None:
    from app.notes import registry
    from app.notes.settings.options import Options
    from app.notes.vault.files import Vault
    from app.notes.workspace import Workspace

    root = tmp_path / "vault"
    root.mkdir()
    (root / "A.md").write_text("eins\n", encoding="utf-8")
    ws = Workspace.open(Vault(root), Options())
    watcher = Socket()
    live.join(str(root), watcher)

    (root / "A.md").unlink()
    registry._follow(str(root), ws)("A.md", True)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert watcher.seen == [{"type": "fs", "event": "delete", "path": "A.md"}]
