"""Ticking a task off, as one transformation of the task's own line.

Two callers share it, and they must agree: the route that edits a file on disk
(a result pointing at another note) and the preview the editor asks for when the
task lives in the note that is open — there the change has to go through the
editor's own document, or its buffer would write the tick away again on the next
save.
"""
from __future__ import annotations

import datetime as dt
import re

from . import recurrence, settings as dv_settings

TASK_LINE = re.compile(r"^(\s*[-*+]\s+\[)(.)(\]\s?)(.*)$")
DONE_STAMP = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")
CANCELLED_STAMP = re.compile(r"\s*❌\s*\d{4}-\d{2}-\d{2}")


def is_task_line(line: str) -> bool:
    return bool(TASK_LINE.match(line))


def text_of(line: str) -> str | None:
    """What the task says after its box, for comparing against a stale result."""
    found = TASK_LINE.match(line)
    return found.group(4) if found else None


def toggled(line: str, checked: bool, mode: str = "tasks",
            today: dt.date | None = None) -> list[str] | None:
    """The line or lines that replace this one, in the order they go in.

    `mode` decides what ticking means, exactly as the two plugins do: in a task
    block it writes a done date and brings the next instance of a recurring task
    with it, while in a query result it only flips the box — that language's own
    completion tracking is off.
    """
    found = TASK_LINE.match(line)
    if not found:
        return None
    today = today or dt.date.today()
    conf = dv_settings.task_settings()
    text = found.group(4)
    following: str | None = None

    if checked:
        if mode == "tasks" and conf.get("setDoneDate") and "✅" not in text:
            text = f"{text.rstrip()} ✅ {today.isoformat()}"
        if mode == "tasks":
            following = recurrence.next_instance(found.group(4), today)
    else:
        text = CANCELLED_STAMP.sub("", DONE_STAMP.sub("", text)).rstrip()

    done = f"{found.group(1)}{'x' if checked else ' '}{found.group(3)}{text}"
    if following is None:
        return [done]
    fresh = f"{found.group(1)} {found.group(3)}{following}"
    # Where the new instance goes is the plugin's setting, not a choice here.
    return [done, fresh] if conf.get("recurrenceOnNextLine") else [fresh, done]
