"""The vault's own look: the CSS it carries, and how the tree is coloured.

The snippets are small pieces of CSS the vault keeps for itself — one of them
appends "overdue" and "due" markers to task dates. The renderer emits the
structure they hook onto; without serving the files the hooks find nothing and
the markers stay invisible.

Whole themes are deliberately not served: this interface has a house style of
its own, and a theme built for another program expects a page this one only
partly produces.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# A snippet is addressed by name from the page, so what a name may be is decided
# here and the name is never used to walk a filesystem.
SAFE_NAME = re.compile(r"^[\w .\-()]+$")
SNIPPET_DIR = "snippets"
APPEARANCE_FILE = "appearance.json"

# How the folder tree is coloured. The colours do not come from a plugin but
# from the vault's theme, switched on through a settings plugin that keeps its
# choices in a file like every other setting read here — so the same file
# decides it, and the tree looks the way it does wherever the vault is opened.
STYLE_BY_VALUE = {
    "anp-default-rainbow": "default",
    "anp-simple-rainbow-color-toggle": "simple",
    "anp-full-rainbow-color-toggle": "full",
}
# That plugin writes its keys with the theme's name in front of them.
THEME_PREFIX = "anuppuccin-theme-settings@@"


@dataclass
class Colours:
    # off · chevron only · title and chevron · filled background
    style: str = "off"
    # How strong the fill is. The theme's own default is a full one; the value
    # in its settings is only where the slider starts, and until somebody moves
    # it nothing is written and the stylesheet's value stands.
    opacity: float = 1.0
    # Colour the files inside a folder too, not only the folder.
    files: bool = False
    # Subfolders take their parent's colour rather than one of their own.
    inherit: bool = False

    def as_json(self) -> dict:
        return {"style": self.style, "opacity": self.opacity,
                "files": self.files, "inheritSubfolders": self.inherit}


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def colours(vault_root: Path, style_settings_dir: str, *,
            chosen_style: str = "", chosen_opacity: float = 0.0) -> Colours:
    """How the tree is coloured: what this person set, else what the vault says."""
    out = Colours()
    data = _read_json(vault_root / style_settings_dir / "data.json") if style_settings_dir else {}

    def at(key: str):
        return data.get(f"{THEME_PREFIX}{key}")

    # The settings plugin writes a key only once it has been touched, so an
    # absent one means "as the theme ships it": no colours.
    from_vault = STYLE_BY_VALUE.get(str(at("anp-alt-rainbow-style") or ""), "")
    out.style = chosen_style or from_vault or "off"

    opacity = at("anp-rainbow-folder-bg-opacity")
    if chosen_opacity > 0:
        out.opacity = chosen_opacity
    elif isinstance(opacity, (int, float)) and 0 < opacity <= 1:
        out.opacity = float(opacity)

    out.files = at("anp-rainbow-file-toggle") is True
    out.inherit = at("anp-rainbow-subfolder-color-toggle") is True
    return out


def info(vault_root: Path, config_dir: str, style_settings_dir: str, *,
         chosen_style: str = "", chosen_opacity: float = 0.0) -> dict:
    """Which snippets the vault has, which of them are on, and the colours."""
    names: list[str] = []
    if config_dir:
        folder = vault_root / config_dir / SNIPPET_DIR
        try:
            names = sorted(p.stem for p in folder.iterdir()
                           if p.is_file() and p.suffix.lower() == ".css")
        except OSError:
            names = []
    enabled_raw = _read_json(vault_root / config_dir / APPEARANCE_FILE).get(
        "enabledCssSnippets") if config_dir else None
    enabled = [s for s in enabled_raw if isinstance(s, str)] if isinstance(enabled_raw, list) else []
    return {"snippets": names,
            # Only what is actually there: a name left over from a snippet that
            # was deleted would be a stylesheet the page waits for in vain.
            "enabledSnippets": [s for s in enabled if s in names],
            "rainbow": colours(vault_root, style_settings_dir,
                               chosen_style=chosen_style,
                               chosen_opacity=chosen_opacity).as_json()}


def snippet(vault_root: Path, config_dir: str, name: str) -> str | None:
    """One snippet's CSS, or None — including for a name that may not be one."""
    if not config_dir or not SAFE_NAME.match(name or ""):
        return None
    try:
        return (vault_root / config_dir / SNIPPET_DIR / f"{name}.css").read_text(encoding="utf-8")
    except OSError:
        return None
