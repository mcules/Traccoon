"""How the vault behaves when something is written to it.

Four decisions that have to match what the vault is already used to, because a
folder of files has more than one writer: the file sync carries what was written
on a phone, agents write into it, and until recently a desktop program did too.

  * **Where a deleted note goes.** Into a folder in the vault, not away. That
    folder starts with a dot, so nothing indexes or searches it.
  * **Whether deleting means deleting.** It does not, by default.
  * **Where an attachment lands.** Dropping an image into a note must put it
    where the vault already keeps its attachments, or the vault ends up with two
    such folders and the sync spreads the mistake to every device.
  * **Whether links follow a rename.** They do. A wikilink to a note that has
    moved looks exactly like one to a note that never existed, so a rename
    without this leaves dead links behind and says nothing.

The last two are the vault's own settings and are read from it. The first two
are this program's and have defaults; they move into the database with the rest
of the configuration.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("notes.options")


@dataclass
class Options:
    # A folder in the vault, hidden, so what is in it is out of every index.
    trash: str = ".trash"
    delete_mode: str = "trash"          # trash | permanent
    # `/` is the vault root, `.` the folder of the note the file is put into,
    # `./sub` a folder beside that note, anything else a folder by name.
    attachment_folder: str = "/"
    follow_links_on_rename: bool = True


_options = Options()


def options() -> Options:
    return _options


def load(vault_root: Path, config_dir: str, *,
         trash: str = "", delete_mode: str = "") -> None:
    """Take the vault's own two settings over, and this program's two from config.

    An absent file is not a fault: a new vault has none, and the defaults are
    what a new vault should behave like.
    """
    global _options
    out = Options()
    if trash:
        out.trash = trash
    if delete_mode in ("trash", "permanent"):
        out.delete_mode = delete_mode
    if config_dir:
        try:
            with (vault_root / config_dir / "app.json").open(encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw.get("attachmentFolderPath"), str):
            out.attachment_folder = raw["attachmentFolderPath"]
        if isinstance(raw.get("alwaysUpdateLinks"), bool):
            out.follow_links_on_rename = raw["alwaysUpdateLinks"]
    _options = out
    log.info("notes: attachments go to %r, links follow a rename: %s",
             out.attachment_folder, out.follow_links_on_rename)


def reset() -> None:
    """Back to the defaults. For tests, and for a vault that configures nothing."""
    global _options
    _options = Options()
