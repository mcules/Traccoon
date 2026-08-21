"""Placeholders in prompt jobs, so that a job can be a template instead of a copy.

The AI and tech news job carried its knowledge (sources, structure, time window) firmly in
the prompt. A second digest (security, radio, whatever) therefore meant copying the prompt
and editing it in three places. Now `jobs.args` carries the parameters and the prompt only `{{placeholders}}`.

Deliberately NO template engine: pure text replacement, no expression, no code. A prompt is
user input that afterwards goes to a model, and evaluation has no business there.

`args` is historically the argument list of the script jobs. Only an **object** counts as a
parameter set; a list stays a script argument untouched.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

# Rückfall, wenn niemand eine Zone nennt. Die Zone der Person steht in ihrem Profil und
# wird durchgereicht — „seit gestern 8 Uhr“ heißt in Tokio etwas anderes als hier.
STD_TZ = ZoneInfo("Europe/Berlin")

_PLATZHALTER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _as_text(value) -> str:
    """Parameter value to prompt text. Lists as an enumeration in one line, objects as JSON."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(x) for x in value if x is not None and x != "")
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def eingebaute_values(*, now: dt.datetime | None = None,
                     last_lauf: dt.datetime | None = None,
                     zone: ZoneInfo | None = None) -> dict[str, str]:
    """Time values practically every recurring job needs.

    `seit` is the last run; without it (first run, job was off) 24 hours back. That way a
    daily digest asks for "since the last time" instead of for a number that stands in the
    prompt and silently becomes wrong when the schedule is changed.
    """
    TZ = zone or STD_TZ
    now = (now or dt.datetime.now(tz=dt.timezone.utc)).astimezone(TZ)
    seit = (last_lauf.astimezone(TZ) if last_lauf else now - dt.timedelta(days=1))
    return {
        "today": now.strftime("%Y-%m-%d"),
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "since": seit.strftime("%Y-%m-%d %H:%M"),
        "window": f"{seit.strftime('%Y-%m-%d %H:%M')} bis {now.strftime('%Y-%m-%d %H:%M')} "
                  f"({TZ.key})",
    }


def parameter(args) -> dict:
    """The parameter set of a job; only an object counts, a list is a script argument."""
    return dict(args) if isinstance(args, dict) else {}


def rendere(prompt: str, args=None, *, now: dt.datetime | None = None,
            last_lauf: dt.datetime | None = None, zone: ZoneInfo | None = None) -> str:
    """Replace `{{name}}`: first the parameters of the job, then the built-in time values.

    An unknown placeholder stays VERBATIM. Emptying it silently would be the worse outcome:
    the assignment would lose a rule without a sound, instead of `{{quellen}}` standing
    visibly in the result and the error being noticed.
    """
    values = {**eingebaute_values(now=now, last_lauf=last_lauf, zone=zone)}
    values.update({k: _as_text(v) for k, v in parameter(args).items()})

    def ersetze(m: re.Match) -> str:
        name = m.group(1)
        return values[name] if name in values else m.group(0)

    return _PLATZHALTER.sub(ersetze, prompt or "")


def offene_platzhalter(prompt: str, args=None) -> list[str]:
    """Placeholders without a value, for the preview and check while creating a job."""
    known = set(eingebaute_values()) | set(parameter(args))
    return sorted({m.group(1) for m in _PLATZHALTER.finditer(prompt or "")} - known)
