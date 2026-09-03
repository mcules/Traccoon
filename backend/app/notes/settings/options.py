"""How a person's note area behaves when something is written to it.

Six decisions, and each of them has to match what the vault is already used to,
because a folder of files has more than one writer: the file sync carries what
was written on a phone, agents write into it, jobs write into it.

They come from two places and that is deliberate:

  * **From the person**, held in `users.notes_prefs`. Where a deleted note goes,
    whether deleting means deleting, how a note opens, how forgiving the search
    is. Those are preferences, they belong to whoever reads the notes, and they
    are set in that person's settings like everything else personal here.
  * **From the vault itself**, read from its own configuration folder: where an
    attachment belongs, and whether links follow a note that is renamed. Those
    two are properties of the folder rather than of the reader — the vault is
    already full of attachments in one particular place, and putting the next
    one somewhere else would be this program disagreeing with the notes.

Nothing here is global. An earlier version held one set for the whole process,
which was right while there was one vault and wrong the moment there were two:
the second would have silently inherited the first one's trash folder.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("notes.options")

VIEWS = ("live", "source", "reading")
DELETE_MODES = ("trash", "permanent")
# Empty means the vault's own colouring decides; the rest override it.
FOLDER_COLOURS = ("", "off", "default", "simple", "full")


@dataclass
class Options:
    # A folder in the vault, hidden, so what is in it is out of every index.
    trash: str = ".trash"
    delete_mode: str = "trash"          # trash | permanent
    # `/` is the vault root, `.` the folder of the note the file is put into,
    # `./sub` a folder beside that note, anything else a folder by name.
    attachment_folder: str = "/"
    follow_links_on_rename: bool = True
    # How a note opens: as it will read, as it is written, or as plain source.
    default_view: str = "live"
    # How far the search reaches past a typo, and whether it matches a word that
    # has only been started. Both are what a person expects while typing, so
    # both belong to the person.
    search_fuzzy: float = 0.2
    search_prefix: bool = True
    folder_colours: str = ""
    folder_colour_opacity: float = 0.0


# What a person may set, and what a value has to look like to be taken. Anything
# else is left at the default rather than refused: a preference that arrived
# malformed must not stop somebody reading their notes.
PREF_FIELDS: dict[str, Any] = {
    "trash": str,
    "delete_mode": DELETE_MODES,
    "default_view": VIEWS,
    "search_fuzzy": float,
    "search_prefix": bool,
    "folder_colours": FOLDER_COLOURS,
    "folder_colour_opacity": float,
}


def _take(out: Options, name: str, raw: Any) -> None:
    kind = PREF_FIELDS[name]
    if isinstance(kind, tuple):
        if raw in kind:
            setattr(out, name, raw)
        return
    if kind is bool:
        if isinstance(raw, bool):
            setattr(out, name, raw)
        return
    if kind is float:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            setattr(out, name, float(raw))
        return
    if isinstance(raw, str) and raw.strip():
        setattr(out, name, raw.strip())


def defaults() -> dict:
    """The preferences a person has before they set any. What the settings page
    shows on a fresh account, and what a new vault behaves like."""
    base = Options()
    return {name: getattr(base, name) for name in PREF_FIELDS}


def from_user(prefs: dict | None, vault_root: Path, config_dir: str) -> Options:
    """The options of one person's vault: their preferences, plus the two the
    vault decides for itself."""
    out = Options()
    for name, raw in (prefs or {}).items():
        if name in PREF_FIELDS:
            _take(out, name, raw)

    if config_dir:
        # An absent file is not a fault: a new vault has none, and the defaults
        # are what a new vault should behave like.
        try:
            with (vault_root / config_dir / "app.json").open(encoding="utf-8") as fh:
                raw_config = json.load(fh)
        except (OSError, ValueError):
            raw_config = {}
        if isinstance(raw_config.get("attachmentFolderPath"), str):
            out.attachment_folder = raw_config["attachmentFolderPath"]
        if isinstance(raw_config.get("alwaysUpdateLinks"), bool):
            out.follow_links_on_rename = raw_config["alwaysUpdateLinks"]
    return out
