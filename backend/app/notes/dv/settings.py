"""How the notes were configured to behave, read once rather than guessed.

Two of the languages below have settings that live in the vault, not in this
program: which checkbox characters exist and what each of them means, how a
missing value is written, what a date looks like by default. Guessing them would
be visible immediately — a task marked `[/]` would read as done rather than as
started, and every query about what is still open would answer wrong.

Where those settings sit is **not** written down here. The folder belongs to a
program that is being switched off, and its name is not a thing this repository
carries; it arrives as configuration, and when it is not configured the built-in
defaults apply. That is also the state every new vault starts in, since a fresh
vault has no such folder at all.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("notes.settings")

# The two statuses that exist without any configuration at all.
CORE_STATUSES: list[dict] = [
    {"symbol": " ", "name": "Todo", "nextStatusSymbol": "x", "type": "TODO"},
    {"symbol": "x", "name": "Done", "nextStatusSymbol": " ", "type": "DONE"},
]

QUERY_DEFAULTS: dict[str, Any] = {
    "renderNullAs": "\\-",
    "taskCompletionTracking": False,
    "taskCompletionUseEmojiShorthand": False,
    "taskCompletionText": "completion",
    "taskCompletionDateFormat": "yyyy-MM-dd",
    "recursiveSubTaskCompletion": False,
    "warnOnEmptyResult": True,
    "defaultDateFormat": "MMMM dd, yyyy",
    "defaultDateTimeFormat": "h:mm a - MMMM dd, yyyy",
    "tableIdColumnName": "File",
    "tableGroupColumnName": "Group",
    "showResultCount": True,
    "inlineQueryPrefix": "=",
    "inlineJsQueryPrefix": "$=",
    "enableInlineDataview": True,
    "enableDataviewJs": True,
    "enableInlineDataviewJs": True,
    "prettyRenderInlineFields": True,
    "prettyRenderInlineFieldsInLivePreview": True,
    "dataviewJsKeyword": "dataviewjs",
}

TASK_DEFAULTS: dict[str, Any] = {
    "globalQuery": "",
    "globalFilter": "",
    "removeGlobalFilter": False,
    "setCreatedDate": False,
    "setDoneDate": True,
    "setCancelledDate": True,
    "recurrenceOnNextLine": False,
    "removeScheduledDateOnRecurrence": False,
    "useFilenameAsScheduledDate": False,
}

_query: dict[str, Any] = dict(QUERY_DEFAULTS)
_tasks: dict[str, Any] = dict(TASK_DEFAULTS)
_statuses: list[dict] = list(CORE_STATUSES)


def query_settings() -> dict[str, Any]:
    return _query


def task_settings() -> dict[str, Any]:
    return _tasks


def statuses() -> list[dict]:
    return _statuses


def status_for(symbol: str) -> dict:
    """What a checkbox character means. Unknown ones are done unless empty.

    Falling back this way rather than refusing keeps a note written with some
    other set of characters readable: `[?]` is at least closed, which is nearer
    the truth than treating it as open.
    """
    for s in _statuses:
        if s["symbol"] == symbol:
            return s
    if symbol == " ":
        return {"symbol": " ", "name": "Todo", "nextStatusSymbol": "x", "type": "TODO"}
    return {"symbol": symbol, "name": "Done", "nextStatusSymbol": " ", "type": "DONE"}


def _pick(defaults: dict[str, Any], raw: dict | None) -> dict[str, Any]:
    """Take over only what is there and of the same type as the default.

    A settings file written by a newer version of something else will carry keys
    this does not know and may carry a key with a different type. Neither is a
    reason to fall back to nothing.
    """
    out = dict(defaults)
    if not raw:
        return out
    for key, default in defaults.items():
        if key in raw and type(raw[key]) is type(default):
            out[key] = raw[key]
    return out


def _read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load(vault_root: Path, dirs: dict[str, str]) -> None:
    """Take the settings over from the vault.

    `dirs` maps a role to a path inside the vault: `query` for the settings of
    the query language, `tasks` for those of the task filters. Both are optional
    and an absent one leaves the defaults in place, which is the right answer for
    a vault that was never configured by anything else.
    """
    global _query, _tasks, _statuses
    query_dir = dirs.get("query")
    if query_dir:
        _query = _pick(QUERY_DEFAULTS, _read_json(vault_root / query_dir / "data.json"))

    tasks_dir = dirs.get("tasks")
    if not tasks_dir:
        return
    raw = _read_json(vault_root / tasks_dir / "data.json")
    _tasks = _pick(TASK_DEFAULTS, raw)
    status_settings = (raw or {}).get("statusSettings") or {}
    core = status_settings.get("coreStatuses")
    custom = status_settings.get("customStatuses")
    found = [s for s in (list(core or CORE_STATUSES) + list(custom or []))
             if isinstance(s, dict) and isinstance(s.get("symbol"), str)]
    _statuses = found or list(CORE_STATUSES)
    log.info("notes: %d task statuses taken over", len(_statuses))


def reset() -> None:
    """Back to the built-in defaults. For tests, and for a vault with none."""
    global _query, _tasks, _statuses
    _query = dict(QUERY_DEFAULTS)
    _tasks = dict(TASK_DEFAULTS)
    _statuses = list(CORE_STATUSES)
