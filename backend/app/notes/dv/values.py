"""What a value is to the query languages: links, dates, spans of time, tasks.

The three little languages of the notes all stand on this. A field is not just
text: `[[Meeting]]` is a link that can be followed, `2026-09-02` is a day that
can be compared and added to, `2 weeks` is a span. Whether two of them are equal
and which of them comes first is decided here, once, because a `WHERE` in one
language and a filter in another must agree or the same note appears in one
answer and not in the other.

Values are plain dictionaries with a `kind`, exactly the shape the interface
already receives, so nothing has to be converted on the way out. The dataclass
that would be tidier here would have to be unwrapped again at every boundary,
and each unwrapping is a place where a field can quietly go missing.

Translated from the implementation the notes were written against. The places
where the two languages disagree are in `js.py`, named; this module uses them
and does not re-decide them.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .js import collate, js_num_str

Value = Any

# ------------------------------------------------------------------- shapes

def is_link(v: Any) -> bool:
    return isinstance(v, dict) and v.get("kind") == "link"


def is_date(v: Any) -> bool:
    return isinstance(v, dict) and v.get("kind") == "date"


def is_duration(v: Any) -> bool:
    return isinstance(v, dict) and v.get("kind") == "duration"


def is_task(v: Any) -> bool:
    return isinstance(v, dict) and v.get("kind") == "task"


def is_plain_object(v: Any) -> bool:
    return isinstance(v, dict) and "kind" not in v


def is_number(v: Any) -> bool:
    """Booleans are numbers to Python and not to that language, so they are out."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# -------------------------------------------------------------------- links

WIKILINK_FULL = re.compile(r"^!?\[\[([^\]]+)\]\]$")


def make_link(raw: str, embed: bool = False) -> dict:
    """A link as written, taken apart into what it points at and what it shows."""
    target = raw.strip()
    display = None
    subpath = None
    bar = target.find("|")
    if bar >= 0:
        display = target[bar + 1:].strip()
        target = target[:bar].strip()
    hash_at = target.find("#")
    if hash_at > 0:
        subpath = target[hash_at + 1:]
        target = target[:hash_at]
    link = {"kind": "link", "target": target, "path": target}
    # A display text or a heading that is not there is left out rather than set
    # to nothing. On the other side those fields are simply absent, and an
    # explicit null would travel out to the interface as a value somebody wrote.
    if display is not None:
        link["display"] = display
    if subpath is not None:
        link["subpath"] = subpath
    link["embed"] = embed
    return link


def link_from_string(s: str) -> dict | None:
    """A string that is *entirely* a link, else nothing.

    Deliberately anchored: a sentence that mentions a link is prose, and turning
    it into a link would make it point somewhere when it was only talking.
    """
    t = s.strip()
    m = WIKILINK_FULL.match(t)
    if not m:
        return None
    return make_link(m.group(1), t.startswith("!"))


def link_key_of(link: dict) -> str:
    path = link.get("path") or link.get("target") or ""
    return re.sub(r"\.(md|markdown)$", "", path, flags=re.I).lower()


def link_base_key(link: dict) -> str:
    """The bare name. `[[Note]]` finds a note wherever it lies, so the name is
    what two links are compared by when one of them carries no folders."""
    k = link_key_of(link)
    return k[k.rfind("/") + 1:] if "/" in k else k


def link_to_markdown(link: dict) -> str:
    sub = f"#{link['subpath']}" if link.get("subpath") else ""
    disp = f"|{link['display']}" if link.get("display") else ""
    return f"{'!' if link.get('embed') else ''}[[{link['target']}{sub}{disp}]]"


# -------------------------------------------------------------------- dates

DATE_ONLY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?")


def make_date(ts: float, has_time: bool) -> dict:
    return {"kind": "date", "ts": ts, "hasTime": has_time}


def _local_ms(*parts: int) -> float:
    """A wall-clock time turned into a moment, in the zone this runs in.

    That language's `new Date(y, m, d)` is local, not UTC, and so is this. The
    zone therefore decides the answer, which is why both sides are set to the
    same one and why a test that cares sets it rather than assuming it.
    """
    return datetime(*parts).timestamp() * 1000.0


