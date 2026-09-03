"""The functions a query can call.

Everything the vault's thousand queries use, plus the neighbours somebody
reaches for next. A name that is not here raises rather than answering null: a
typo should show as a visible error in the block, not as an empty table that
looks like a true answer.

The awkward corners are all in the same place — the two languages count strings
differently, round differently and read numbers out of text differently — and
they are handled through `js.py` rather than re-decided here.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Any, Callable

from .js import (compile_js, js_len, js_num_str, js_pad, js_round, js_round_to,
                 js_slice, parse_float)
from .values import (
    Value, compare, contains_value, date_from_string, date_to_iso,
    duration_from_string, duration_to_string, equals, format_date, is_date,
    is_duration, is_link, is_number, make_date, make_link, start_of_day, to_str,
    truthy,
)

DAY_MS = 86_400_000


class UnknownFunction(Exception):
    """A name no function answers to. It reaches the person who wrote it."""


class BadPattern(Exception):
    """A regular expression the engine will not take, where that is an error."""


def as_list(v: Value) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def num(v: Value) -> float | int | None:
    """A value as a number, or nothing.

    Text is read with the forgiving reading, the one that takes `2 days` for 2 —
    which is what the other side does here and what half the fields in a vault
    need, because they were written by a person and not by a form.
    """
    if isinstance(v, bool):
        return 1 if v else 0
    if is_number(v):
        return v
    if isinstance(v, str):
        n = parse_float(v.replace(",", ".", 1))
        return None if math.isnan(n) else n
    if is_duration(v):
        return v["ms"]
    return None


def _now():
    from datetime import datetime
    return datetime.now()


def to_date(v: Value) -> Value:
    """A value as a day. The words a person types are days too."""
    if is_date(v):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        now = _now()
        if s == "today":
            return make_date(start_of_day(now).timestamp() * 1000, False)
        if s == "now":
            return make_date(now.timestamp() * 1000, True)
        if s == "tomorrow":
            return make_date(start_of_day(now + timedelta(days=1)).timestamp() * 1000, False)
        if s == "yesterday":
            return make_date(start_of_day(now - timedelta(days=1)).timestamp() * 1000, False)
        if s in ("sow", "som", "soy"):
            d = start_of_day(now)
            if s == "sow":
                # The week starts on Monday here, as it does there: that
                # language counts Sunday as zero, so the shift is written out.
                weekday = (d.weekday() + 1) % 7
                d = d - timedelta(days=(weekday + 6) % 7)
            elif s == "som":
                d = d.replace(day=1)
            else:
                d = d.replace(month=1, day=1)
            return make_date(d.timestamp() * 1000, False)
        return date_from_string(v)
    return None


def _lambda(arg: Any, ctx: Any, ev: Callable) -> Callable[..., Value]:
    """A function argument turned into something callable.

    A lambda gets its parameters bound and its body evaluated per call; anything
    else is evaluated once and handed back unchanged, which is how `sort(list,
    x)` degrades into sorting by a fixed value rather than failing.
    """
    from .dql import Lambda, Row
    if arg is None:
        return lambda *_: None
    if isinstance(arg, Lambda):
        def run(*vals: Value) -> Value:
            variables = dict(ctx.row.vars)
            for i, p in enumerate(arg.params):
                variables[p] = vals[i] if i < len(vals) else None
            inner = ctx.__class__(index=ctx.index,
                                  row=Row(page=ctx.row.page, item=ctx.row.item,
                                          vars=variables),
                                  this_page=ctx.this_page)
            return ev(arg.body, inner)
        return run
    v = ev(arg, ctx)
    return lambda *_: v


_DOLLAR = re.compile(r"\$(\$|&|\d{1,2})")


def _replacement(text: str) -> str:
    """A replacement string written for the other engine, spelled for this one.

    There a group is `$1` and the whole match is `$&`; here they are `\\1` and
    `\\g<0>`. A backslash in the text has no meaning there and would gain one
    here, so it is escaped first.
    """
    out = text.replace("\\", "\\\\")

    def swap(m: re.Match) -> str:
        what = m.group(1)
        if what == "$":
            return "$"
        if what == "&":
            return r"\g<0>"
        return f"\\g<{int(what)}>"
    return _DOLLAR.sub(swap, out)


def call_function(raw_name: str, args: list, ctx: Any, ev: Callable) -> Value:
    name = raw_name.lower()

    def val(i: int) -> Value:
        return None if i >= len(args) else ev(args[i], ctx)

    # ------------------------------------------------------------- strings
    if name == "string":
        return to_str(val(0))
    if name == "number":
        return num(val(0))
    if name == "lower":
        return to_str(val(0)).lower()
    if name == "upper":
        return to_str(val(0)).upper()
    if name == "replace":
        subject = to_str(val(0))
        needle = to_str(val(1))
        replacement = to_str(val(2))
        # Split and join, not a regular expression: the text is taken literally,
        # so a `.` in it is a full stop. An empty needle puts the replacement
        # *between* the characters and not around them, which is where Python's
        # own `replace` would have answered differently.
        parts = list(subject) if needle == "" else subject.split(needle)
        return replacement.join(parts)
    if name == "regexreplace":
        pattern = compile_js(to_str(val(1)))
        subject = to_str(val(0))
        if pattern is None:
            return subject
        try:
            return pattern.sub(_replacement(to_str(val(2))), subject)
        except re.error:
            return subject
    if name in ("regexmatch", "regextest"):
        pattern = to_str(val(0))
        subject = to_str(val(1))
        # `regexmatch` asks whether the whole string is the pattern; `regextest`
        # whether it appears anywhere.
        compiled = compile_js(f"^(?:{pattern})$" if name == "regexmatch" else pattern)
        if compiled is None:
            return False
        return compiled.search(subject) is not None
    if name == "split":
        pattern = compile_js(to_str(val(1)))
        if pattern is None:
            # Unlike the two above, this one does not catch on the other side:
            # a pattern the engine refuses ends the whole query with an error,
            # and quietly answering with the unsplit text instead would hide it.
            raise BadPattern(f"split(): {to_str(val(1))!r} is not a pattern")
        return pattern.split(to_str(val(0)))
    if name == "join":
        sep = to_str(val(1)) if len(args) > 1 else ", "
        return sep.join(to_str(x) for x in as_list(val(0)))
    if name == "truncate":
        s = to_str(val(0))
        n = num(val(1))
        n = js_len(s) if n is None else int(n)
        suffix = to_str(val(2)) if len(args) > 2 else "..."
        if js_len(s) <= n:
            return s
        return js_slice(s, 0, max(0, n - js_len(suffix))) + suffix
    if name in ("padleft", "padright"):
        s = to_str(val(0))
        n = num(val(1)) or 0
        filler = to_str(val(2)) if len(args) > 2 else " "
        return js_pad(s, int(n), filler, start=name == "padleft")

    # --------------------------------------------------------- collections
    if name == "contains":
        return contains_value(val(0), val(1))
    if name == "econtains":
        hay = val(0)
        needle = val(1)
        if isinstance(hay, list):
            return any(equals(x, needle) for x in hay)
        return equals(hay, needle)
    if name == "containsword":
        words = re.split(r"\W+", to_str(val(0)).lower(), flags=re.ASCII)
        return to_str(val(1)).lower() in words
    if name == "length":
        v = val(0)
        if v is None:
            return 0
        if isinstance(v, list):
            return len(v)
        if isinstance(v, str):
            return js_len(v)
        if isinstance(v, dict):
            return len(v)
        return 1
    if name == "sum":
        total: float | int = 0
        for x in as_list(val(0)):
            n = num(x)
            total += 0 if n is None else n
        return total
    if name == "average":
        values = [n for n in (num(x) for x in as_list(val(0))) if n is not None]
        return sum(values) / len(values) if values else None
    if name == "product":
        total = 1
        for x in as_list(val(0)):
            n = num(x)
            total *= 1 if n is None else n
        return total
    if name in ("min", "max"):
        items = [val(i) for i in range(len(args))] if len(args) > 1 else as_list(val(0))
        if not items:
            return None
        best = items[0]
        for b in items[1:]:
            c = compare(b, best)
            if (c < 0) if name == "min" else (c > 0):
                best = b
        return best
    if name == "first":
        items = as_list(val(0))
        return items[0] if items else None
    if name == "last":
        items = as_list(val(0))
        return items[-1] if items else None
    if name == "reverse":
        return list(reversed(as_list(val(0))))
    if name == "sort":
        from functools import cmp_to_key
        key = _lambda(args[1], ctx, ev) if len(args) > 1 else None
        items = list(as_list(val(0)))
        if key is None:
            return sorted(items, key=cmp_to_key(compare))
        return sorted(items, key=cmp_to_key(lambda a, b: compare(key(a), key(b))))
    if name == "unique":
        out: list = []
        for x in as_list(val(0)):
            if not any(equals(x, y) for y in out):
                out.append(x)
        return out
    if name == "nonnull":
        return [x for x in as_list(val(0)) if x is not None and x != ""]
    if name == "flat":
        out = []
        for x in as_list(val(0)):
            if isinstance(x, list):
                out.extend(x)
            else:
                out.append(x)
        return out
    if name == "slice":
        items = as_list(val(0))
        start = num(val(1))
        start = 0 if start is None else int(start)
        if len(args) > 2:
            stop = num(val(2))
            stop = len(items) if stop is None else int(stop)
        else:
            stop = len(items)
        return items[_at(start, len(items)):_at(stop, len(items))]
    if name == "filter":
        f = _lambda(args[1] if len(args) > 1 else None, ctx, ev)
        return [x for x in as_list(val(0)) if truthy(f(x))]
    if name == "map":
        f = _lambda(args[1] if len(args) > 1 else None, ctx, ev)
        return [f(x) for x in as_list(val(0))]
    if name == "any":
        if len(args) > 1:
            f = _lambda(args[1], ctx, ev)
            return any(truthy(f(x)) for x in as_list(val(0)))
        return any(truthy(x) for x in as_list(val(0)))
    if name == "all":
        if len(args) > 1:
            f = _lambda(args[1], ctx, ev)
            return all(truthy(f(x)) for x in as_list(val(0)))
        return all(truthy(x) for x in as_list(val(0)))
    if name == "none":
        f = _lambda(args[1], ctx, ev) if len(args) > 1 else None
        return not any(truthy(f(x)) if f else truthy(x) for x in as_list(val(0)))

    # ---------------------------------------------------------------- misc
    if name == "choice":
        return val(1) if truthy(val(0)) else val(2)
    if name in ("default", "ldefault"):
        v = val(0)
        return val(1) if (v is None or v == "") else v
    if name == "typeof":
        v = val(0)
        if v is None:
            return "null"
        if isinstance(v, list):
            return "array"
        if is_link(v):
            return "link"
        if is_date(v):
            return "date"
        if is_duration(v):
            return "duration"
        if isinstance(v, bool):
            return "boolean"
        if is_number(v):
            return "number"
        if isinstance(v, str):
            return "string"
        return "object"
    if name == "link":
        target = val(0)
        link = dict(target) if is_link(target) else make_link(to_str(target))
        if len(args) > 1:
            link["display"] = to_str(val(1))
        return link
    if name == "embed":
        target = val(0)
        link = dict(target) if is_link(target) else make_link(to_str(target))
        link["embed"] = truthy(val(1)) if len(args) > 1 else True
        return link
    if name == "elink":
        url = to_str(val(0))
        label = to_str(val(1)) if len(args) > 1 else url
        return f"[{label}]({url})"

    # ------------------------------------------------------ dates & numbers
    if name == "date":
        return to_date(val(0))
    if name == "dur":
        v = val(0)
        if is_duration(v):
            return v
        return duration_from_string(to_str(v))
    if name == "dateformat":
        d = to_date(val(0))
        return format_date(d, to_str(val(1))) if is_date(d) else None
    if name == "durationformat":
        d = val(0)
        return duration_to_string(d) if is_duration(d) else None
    if name == "striptime":
        d = to_date(val(0))
        if not is_date(d):
            return None
        from .values import as_local
        return make_date(start_of_day(as_local(d)).timestamp() * 1000, False)
    if name == "round":
        n = num(val(0))
        if n is None:
            return None
        digits = num(val(1)) if len(args) > 1 else 0
        return js_round_to(n, int(digits or 0))
    if name == "floor":
        n = num(val(0))
        return None if n is None else math.floor(n)
    if name == "ceil":
        n = num(val(0))
        return None if n is None else math.ceil(n)
    if name == "abs":
        n = num(val(0))
        return None if n is None else abs(n)

    raise UnknownFunction(f"unknown function '{raw_name}()'")


def _at(i: int, n: int) -> int:
    """A slice bound the way that language reads it: negative counts from the end."""
    return max(0, n + i) if i < 0 else min(i, n)
