"""The date tokens the vault's own settings speak.

Not a date library: a handful of tokens appear in this vault — `YYYY/MM/YYYY-MM-DD`
for the path of a daily note, `YYYY-MM-DD` in templates, the odd `dddd` — and a
full one would cost more than it explains.

**This is a different dialect from the one in `dv/values.py`**, which the query
language uses (`yyyy`, `dd`, Luxon style). The two must not be mixed: `DD` means
the day here and nothing there, `dd` the other way round. They are kept apart on
purpose rather than unified, because each follows something outside this code.

The month and weekday names are German because that is the language the notes
are written in — they end up inside a note, not in the interface.
"""
from __future__ import annotations

import datetime as dt
import re

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]
DAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]

# Anything in brackets is taken literally; the rest are tokens, longest first so
# `YYYY` is not read as two `YY`.
TOKEN = re.compile(r"\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|DDDD|dddd|ddd|DD|D"
                   r"|HH|H|mm|m|ss|s|ww|w|A|a")
ESCAPE = re.compile(r"[.*+?^${}()|\[\]\\]")


def _pad(n: int, width: int = 2) -> str:
    return str(n).rjust(width, "0")


def _weekday(d: dt.date) -> int:
    """Sunday is zero, the way that dialect counts. Python starts at Monday."""
    return (d.weekday() + 1) % 7


def format_date(when: dt.datetime, pattern: str) -> str:
    def one(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)
        token = m.group(0)
        table = {
            "YYYY": str(when.year),
            "YY": _pad(when.year % 100),
            "MMMM": MONTHS[when.month - 1],
            "MMM": MONTHS[when.month - 1][:3],
            "MM": _pad(when.month),
            "M": str(when.month),
            "DD": _pad(when.day),
            "D": str(when.day),
            "dddd": DAYS[_weekday(when)],
            "ddd": DAYS[_weekday(when)][:2],
            "HH": _pad(when.hour),
            "H": str(when.hour),
            "mm": _pad(when.minute),
            "m": str(when.minute),
            "ss": _pad(when.second),
            "s": str(when.second),
            "ww": _pad(when.isocalendar().week),
            "w": str(when.isocalendar().week),
            "A": "AM" if when.hour < 12 else "PM",
            "a": "am" if when.hour < 12 else "pm",
        }
        return table.get(token, token)

    return TOKEN.sub(one, pattern)


def parse_date(text: str, pattern: str) -> dt.datetime | None:
    """Read a date back out of text written with `pattern`.

    What turns a daily note's own title into the day it stands for. Names and
    the like are matched but not interpreted: a weekday in the title says
    nothing the year, month and day do not already say.
    """
    order: list[str] = []
    parts: list[str] = []
    last = 0
    for m in TOKEN.finditer(pattern):
        parts.append(ESCAPE.sub(r"\\\g<0>", pattern[last:m.start()]))
        last = m.end()
        if m.group(1) is not None:
            parts.append(ESCAPE.sub(r"\\\g<0>", m.group(1)))
            continue
        token = m.group(0)
        if token == "YYYY":
            parts.append(r"(\d{4})")
            order.append("Y")
        elif token in ("MM", "M"):
            parts.append(r"(\d{1,2})")
            order.append("M")
        elif token in ("DD", "D"):
            parts.append(r"(\d{1,2})")
            order.append("D")
        elif token in ("HH", "H"):
            parts.append(r"(\d{1,2})")
            order.append("h")
        elif token in ("mm", "m"):
            parts.append(r"(\d{1,2})")
            order.append("i")
        else:
            parts.append(".+?")
    parts.append(ESCAPE.sub(r"\\\g<0>", pattern[last:]))

    found = re.match(f"^{''.join(parts)}$", text.strip())
    if not found:
        return None
    values = {key: int(found.group(i + 1)) for i, key in enumerate(order)}
    if not {"Y", "M", "D"} <= values.keys():
        return None
    try:
        return dt.datetime(values["Y"], values["M"], values["D"],
                           values.get("h", 0), values.get("i", 0))
    except ValueError:
        return None


def add_days(when: dt.datetime, days: int) -> dt.datetime:
    return when + dt.timedelta(days=days)