def date_from_string(s: str) -> dict | None:
    t = s.strip()
    m = DATE_TIME.match(t)
    if m:
        ts = _local_ms(int(m[1]), int(m[2]), int(m[3]),
                       int(m[4]), int(m[5]), int(m[6]) if m[6] else 0)
        return make_date(ts, True)
    m = DATE_ONLY.match(t)
    if m:
        return make_date(_local_ms(int(m[1]), int(m[2]), int(m[3])), False)
    return None


def date_from_ms(ms: float, has_time: bool = True) -> dict:
    return make_date(float(ms), has_time)


def as_local(d: dict) -> datetime:
    return datetime.fromtimestamp(d["ts"] / 1000.0)


def start_of_day(x: datetime) -> datetime:
    return datetime(x.year, x.month, x.day)


def pad(n: int, width: int = 2) -> str:
    return str(abs(int(n))).rjust(width, "0")


def date_to_iso(d: dict) -> str:
    x = as_local(d)
    day = f"{x.year}-{pad(x.month)}-{pad(x.day)}"
    if not d.get("hasTime"):
        return day
    return f"{day}T{pad(x.hour)}:{pad(x.minute)}:{pad(x.second)}"


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

_TOKENS = re.compile(r"yyyy|yy|MMMM|MMM|MM|M|dd|d|EEEE|EEE|HH|H|mm|ss")


def format_date(d: dict, fmt: str) -> str:
    """The subset of format tokens the notes actually use.

    Weekday numbering follows that language's, where Sunday is zero. Python's
    `weekday()` starts at Monday, which would shift every weekday name by one.
    """
    x = as_local(d)
    weekday = (x.weekday() + 1) % 7
    table = {
        "yyyy": str(x.year), "yy": pad(x.year % 100),
        "MMMM": MONTHS[x.month - 1], "MMM": MONTHS[x.month - 1][:3],
        "MM": pad(x.month), "M": str(x.month),
        "dd": pad(x.day), "d": str(x.day),
        "EEEE": DAYS[weekday], "EEE": DAYS[weekday][:3],
        "HH": pad(x.hour), "H": str(x.hour),
        "mm": pad(x.minute), "ss": pad(x.second),
    }
    return _TOKENS.sub(lambda m: table.get(m.group(0), m.group(0)), fmt)


# ---------------------------------------------------------------- durations

_MS = 1000
_DUR_UNITS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"^(years?|yrs?|y)$", re.I), 365 * 24 * 3600 * _MS),
    (re.compile(r"^(months?|mos?)$", re.I), 30 * 24 * 3600 * _MS),
    (re.compile(r"^(weeks?|wks?|w)$", re.I), 7 * 24 * 3600 * _MS),
    (re.compile(r"^(days?|d)$", re.I), 24 * 3600 * _MS),
    (re.compile(r"^(hours?|hrs?|h)$", re.I), 3600 * _MS),
    (re.compile(r"^(minutes?|mins?|m)$", re.I), 60 * _MS),
    (re.compile(r"^(seconds?|secs?|s)$", re.I), _MS),
]
_DUR_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def duration_from_string(s: str) -> dict | None:
    ms = 0.0
    seen = False
    for m in _DUR_PART.finditer(s):
        unit = next((size for pattern, size in _DUR_UNITS if pattern.match(m[2])), None)
        if unit is None:
            return None
        ms += float(m[1]) * unit
        seen = True
    return {"kind": "duration", "ms": ms} if seen else None


_DUR_NAMES: list[tuple[str, float]] = [
    ("years", 365 * 24 * 3600 * _MS), ("months", 30 * 24 * 3600 * _MS),
    ("days", 24 * 3600 * _MS), ("hours", 3600 * _MS),
    ("minutes", 60 * _MS), ("seconds", _MS),
]


