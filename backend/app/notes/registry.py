"""Which vault belongs to whom, and the one workspace over each of them.

Two things ask for a vault now: the routes the browser talks to, and the tool
server the agents talk to. They must get the **same** workspace — two of them
over one folder would each hold their own idea of what is in it, and the two
would answer differently about the same note without anything saying so.

So the cache lives here rather than in either of them.

One vault per person. What is personal hangs off its owner in this house, the
way stores and mail accounts already do, and an account without one has no note
area — which is the state every account starts in.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import status

from ..config import settings
from ..core.error import Error
from ..models.user import User
from .dv import settings as note_settings
from .index.watch import watch
from .settings import options as vault_options
from .vault.files import Vault
from .workspace import Workspace

log = logging.getLogger("notes")

_workspaces: dict[str, Workspace] = {}
_watchers: dict[str, asyncio.Task] = {}
_settings_loaded = False


def vault_of(user: User) -> Vault:
    """The vault of the person asking, or a clear no."""
    rel = (user.vault_path or "").strip()
    if not rel:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_vault",
                    "This account has no note vault")
    root = Path(rel)
    if not root.is_dir():
        raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.notes_vault_missing",
                    "The note vault is not there: {path}", path=rel)
    return Vault(root, name=settings.notes_vault_name or root.name)


def _load_settings(v: Vault) -> None:
    """Take over how the notes were configured to behave, once.

    Held per process rather than per vault, which is right while there is one.
    When a second person gets a vault this moves into the database with the rest
    of the configuration — until then a second vault would silently inherit the
    first one's checkbox characters and attachment folder.
    """
    global _settings_loaded
    if _settings_loaded:
        return
    dirs = {"query": settings.notes_query_settings_dir,
            "tasks": settings.notes_task_settings_dir}
    note_settings.load(Path(v.root), {k: d for k, d in dirs.items() if d})
    vault_options.load(Path(v.root), settings.notes_config_dir,
                       trash=settings.notes_trash_dir,
                       delete_mode=settings.notes_delete_mode)
    _settings_loaded = True


def _recovery_root() -> Path | None:
    root = (settings.notes_recovery_dir or "").strip()
    return Path(root) if root else None


def workspace_of(user: User) -> Workspace:
    """The workspace of this person's vault, built and followed on first use.

    Reading six thousand notes on every request is not a cache decision, it is
    the difference between an answer and a timeout. Building on first use rather
    than at startup keeps a vault nobody opens out of the way; the watcher next
    to it is what stops the copy drifting away from the disk, which is the kind
    of failure that says nothing while it happens.
    """
    v = vault_of(user)
    key = str(v.root)
    ws = _workspaces.get(key)
    if ws is None:
        _load_settings(v)
        ws = Workspace.open(v, vault_options.options(), _recovery_root())
        _workspaces[key] = ws
    task = _watchers.get(key)
    if task is None or task.done():
        try:
            _watchers[key] = asyncio.create_task(
                watch(v, lambda rel, gone, _w=ws: _w.touch(rel, removed=gone)))
        except RuntimeError:
            # No loop running: a test, or a script importing this. The indexes
            # are still correct, they just will not follow the disk, and saying
            # so in the log is better than refusing to answer.
            log.warning("notes: no event loop, the index will not follow %s", key)
    return ws


def forget_all() -> None:
    """Drop everything cached. For tests, and for a vault that moved."""
    global _settings_loaded
    for task in _watchers.values():
        task.cancel()
    _watchers.clear()
    _workspaces.clear()
    _settings_loaded = False
