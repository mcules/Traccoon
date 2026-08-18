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

TZ = ZoneInfo("Europe/Berlin")

_PLATZHALTER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _als_text(wert) -> str:
    """Parameter value to prompt text. Lists as an enumeration in one line, objects as JSON."""
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, (list, tuple)):
        return ", ".join(_als_text(x) for x in wert if x is not None and x != "")
    if isinstance(wert, dict):
        return json.dumps(wert, ensure_ascii=False)
    return str(wert)


def eingebaute_werte(*, jetzt: dt.datetime | None = None,
                     letzter_lauf: dt.datetime | None = None) -> dict[str, str]:
    """Time values practically every recurring job needs.

    `seit` is the last run; without it (first run, job was off) 24 hours back. That way a
    daily digest asks for "since the last time" instead of for a number that stands in the
    prompt and silently becomes wrong when the schedule is changed.
    """
    jetzt = (jetzt or dt.datetime.now(tz=dt.timezone.utc)).astimezone(TZ)
    seit = (letzter_lauf.astimezone(TZ) if letzter_lauf else jetzt - dt.timedelta(days=1))
    return {
        "heute": jetzt.strftime("%Y-%m-%d"),
        "jetzt": jetzt.strftime("%Y-%m-%d %H:%M"),
        "seit": seit.strftime("%Y-%m-%d %H:%M"),
        "zeitfenster": f"{seit.strftime('%Y-%m-%d %H:%M')} bis {jetzt.strftime('%Y-%m-%d %H:%M')} "
                       f"({TZ.key})",
    }


def parameter(args) -> dict:
    """The parameter set of a job; only an object counts, a list is a script argument."""
    return dict(args) if isinstance(args, dict) else {}


def rendere(prompt: str, args=None, *, jetzt: dt.datetime | None = None,
            letzter_lauf: dt.datetime | None = None) -> str:
    """Replace `{{name}}`: first the parameters of the job, then the built-in time values.

    An unknown placeholder stays VERBATIM. Emptying it silently would be the worse outcome:
    the assignment would lose a rule without a sound, instead of `{{quellen}}` standing
    visibly in the result and the error being noticed.
    """
    werte = {**eingebaute_werte(jetzt=jetzt, letzter_lauf=letzter_lauf)}
    werte.update({k: _als_text(v) for k, v in parameter(args).items()})

    def ersetze(m: re.Match) -> str:
        name = m.group(1)
        return werte[name] if name in werte else m.group(0)

    return _PLATZHALTER.sub(ersetze, prompt or "")


def offene_platzhalter(prompt: str, args=None) -> list[str]:
    """Placeholders without a value, for the preview and check while creating a job."""
    bekannt = set(eingebaute_werte()) | set(parameter(args))
    return sorted({m.group(1) for m in _PLATZHALTER.finditer(prompt or "")} - bekannt)