def duration_to_string(d: dict) -> str:
    """The two largest units, which is how a span reads to a person."""
    ms = abs(d["ms"])
    parts: list[str] = []
    for name, size in _DUR_NAMES:
        n = int(ms // size)
        if n > 0:
            parts.append(f"{n} {name[:-1] if n == 1 else name}")
            ms -= n * size
    if not parts:
        return "0 seconds"
    return ("-" if d["ms"] < 0 else "") + ", ".join(parts[:2])


# ------------------------------------------------------------------ writing

def to_markdown(v: Value) -> str:
    """A value as the markdown that would go in a table cell."""
    if v is None:
        return "\\-"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if is_number(v):
        return js_num_str(v)
    if is_link(v):
        return link_to_markdown(v)
    if is_date(v):
        return date_to_iso(v)
    if is_duration(v):
        return duration_to_string(v)
    if is_task(v):
        return v["text"]
    if isinstance(v, list):
        return ", ".join(to_markdown(x) for x in v)
    from .js import json_key
    return json_key(v)


def to_str(v: Value) -> str:
    """The plain text of a value: what `string()` gives and what `+` joins.

    A link reads as what it shows, not as where it points: a table of links
    joined into a sentence should read as the sentence somebody wrote.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if is_number(v):
        return js_num_str(v)
    if is_link(v):
        return v.get("display") or v.get("target") or ""
    if is_date(v):
        return date_to_iso(v)
    if is_duration(v):
        return duration_to_string(v)
    if is_task(v):
        return v["text"]
    if isinstance(v, list):
        return ", ".join(to_str(x) for x in v)
    from .js import json_key
    return json_key(v)


# -------------------------------------------------------- truth and equality

def truthy(v: Value) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if is_number(v):
        return v != 0
    if isinstance(v, str):
        return len(v) > 0
    if isinstance(v, list):
        return len(v) > 0
    return True


def equals(a: Value, b: Value) -> bool:
    """Whether two values are the same thing.

    Links are the interesting case: `[[X]]` and `[[Folder/X]]` are the same note
    when only one of them says where it lives, because a bare name is resolved
    against the whole vault. Two full paths that differ are two notes.
    """
    if a is None:
        return b is None
    if b is None:
        return False
    if is_link(a) or is_link(b):
        la = a if is_link(a) else (link_from_string(a) if isinstance(a, str) else None)
        lb = b if is_link(b) else (link_from_string(b) if isinstance(b, str) else None)
        if la is None or lb is None:
            return False
        ka, kb = link_key_of(la), link_key_of(lb)
        if ka == kb:
            return True
        return (link_base_key(la) == link_base_key(lb)
                and ("/" not in ka or "/" not in kb))
    if is_date(a) and is_date(b):
        return a["ts"] == b["ts"]
    if is_duration(a) and is_duration(b):
        return a["ms"] == b["ms"]
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equals(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) or isinstance(b, dict):
        return to_str(a) == to_str(b)
    if isinstance(a, bool) != isinstance(b, bool):
        # `true == 1` is true there. Python already agrees, but only because a
        # bool *is* an int here; saying so keeps the next reader from tidying it.
        return bool(a) == bool(b) and (a == b)
    return a == b


def compare(a: Value, b: Value) -> int:
    """The order `SORT` uses. Nothing sorts last, whichever way the sort runs."""
    an = a is None
    bn = b is None
    if an and bn:
        return 0
    if an:
        return 1
    if bn:
        return -1
    if is_number(a) and is_number(b):
        return -1 if a < b else (1 if a > b else 0)
    if is_date(a) and is_date(b):
        return -1 if a["ts"] < b["ts"] else (1 if a["ts"] > b["ts"] else 0)
    if is_duration(a) and is_duration(b):
        return -1 if a["ms"] < b["ms"] else (1 if a["ms"] > b["ms"] else 0)
    if isinstance(a, bool) and isinstance(b, bool):
        return 0 if a == b else (1 if a else -1)
    if isinstance(a, list) and isinstance(b, list):
        for x, y in zip(a, b):
            c = compare(x, y)
            if c != 0:
                return c
        return (len(a) > len(b)) - (len(a) < len(b))
    return collate(to_str(a), to_str(b))


def contains_value(hay: Value, needle: Value) -> bool:
    """`contains()`: a list is asked about membership, text about a substring."""
    if hay is None:
        return False
    if isinstance(hay, list):
        return any(contains_value(x, needle) for x in hay)
    if is_link(hay) or is_link(needle):
        return equals(hay, needle)
    if is_plain_object(hay):
        return any(k == to_str(needle) for k in hay)
    return to_str(needle).lower() in to_str(hay).lower()
