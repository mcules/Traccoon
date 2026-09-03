"""The task filter: the second little language, and nothing like the first.

A ```tasks block is not a query with clauses. It is a list of one-line
instructions — filters, `sort by`, `group by`, and switches that only change how
the result looks — and every line stands on its own:

    not done
    due before in 7 days
    (tag includes #wichtig) OR (priority is above medium)
    sort by due
    group by filename

It reads the same task index the query language reads, so the two cannot drift
apart.

An instruction that is not understood is collected as a warning and the block
still answers. Refusing the whole block over one line somebody typed wrong would
hide the twenty tasks the other lines found; answering silently would hide the
line. Both are worse than saying so.

The headings of the groups are still the German ones this produced before the
move; they belong in the message catalogues and go there with the rest of the
interface, which is a separate step and not one to slip into a translation.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import cmp_to_key
from typing import Any, Callable

from .js import collate, compile_js
from .pages import PageIndex
from .values import date_from_string, make_date, pad

DAY_MS = 86_400_000

Pred = Callable[[dict], bool]


# ------------------------------------------------------------------- dates

def _start_of_today() -> datetime:
    n = datetime.now()
    return datetime(n.year, n.month, n.day)


_REL = re.compile(r"^in\s+(-?[0-9]+)\s+(day|days|week|weeks|month|months|year|years)$")
_AGO = re.compile(r"^(-?[0-9]+)\s+(day|days|week|weeks|month|months|year|years)\s+ago$")
_PERIOD = re.compile(r"^(next|last|this)\s+(week|month|year)$")


def _add_months(d: datetime, n: int) -> datetime:
    """Move by whole months the way that language's date object does: it lets
    the day overflow, so the 31st of January plus one month is the 3rd of
    March. Clamping instead would answer differently for exactly those dates."""
    total = (d.year * 12 + (d.month - 1)) + n
    year, month = divmod(total, 12)
    month += 1
    day = d.day
    base = datetime(year, month, 1)
    return base + timedelta(days=day - 1)


def parse_date_expr(raw: str) -> dict | None:
    """`today`, `in 7 days`, `3 days ago`, `next week`, `2026-05-08`, …"""
    s = raw.strip().lower()
    iso = date_from_string(s)
    if iso:
        return iso
    today = _start_of_today()

    def at(d: datetime) -> dict:
        return make_date(d.timestamp() * 1000, False)

    if s == "today":
        return at(today)
    if s == "tomorrow":
        return at(today + timedelta(days=1))
    if s == "yesterday":
        return at(today - timedelta(days=1))

    # `in -7 days` is allowed and means the same as `7 days ago`.
    rel = _REL.match(s)
    ago = _AGO.match(s)
    m = rel or ago
    if m:
        n = int(m.group(1)) * (-1 if ago else 1)
        unit = m.group(2)
        if unit.startswith("day"):
            return at(today + timedelta(days=n))
        if unit.startswith("week"):
            return at(today + timedelta(days=n * 7))
        if unit.startswith("month"):
            return at(_add_months(today, n))
        return at(_add_months(today, n * 12))

    period = _PERIOD.match(s)
    if period:
        sign = -1 if period.group(1) == "last" else (1 if period.group(1) == "next" else 0)
        what = period.group(2)
        if what == "week":
            # The week starts on Monday. That language numbers Sunday zero, so
            # the shift is written out rather than taken from either numbering.
            weekday = (today.weekday() + 1) % 7
            return at(today - timedelta(days=(weekday + 6) % 7) + timedelta(days=sign * 7))
        if what == "month":
            return at(_add_months(datetime(today.year, today.month, 1), sign))
        return at(datetime(today.year + sign, 1, 1))
    return None


# ----------------------------------------------------------------- filters

PRIORITY_RANK = {"highest": 5, "high": 4, "medium": 3, "none": 2, "low": 1, "lowest": 0}


def priority_rank(t: dict) -> int:
    p = t.get("priority")
    if p == "high":
        return 4
    if p == "medium":
        return 3
    if p == "low":
        return 1
    return 2                                    # none


_DESC_DATED = re.compile(r"[📅✅⏳🛫➕🔁⏫🔼🔽][^\s]*\s*[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DESC_MARKS = re.compile(r"[📅✅⏳🛫➕⏫🔼🔽]")
_DESC_RECUR = re.compile(r"🔁[^📅✅⏳🛫➕]*")
_DESC_BLOCK = re.compile(r"\s\^[A-Za-z0-9-]+\Z")
_SPACES = re.compile(r"\s+")


def task_description(t: dict) -> str:
    """The description as this language sees it: no marks, no dates, no block id."""
    text = _DESC_DATED.sub(" ", t["text"])
    text = _DESC_MARKS.sub(" ", text)
    text = _DESC_RECUR.sub(" ", text, count=1)
    text = _DESC_BLOCK.sub("", text, count=1)
    return _SPACES.sub(" ", text).strip()


def date_field_of(t: dict, field: str) -> dict | None:
    if field == "due":
        return t.get("due")
    if field == "done":
        return t.get("done")
    if field == "scheduled":
        return t.get("scheduled")
    if field in ("start", "starts"):
        return t.get("start")
    if field == "created":
        return t.get("created")
    if field == "happens":
        # The first of the three that is set: when the task actually shows up.
        return t.get("due") or t.get("scheduled") or t.get("start")
    return None


TEXT_FIELDS = ["path", "description", "heading", "folder", "filename", "tag", "tags"]


def text_values(t: dict, field: str) -> list[str]:
    if field == "path":
        return [t["path"]]
    if field == "folder":
        p = t["path"]
        return [p[:p.rfind("/") + 1] if "/" in p else "/"]
    if field == "filename":
        p = t["path"]
        return [p[p.rfind("/") + 1:]]
    if field == "description":
        return [task_description(t)]
    if field == "heading":
        return [t.get("section") or ""]
    if field in ("tag", "tags"):
        return t["tags"]
    return []


_STATUS_TYPE = re.compile(r"^status\.type\s+is\s+(todo|done|in_progress|cancelled|non_task)$")
_STATUS_NAME = re.compile(r"^status\.name\s+(includes|does not include)\s+(.+)$")
_HAS_DATE = re.compile(r"^(no|has)\s+(due|done|scheduled|start|created|happens)\s+date$")
_DATE_CMP = re.compile(r"^(due|done|scheduled|starts|start|created|happens)\s+"
                       r"(before|after|on or before|on or after|on|in)\s+(.+)$")
_PRIORITY = re.compile(r"^priority\s+is\s+(above\s+|below\s+|not\s+)?"
                       r"(highest|high|medium|none|low|lowest)$")
_TEXT_FILTER = re.compile(
    r"^(path|description|heading|folder|filename|tag|tags)\s+"
    r"(includes|include|does not include|do not include|regex matches|regex does not match)"
    r"\s+(.+)$", re.I)
_REGEX_LITERAL = re.compile(r"^/(.*)/([a-z]*)$")


def parse_filter(line: str) -> Pred | None:
    """One filter line as a question about a task, or nothing when it is not one."""
    s = line.strip()
    low = s.lower()

    # The configured status types decide this, not the character: a cancelled
    # task is not "not done", and one in progress is.
    def closed(t: dict) -> bool:
        return t["statusType"] in ("DONE", "CANCELLED")

    if low == "done":
        return closed
    if low == "not done":
        return lambda t: not closed(t)
    if low == "cancelled":
        return lambda t: t["statusType"] == "CANCELLED"
    if low == "in progress":
        return lambda t: t["statusType"] == "IN_PROGRESS"

    m = _STATUS_TYPE.match(low)
    if m:
        want = m.group(1).upper()
        return lambda t: t["statusType"] == want
    m = _STATUS_NAME.match(low)
    if m:
        needle = m.group(2).strip().lower()
        negate = m.group(1) == "does not include"
        return lambda t: (needle in t["statusName"].lower()) != negate

    if low == "is recurring":
        return lambda t: bool(t.get("recurrence"))
    if low == "is not recurring":
        return lambda t: not t.get("recurrence")
    if low == "exclude sub-items":
        return lambda t: t["level"] == 0

    m = _HAS_DATE.match(low)
    if m:
        want = m.group(1) == "has"
        field = m.group(2)
        return lambda t: (date_field_of(t, field) is not None) == want

    m = _DATE_CMP.match(low)
    if m:
        field, op = m.group(1), m.group(2)
        when = parse_date_expr(m.group(3))
        if when is None:
            return None

        def by_date(t: dict) -> bool:
            d = date_field_of(t, field)
            if d is None:
                return False
            if op == "before":
                return d["ts"] < when["ts"]
            if op == "after":
                return d["ts"] > when["ts"]
            if op == "on or before":
                return d["ts"] <= when["ts"]
            if op == "on or after":
                return d["ts"] >= when["ts"]
            return d["ts"] == when["ts"]        # on / in
        return by_date

    m = _PRIORITY.match(low)
    if m:
        mod = (m.group(1) or "").strip()
        rank = PRIORITY_RANK[m.group(2)]

        def by_priority(t: dict) -> bool:
            r = priority_rank(t)
            if mod == "above":
                return r > rank
            if mod == "below":
                return r < rank
            if mod == "not":
                return r != rank
            return r == rank
        return by_priority

    m = _TEXT_FILTER.match(s)
    if m and m.group(1).lower() in TEXT_FIELDS:
        field = m.group(1).lower()
        op = m.group(2).lower()
        arg = m.group(3).strip()
        if op.startswith("regex"):
            literal = _REGEX_LITERAL.match(arg)
            # No unicode flag on this side, unlike the query language's: the
            # engine there is called without it here, and it is more forgiving
            # about what it will take.
            pattern = compile_js(literal.group(1) if literal else arg,
                                 ignore_case=bool(literal and "i" in literal.group(2)),
                                 unicode_mode=False)
            if pattern is None:
                return None
            negate = op == "regex does not match"
            return lambda t: any(pattern.search(v) for v in text_values(t, field)) != negate
        needle = arg.lower()
        negate = op.startswith("do")
        return lambda t: any(needle in v.lower() for v in text_values(t, field)) != negate

    return None


_BOOL_WORD = re.compile(r"^(AND|OR|XOR|NOT)\b", re.I)
_NEXT_OP = re.compile(r"\s+(AND|OR|XOR)\b|\(", re.I)


def parse_boolean(line: str, warn: Callable[[str], None]) -> Pred | None:
    """A filter line with the combinators: `(A) AND (B)`, `NOT (A)`, `A XOR B`."""
    s = line.strip()
    if not re.search(r"[()]", s) or not re.search(r"\b(AND|OR|NOT|XOR)\b", s, re.I):
        p = parse_filter(s)
        if p is None:
            warn(s)
        return p

    parts: list[tuple[str, Any]] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == " ":
            i += 1
            continue
        if c == "(":
            depth = 0
            j = i
            while j < len(s):
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            parts.append(("p", parse_boolean(s[i + 1:j], warn)))
            i = j + 1
            continue
        op = _BOOL_WORD.match(s[i:])
        if op:
            parts.append(("op", op.group(1).upper()))
            i += len(op.group(1))
            continue
        rest = s[i:]
        cut = _NEXT_OP.search(rest)
        text = rest if cut is None else rest[:cut.start()]
        p = parse_filter(text)
        if p is None:
            warn(text.strip())
        parts.append(("p", p))
        i += len(text)

    # Left to right; this language wants the brackets written out anyway.
    acc: Pred | None = None
    pending: str | None = None
    negate_next = False
    for kind, value in parts:
        if kind == "op":
            if value == "NOT":
                negate_next = True
            else:
                pending = value
            continue
        p = value
        if p is None:
            return None
        if negate_next:
            inner = p
            p = lambda t, _inner=inner: not _inner(t)
            negate_next = False
        if acc is None:
            acc = p
        else:
            a, b = acc, p
            if pending == "OR":
                acc = lambda t, _a=a, _b=b: _a(t) or _b(t)
            elif pending == "XOR":
                acc = lambda t, _a=a, _b=b: _a(t) != _b(t)
            else:
                acc = lambda t, _a=a, _b=b: _a(t) and _b(t)
            pending = None
    return acc


# ------------------------------------------------------------ sort & group

_DATE_SORTS = ("due", "done", "scheduled", "start", "created", "happens")
# The largest whole number that language can hold exactly. Undated tasks are
# given it so they land at the end, which is where this puts them.
UNDATED = 9_007_199_254_740_991


def sort_value(t: dict, field: str) -> float | str:
    if field in _DATE_SORTS:
        d = date_field_of(t, field)
        return d["ts"] if d else UNDATED
    if field == "priority":
        return -priority_rank(t)                # highest first
    if field == "path":
        return t["path"].lower()
    if field == "status":
        return 1 if t["statusType"] in ("DONE", "CANCELLED") else 0
    if field == "description":
        return task_description(t).lower()
    return 0


# A group heading is read by a person, so it travels as a key rather than as a
# finished sentence: the interface looks it up in its own language. The German
# wording stays beside it in `label`, and not out of nostalgia — the answers
# these functions give were recorded from the service being replaced and are
# compared against it. A reader that knows nothing of catalogues (a script, the
# recorded comparison) still gets a sentence.
#
# Which field a group is by decides whether there is a key at all. A path, a
# folder, a file name and a status are not words this program chose — they come
# out of the vault, and translating them would be inventing.
PRIORITY_NAMES = {5: "Höchste", 4: "Hoch", 3: "Mittel", 2: "Ohne", 1: "Niedrig",
                  0: "Niedrigste"}
PRIORITY_KEYS = {5: "highest", 4: "high", 3: "medium", 2: "none", 1: "low",
                 0: "lowest"}
WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
            "Samstag"]
NO_HEADING = "Ohne Überschrift"
NO_TAG = "Ohne Tag"
# What a date field is called when a task has none of it. Only `due` had a German
# word on the other side; the rest fell through to the field's own name, and that
# is copied rather than tidied — the wording is what the recorded answers hold.
# The translated version travels beside it as a key.


def group_key(t: dict, field: str) -> dict:
    if field == "path":
        p = re.sub(r"\.(md|markdown)$", "", t["path"], flags=re.I)
        return {"key": p, "label": p, "link": t["path"]}
    if field == "folder":
        p = t["path"]
        f = p[:p.rfind("/")] if "/" in p else "/"
        return {"key": f, "label": f}
    if field == "filename":
        return {"key": t["noteName"], "label": t["noteName"], "link": t["path"]}
    if field == "heading":
        section = t.get("section")
        if section:
            return {"key": section, "label": section}
        return {"key": "", "label": NO_HEADING,
                "label_key": "notes_task.group_no_heading"}
    if field == "status":
        return {"key": t["statusType"], "label": t["statusName"]}
    if field == "priority":
        r = priority_rank(t)
        return {"key": str(r), "label": f"Priorität: {PRIORITY_NAMES.get(r, 'Ohne')}",
                "label_key": f"notes_task.group_priority_{PRIORITY_KEYS.get(r, 'none')}"}
    if field == "tags":
        tag = t["tags"][0] if t["tags"] else ""
        if tag:
            return {"key": tag, "label": tag}
        return {"key": "", "label": NO_TAG, "label_key": "notes_task.group_no_tag"}
    d = date_field_of(t, field)
    if d is None:
        what = "Fälligkeit" if field == "due" else field
        return {"key": "zzz-none", "label": f"Ohne {what}",
                "label_key": f"notes_task.group_no_{field}"}
    from .values import as_local
    x = as_local(d)
    iso = f"{x.year}-{pad(x.month)}-{pad(x.day)}"
    # The day itself is the key. The weekday beside it is a word in some
    # language, so the interface writes it from the date rather than reading it
    # from here — there is nothing to translate, only to format.
    return {"key": iso, "label": f"{iso} {WEEKDAYS[(x.weekday() + 1) % 7]}",
            "label_key": "notes_task.group_date"}


# ------------------------------------------------------------------ the run

LAYOUT_RE = re.compile(
    r"^(hide|show)\s+(task count|backlink|edit button|toolbar|urgency|priority|"
    r"due date|start date|scheduled date|done date|created date|recurrence rule|tags)$")
LIMIT_RE = re.compile(r"^limit(?:\s+to)?\s+([0-9]+)(?:\s+tasks?)?$")
SORT_RE = re.compile(r"^sort by\s+([a-z ]+?)(\s+reverse)?$")
GROUP_RE = re.compile(r"^group by\s+([a-z ]+?)(\s+reverse)?$")


def execute(index: PageIndex, src: str) -> dict:
    warnings: list[str] = []

    def warn(s: str) -> None:
        if s and s not in warnings:
            warnings.append(s)

    layout = {"hideTaskCount": False, "hideBacklink": False, "hideToolbar": False,
              "hideEditButton": False, "shortMode": False}
    preds: list[Pred] = []
    sorts: list[dict] = []
    groups: list[dict] = []
    limit = 0

    for raw_line in src.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()

        lay = LAYOUT_RE.match(low)
        if lay:
            on = lay.group(1) == "hide"
            what = lay.group(2)
            if what == "task count":
                layout["hideTaskCount"] = on
            elif what == "backlink":
                layout["hideBacklink"] = on
            elif what == "toolbar":
                layout["hideToolbar"] = on
            elif what == "edit button":
                layout["hideEditButton"] = on
            continue
        if low in ("short mode", "full mode"):
            layout["shortMode"] = low == "short mode"
            continue
        m = LIMIT_RE.match(low)
        if m:
            limit = int(m.group(1))
            continue
        m = SORT_RE.match(low)
        if m:
            sorts.append({"field": m.group(1).strip(), "reverse": bool(m.group(2))})
            continue
        m = GROUP_RE.match(low)
        if m:
            groups.append({"field": m.group(1).strip(), "reverse": bool(m.group(2))})
            continue
        if low.startswith("explain") or low.startswith("ignore global query"):
            continue

        pred = parse_boolean(line, warn)
        if pred is not None:
            preds.append(pred)

    hits: list[dict] = []

    def collect(note_name: str, t: dict) -> None:
        hits.append({**t, "noteName": note_name})
        for c in t["children"]:
            collect(note_name, c)

    for p in index.all():
        for t in p.tasks:
            collect(p.name, t)

    kept = [t for t in hits if all(f(t) for f in preds)]

    # Applied back to front, and each sort is stable: the last instruction is
    # the coarsest, which is how a person reads two `sort by` lines.
    for spec in reversed(sorts):
        field = spec["field"]

        def cmp(a: dict, b: dict, _f: str = field) -> int:
            va, vb = sort_value(a, _f), sort_value(b, _f)
            if isinstance(va, str) or isinstance(vb, str):
                return collate(str(va), str(vb))
            return -1 if va < vb else (1 if va > vb else 0)
        kept = sorted(kept, key=cmp_to_key(cmp), reverse=False)
        if spec["reverse"]:
            kept = sorted(kept, key=cmp_to_key(lambda a, b, _f=field: -cmp(a, b, _f)))

    total = len(kept)
    if limit > 0:
        kept = kept[:limit]

    if not groups:
        return {"kind": "tasks",
                "groups": [{"key": "", "label": "", "tasks": kept}],
                "total": total, "layout": layout, "warnings": warnings}

    field = groups[0]["field"]
    buckets: dict[str, dict] = {}
    for t in kept:
        g = group_key(t, field)
        entry = buckets.get(g["key"])
        if entry is None:
            entry = {"key": g["key"], "label": g["label"], "tasks": []}
            if "link" in g:
                entry["link"] = g["link"]
            if "label_key" in g:
                entry["label_key"] = g["label_key"]
            buckets[g["key"]] = entry
        entry["tasks"].append(t)
    out = sorted(buckets.values(), key=cmp_to_key(lambda a, b: collate(a["key"], b["key"])))
    if groups[0]["reverse"]:
        out.reverse()
    return {"kind": "tasks", "groups": out, "total": total, "layout": layout,
            "warnings": warnings}
