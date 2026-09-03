"""Recurrence, the way the vault's task plugin does it.

When a recurring task (🔁) is ticked off, the next instance is written above the
completed one rather than the task simply disappearing. What is covered is the
rule shapes that occur in this vault plus their obvious neighbours: `every
day|week|month|year`, `every N <unit>`, `every <weekday>`, `every year on
<Month> <day>`, each optionally followed by `when done`.

A rule that is not understood produces no next instance rather than a wrong one.
Ticking the task off still works; it simply does not come back, which is
visible — a task that returns on the wrong day is not.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

WEEKDAYS = {"sunday": 6, "sun": 6, "monday": 0, "mon": 0, "tuesday": 1, "tue": 1,
            "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "friday": 4,
            "fri": 4, "saturday": 5, "sat": 5}
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
          "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
          "november": 11, "december": 12}

# The three dates a task can carry, in the order they are looked at.
DATE_FIELDS = ("📅", "⏳", "🛫")
ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
RULE = re.compile(r"🔁\s*([^📅✅⏳🛫➕❌]*)")
DONE_MARK = re.compile(r"\s*[✅❌]\s*\d{4}-\d{2}-\d{2}")

YEAR_ON = re.compile(r"^year\s+on\s+([a-z]+)\s+(\d{1,2})$")
WEEKDAY_ONLY = re.compile(r"^([a-z]+)$")
EVERY_N = re.compile(r"^(?:(\d+)\s+)?(day|week|month|year)s?$")


@dataclass
class Rule:
    # Base the next date on the day it was done rather than on the old due date.
    when_done: bool
    kind: str                 # "yearly-on" | "weekday" | "every"
    unit: str = ""
    every: int = 1
    month: int = 0
    day: int = 0
    weekday: int = 0

    def next(self, when: dt.date) -> dt.date | None:
        if self.kind == "yearly-on":
            try:
                out = dt.date(when.year, self.month, self.day)
            except ValueError:
                return None
            return out if out > when else _add_years(out, 1)
        if self.kind == "weekday":
            out = when + dt.timedelta(days=1)
            while out.weekday() != self.weekday:
                out += dt.timedelta(days=1)
            return out
        if self.unit == "day":
            return when + dt.timedelta(days=self.every)
        if self.unit == "week":
            return when + dt.timedelta(weeks=self.every)
        if self.unit == "month":
            return _add_months(when, self.every)
        return _add_years(when, self.every)


def _add_months(when: dt.date, n: int) -> dt.date:
    """A month later. The 31st of a month with 30 days lands on the 30th, which
    is what a calendar does and what the other side's date arithmetic does."""
    month = when.month - 1 + n
    year = when.year + month // 12
    month = month % 12 + 1
    day = min(when.day, _days_in(year, month))
    return dt.date(year, month, day)


def _add_years(when: dt.date, n: int) -> dt.date:
    day = min(when.day, _days_in(when.year + n, when.month))
    return dt.date(when.year + n, when.month, day)


def _days_in(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


def parse(rule: str) -> Rule | None:
    raw = (rule or "").strip().lower()
    if not raw.startswith("every"):
        return None
    when_done = bool(re.search(r"\bwhen done\b", raw))
    body = re.sub(r"\bwhen done\b", "", raw)
    body = re.sub(r"^every\s*", "", body).strip()

    found = YEAR_ON.match(body)
    if found and found.group(1) in MONTHS:
        return Rule(when_done=when_done, kind="yearly-on",
                    month=MONTHS[found.group(1)], day=int(found.group(2)))

    found = WEEKDAY_ONLY.match(body)
    if found and found.group(1) in WEEKDAYS:
        return Rule(when_done=when_done, kind="weekday",
                    weekday=WEEKDAYS[found.group(1)])

    found = EVERY_N.match(body)
    if found:
        return Rule(when_done=when_done, kind="every",
                    every=int(found.group(1)) if found.group(1) else 1,
                    unit=found.group(2))
    return None


def _iso(day: dt.date) -> str:
    return day.isoformat()


def _read(text: str) -> dt.date | None:
    found = ISO.match(text)
    if not found:
        return None
    try:
        return dt.date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError:
        return None


def next_instance(line: str, today: dt.date | None = None) -> str | None:
    """The next instance of a recurring task line, or None if it does not recur.

    Every date on the line moves by the same amount as the reference date, so a
    task scheduled three days before it is due keeps that spacing.
    """
    today = today or dt.date.today()
    found = RULE.search(line)
    if not found:
        return None
    rule = parse(found.group(1))
    if rule is None:
        return None

    # Reference date: due, else scheduled, else start.
    reference: dt.date | None = None
    for emoji in DATE_FIELDS:
        m = re.search(f"{emoji}" + r"\s*(\d{4}-\d{2}-\d{2})", line)
        if m:
            reference = _read(m.group(1))
            if reference:
                break

    base = today if (rule.when_done or reference is None) else reference
    next_ref = rule.next(base)
    if next_ref is None:
        return None
    shift = (next_ref - reference).days if reference else 0

    out = line
    for emoji in DATE_FIELDS:
        def move(m: re.Match) -> str:
            was = _read(m.group(2))
            if was is None:
                return m.group(0)
            moved = was + dt.timedelta(days=shift) if (reference and shift) else next_ref
            return f"{m.group(1)}{_iso(moved)}"
        out = re.sub(f"({emoji}" + r"\s*)(\d{4}-\d{2}-\d{2})", move, out, count=1)
    # A fresh instance carries no completion marks.
    return DONE_MARK.sub("", out)
