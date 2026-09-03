"""The properties notes carry, and what type each one is.

Two different questions live here, and they are different on purpose. What
*types* somebody has assigned sits in a file in the vault and is read and
written as it stands, so that a type set here is the same type anywhere else the
vault is opened. What types are actually *in use* is counted from the notes
themselves, because that is the answer to "what can I filter on".
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

# These are always lists, whatever a single one of them happens to look like.
CORE_LISTS = {"tags", "aliases", "cssclasses"}
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TYPES_FILE = "types.json"


def type_of(key: str, value: Any) -> str:
    """What kind of property this is, judged by what stands there."""
    if key in CORE_LISTS:
        return "list"
    if isinstance(value, list):
        return "list"
    # A boolean is a number in Python's eyes, so it has to be asked about first.
    if isinstance(value, bool):
        return "checkbox"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dt.datetime):
        return "datetime"
    if isinstance(value, dt.date):
        return "date"
    if isinstance(value, str):
        if DATETIME.match(value):
            return "datetime"
        if DATE.match(value):
            return "date"
    return "text"


def in_use(frontmatters: list[dict]) -> list[dict]:
    """Every property the notes carry, with the type most of them use.

    Sorted by how often it appears, because a list of properties is read to
    find one, and the ones somebody uses everywhere are the ones they mean. The
    three built-in lists are offered even when nothing uses them yet.
    """
    count: dict[str, int] = {k: 0 for k in CORE_LISTS}
    kinds: dict[str, dict[str, int]] = {k: {"list": 1} for k in CORE_LISTS}

    for fm in frontmatters:
        for key, value in (fm or {}).items():
            count[key] = count.get(key, 0) + 1
            per = kinds.setdefault(key, {})
            found = type_of(key, value)
            per[found] = per.get(found, 0) + 1

    out = []
    for key, times in count.items():
        best, most = "text", 0
        for name, n in kinds.get(key, {}).items():
            if n > most:
                best, most = name, n
        out.append({"key": key, "type": best, "count": times})
    # Most used first; equal counts in the order somebody would look them up.
    out.sort(key=lambda p: (-p["count"], p["key"]))
    return out


def _file(vault_root: Path, config_dir: str) -> Path:
    return vault_root / config_dir / TYPES_FILE


def assigned(vault_root: Path, config_dir: str) -> dict[str, str]:
    """The types set in the vault. A vault that has none simply has none."""
    if not config_dir:
        return {}
    try:
        with _file(vault_root, config_dir).open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    found = data.get("types") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def assign(vault_root: Path, config_dir: str, key: str, kind: str) -> dict[str, str]:
    """Set one type, keeping whatever else the file says.

    The file belongs to the vault and not to this program: something else wrote
    it and will read it again, so everything in it that is not this one key is
    written back exactly as it was found.
    """
    if not config_dir:
        return {}
    path = _file(vault_root, config_dir)
    data: dict = {"types": {}}
    try:
        with path.open(encoding="utf-8") as fh:
            found = json.load(fh)
        if isinstance(found, dict):
            data = dict(found)
            data["types"] = dict(found.get("types") or {})
    except (OSError, ValueError):
        pass
    data["types"][key] = kind
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data["types"]
