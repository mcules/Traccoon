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
    # Where the daily notes live and what they are called. Read from the vault
    # rather than set here: the folder is full of notes with those names already.
    daily_folder: str = ""
    daily_format: str = "YYYY-MM-DD"
    # An appointment whose title contains `match` gets the note at `template`
    # written underneath it. The club evening brings its running order, the
    # course its structure.
    calendar_templates: list = field(default_factory=list)
    # Where the templates are kept, and which folder gets which form. Both come
    # from the vault: a new note in the people folder should get the person form
    # here exactly as it does anywhere else the vault is opened.
    templates_folder: str = ""
    template_date_format: str = "YYYY-MM-DD"
    template_time_format: str = "HH:mm"
    folder_templates: list = field(default_factory=list)


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
    "calendar_templates": list,
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
    if kind is list:
        if isinstance(raw, list):
            # Only the pairs that are complete. A half-written mapping would
            # otherwise write an empty agenda under an appointment.
            setattr(out, name, [
                {"match": str(x["match"]), "template": str(x["template"])}
                for x in raw
                if isinstance(x, dict) and x.get("match") and x.get("template")])
        return
    if isinstance(raw, str) and raw.strip():
        setattr(out, name, raw.strip())


def defaults() -> dict:
    """The preferences a person has before they set any. What the settings page
    shows on a fresh account, and what a new vault behaves like."""
    base = Options()
    return {name: getattr(base, name) for name in PREF_FIELDS}


def from_user(prefs: dict | None, vault_root: Path, config_dir: str,
              template_settings_dir: str = "") -> Options:
    """The options of one person's vault: their preferences, plus the ones the
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

        # Where the daily notes live. The vault decides this too — the folder is
        # already full of notes with those names, and a second opinion here would
        # write tomorrow's note somewhere nobody looks.
        try:
            with (vault_root / config_dir / "daily-notes.json").open(encoding="utf-8") as fh:
                daily = json.load(fh)
        except (OSError, ValueError):
            daily = {}
        if isinstance(daily.get("folder"), str):
            out.daily_folder = daily["folder"]
        if isinstance(daily.get("format"), str) and daily["format"]:
            out.daily_format = daily["format"]

        # Where the templates are kept.
        try:
            with (vault_root / config_dir / "templates.json").open(encoding="utf-8") as fh:
                templates = json.load(fh)
        except (OSError, ValueError):
            templates = {}
        if isinstance(templates.get("folder"), str):
            out.templates_folder = templates["folder"]
        if isinstance(templates.get("dateFormat"), str) and templates["dateFormat"]:
            out.template_date_format = templates["dateFormat"]
        if isinstance(templates.get("timeFormat"), str) and templates["timeFormat"]:
            out.template_time_format = templates["timeFormat"]

    # Which folder gets which form. A mapping that points at a note which is no
    # longer there stays in the list: it is the vault's statement, and dropping
    # entries here would quietly disagree with what the vault says about itself.
    if template_settings_dir:
        try:
            with (vault_root / template_settings_dir / "data.json").open(encoding="utf-8") as fh:
                raw_templater = json.load(fh)
        except (OSError, ValueError):
            raw_templater = {}
        found = raw_templater.get("folder_templates")
        if isinstance(found, list):
            out.folder_templates = [
                {"folder": str(f.get("folder") or ""), "template": str(f.get("template") or "")}
                for f in found
                if isinstance(f, dict) and f.get("folder") and f.get("template")]
    return out
