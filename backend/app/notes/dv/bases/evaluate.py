"""Working out what a formula says about one note.

Three namespaces are in scope: `file.` for what the file system knows, `note.`
for the note's own properties (which a bare name also means), and `formula.` for
the named formulas of the file — those may build on one another, so each is
worked out once per note and remembered, and one that ends up needing itself
gives null instead of running for ever.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..js import js_len, js_number, js_slice, json_key, to_fixed
from ..pages import Page, PageIndex
from ..values import (
    Value, as_local, compare, contains_value, date_from_string, equals,
    format_date, is_date, is_duration, is_link, is_number, make_date, make_link,
    start_of_day, to_str, truthy,
)
from .formula import FormulaError, Node, parse_formula


@dataclass
class Context:
    index: PageIndex
    page: Page
    formulas: dict[str, str] = field(default_factory=dict)
    # Per-note memo, and at the same time the guard against a formula that ends
    # up asking for itself.
    memo: dict[str, Value] = field(default_factory=dict)
    pending: set = field(default_factory=set)


def num(v: Value) -> float | int | None:
    """A value as a number, the strict way.

    This language reads numbers the way `Number()` does, not the way the query
    language does: `12abc` is not 12 here, it is nothing. The two are different
    on purpose and both are copied as they are.
    """
    if isinstance(v, bool):
        return 1 if v else 0
    if is_number(v):
        return v
    if isinstance(v, str):
        n = js_number(v.replace(",", ".", 1))
        return None if math.isnan(n) or math.isinf(n) else n
    if is_date(v):
        return v["ts"]
    if is_duration(v):
        return v["ms"]
    return None


def as_list(v: Value) -> list:
    if isinstance(v, list):
        return v
    return [] if v is None else [v]


# ------------------------------------------------------------------- file.*

def file_property(ctx: Context, name: str) -> Value:
    page = ctx.page
    base = page.path[page.path.rfind("/") + 1:]
    if name in ("name", "basename"):
        # Both spellings mean the title as shown: a table of note names with
        # ".md" after every one of them is nobody's intent.
        return page.name
    if name == "filename":
        return base
    if name == "path":
        return page.path
    if name == "folder":
        return page.folder
    if name == "ext":
        return page.ext
    if name == "size":
        return page.size
    if name == "ctime":
        return page.ctime
    if name == "mtime":
        return page.mtime
    if name == "tags":
        # The tags already carry their hash. The implementation this comes from
        # put a second one in front of every tag, which made `file.tags` a
        # column of `##tag` and `file.hasTag()` answer no to everything. Fixed
        # rather than copied: nothing can have been built on a thing that never
        # once said yes.
        return list(page.tags)
    if name == "aliases":
        return page.aliases
    if name == "links":
        return page.outlinks
    if name == "backlinks":
        return ctx.index.inlinks_of(page.path)
    if name == "embeds":
        return [l for l in page.outlinks if l.get("embed")]
    if name == "properties":
        return page.fields
    if name == "file":
        return make_link(page.path)
    return None


def file_method(ctx: Context, name: str, args: list) -> Value:
    page = ctx.page
    arg = to_str(args[0]) if args else ""
    if name == "hasTag":
        # A tag stands for its subtree, the same as it does in search.
        wanted = [to_str(a).lstrip("#").lower() for a in args]
        wanted = [w for w in wanted if w]
        have = [t.lstrip("#").lower() for t in page.tags]
        return any(t == w or t.startswith(w + "/") for w in wanted for t in have)
    if name == "inFolder":
        f = re.sub(r"/+$", "", arg)
        return page.folder == f or page.folder.startswith(f + "/")
    if name == "hasProperty":
        return arg in page.fields
    if name == "hasLink":
        target = re.sub(r"\.md$", "", arg, flags=re.I).lower()
        return any(re.sub(r"\.md$", "", l["path"], flags=re.I).lower() == target
                   or l["target"].lower() == target for l in page.outlinks)
    if name == "asLink":
        return make_link(page.path, False)
    return None


# ----------------------------------------------------------------- methods

_TITLE = re.compile(r"(?<![A-Za-z0-9_])([^\W\d_])")

# Provisional wording, like the group headings of the task filter: these read as
# text in a cell and belong in the message catalogues with the rest.
RELATIVE_TODAY, RELATIVE_TOMORROW, RELATIVE_YESTERDAY = "heute", "morgen", "gestern"


def method_on(value: Value, name: str, args: list) -> Value:
    a0 = args[0] if args else None

    # A question about emptiness can be asked of anything.
    if name == "isEmpty":
        return value is None or value == "" or (isinstance(value, list) and not value)
    if name == "toString":
        return to_str(value)

    if isinstance(value, list):
        if name == "contains":
            return any(contains_value(v, a0) or equals(v, a0) for v in value)
        if name == "containsAll":
            return all(any(equals(v, x) or contains_value(v, x) for v in value) for x in args)
        if name == "containsAny":
            return any(any(equals(v, x) or contains_value(v, x) for v in value) for x in args)
        if name == "join":
            return (to_str(a0) if args else ", ").join(to_str(v) for v in value)
        if name == "length":
            return len(value)
        if name == "reverse":
            return list(reversed(value))
        if name == "sort":
            from functools import cmp_to_key
            return sorted(value, key=cmp_to_key(compare))
        if name == "unique":
            seen: set[str] = set()
            out = []
            for v in value:
                k = json_key(v)
                if k not in seen:
                    seen.add(k)
                    out.append(v)
            return out
        if name == "slice":
            start = num(a0) or 0
            stop = num(args[1]) if len(args) > 1 else None
            return value[int(start):(None if stop is None else int(stop))]
        if name == "first":
            return value[0] if value else None
        if name == "last":
            return value[-1] if value else None
        return None

    if is_date(value):
        if name == "format":
            return format_date(value, to_str(a0) or "YYYY-MM-DD")
        if name == "date":
            return make_date(start_of_day(as_local(value)).timestamp() * 1000, False)
        if name == "time":
            return format_date(value, "HH:mm")
        if name == "year":
            return as_local(value).year
        if name == "month":
            return as_local(value).month
        if name == "day":
            return as_local(value).day
        if name == "relative":
            from ..js import js_round
            days = js_round((value["ts"] - datetime.now().timestamp() * 1000) / 86_400_000)
            if days == 0:
                return RELATIVE_TODAY
            if days == 1:
                return RELATIVE_TOMORROW
            if days == -1:
                return RELATIVE_YESTERDAY
            return f"in {days} Tagen" if days > 0 else f"vor {-days} Tagen"
        return None

    if is_link(value):
        if name == "asFile":
            return value["path"]
        if name == "linksTo":
            return re.sub(r"\.md$", "", value["path"], flags=re.I).lower() == to_str(a0).lower()
        return None

    if is_number(value):
        if name == "toFixed":
            return to_fixed(float(value), int(num(a0) or 0))
        if name == "round":
            from ..js import js_round_to
            return js_round_to(float(value), int(num(a0) or 0))
        if name == "abs":
            return abs(value)
        if name == "ceil":
            return math.ceil(value)
        if name == "floor":
            return math.floor(value)
        return None

    if value is None:
        return None

    s = to_str(value)
    if name == "contains":
        return to_str(a0).lower() in s.lower()
    if name == "containsAll":
        return all(to_str(x).lower() in s.lower() for x in args)
    if name == "containsAny":
        return any(to_str(x).lower() in s.lower() for x in args)
    if name == "startsWith":
        return s.startswith(to_str(a0))
    if name == "endsWith":
        return s.endswith(to_str(a0))
    if name == "lower":
        return s.lower()
    if name == "upper":
        return s.upper()
    if name == "trim":
        return s.strip()
    if name == "title":
        return _TITLE.sub(lambda m: m.group(1).upper(), s)
    if name == "reverse":
        return s[::-1]
    if name == "replace":
        needle = to_str(a0)
        repl = to_str(args[1]) if len(args) > 1 else ""
        return repl.join(list(s) if needle == "" else s.split(needle))
    if name == "split":
        sep = to_str(a0) or " "
        return s.split(sep)
    if name == "slice":
        start = num(a0) or 0
        stop = num(args[1]) if len(args) > 1 else None
        return js_slice(s, int(start), None if stop is None else int(stop))
    if name == "length":
        return js_len(s)
    if name == "asDate":
        return date_from_string(s)
    if name == "asNumber":
        return num(s)
    return None


# --------------------------------------------------------------- functions

def global_function(name: str, args: list, ctx: Context) -> Value:
    a0 = args[0] if args else None
    if name == "if":
        return (args[1] if len(args) > 1 else None) if truthy(a0) else (
            args[2] if len(args) > 2 else None)
    if name == "list":
        out = []
        for a in args:
            out.extend(a if isinstance(a, list) else [a])
        return out
    if name == "number":
        return num(a0)
    if name == "string":
        return to_str(a0)
    if name == "date":
        return a0 if is_date(a0) else date_from_string(to_str(a0))
    if name == "now":
        return make_date(datetime.now().timestamp() * 1000, True)
    if name == "today":
        return make_date(start_of_day(datetime.now()).timestamp() * 1000, False)
    if name == "link":
        return make_link(to_str(a0), False)
    if name in ("min", "max", "sum"):
        numbers = [n for n in (num(x) for a in args for x in as_list(a)) if n is not None]
        if name == "sum":
            return sum(numbers)
        if not numbers:
            return None
        return min(numbers) if name == "min" else max(numbers)
    if name == "concat":
        return "".join(to_str(a) for a in args)
    if name == "contains":
        return contains_value(a0, args[1] if len(args) > 1 else None)
    return None


# -------------------------------------------------------------- evaluation

NAMESPACES = {"file", "note", "formula"}


def _namespace_of(node: Node) -> str | None:
    return node.name if node.kind == "ident" and node.name in NAMESPACES else None


def formula_value(name: str, ctx: Context) -> Value:
    if name in ctx.memo:
        return ctx.memo[name]
    src = ctx.formulas.get(name)
    if src is None:
        return None
    if name in ctx.pending:
        # A formula that ends up asking for itself has no value; saying so beats
        # filling the stack.
        return None
    ctx.pending.add(name)
    try:
        value = evaluate(parse_formula(src), ctx)
    except Exception:                             # noqa: BLE001
        value = None
    ctx.pending.discard(name)
    ctx.memo[name] = value
    return value


def evaluate(node: Node, ctx: Context) -> Value:
    kind = node.kind
    if kind == "lit":
        return node.value

    if kind == "ident":
        # A bare name is one of the note's own properties.
        if node.name in NAMESPACES:
            return None
        return ctx.page.fields.get(node.name)

    if kind == "member":
        ns = _namespace_of(node.target)
        if ns == "file":
            return file_property(ctx, node.name)
        if ns == "note":
            return ctx.page.fields.get(node.name)
        if ns == "formula":
            return formula_value(node.name, ctx)
        target = evaluate(node.target, ctx)
        if isinstance(target, dict) and not isinstance(target, list) and node.name in target:
            return target[node.name]
        # `x.length` reads as a property but is a method everywhere else.
        return method_on(target, node.name, [])

    if kind == "call":
        args = [evaluate(a, ctx) for a in node.args]
        if node.target is None:
            return global_function(node.name, args, ctx)
        ns = _namespace_of(node.target)
        if ns == "file":
            return file_method(ctx, node.name, args)
        if ns == "note":
            return method_on(ctx.page.fields.get(node.name), "toString", args)
        if ns == "formula":
            return method_on(formula_value(node.name, ctx), "toString", args)
        return method_on(evaluate(node.target, ctx), node.name, args)

    if kind == "index":
        target = evaluate(node.target, ctx)
        idx = evaluate(node.index, ctx)
        if isinstance(target, list):
            i = num(idx)
            if i is None:
                return None
            i = int(i)
            i = len(target) + i if i < 0 else i
            return target[i] if 0 <= i < len(target) else None
        if isinstance(target, dict):
            return target.get(to_str(idx))
        return None

    if kind == "unary":
        v = evaluate(node.item, ctx)
        if node.op == "!":
            return not truthy(v)
        n = num(v)
        return None if n is None else -n

    if kind == "ternary":
        return evaluate(node.then, ctx) if truthy(evaluate(node.cond, ctx)) \
            else evaluate(node.other, ctx)

    if kind == "binary":
        op = node.op
        if op == "&&":
            return truthy(evaluate(node.right, ctx)) if truthy(evaluate(node.left, ctx)) else False
        if op == "||":
            return True if truthy(evaluate(node.left, ctx)) else truthy(evaluate(node.right, ctx))
        l = evaluate(node.left, ctx)
        r = evaluate(node.right, ctx)
        if op == "==":
            return equals(l, r)
        if op == "!=":
            return not equals(l, r)
        if op in (">", "<", ">=", "<="):
            # An ordering comparison against a value that is not there is false:
            # a note without a price is not "cheaper than 10", it is simply not
            # in the answer. Sorting still puts such notes last, which is a
            # different question and stays where it is.
            if l is None or r is None:
                return False
            c = compare(l, r)
            return {">" : c > 0, "<": c < 0, ">=": c >= 0, "<=": c <= 0}[op]
        if op == "+":
            # Plus joins text when either side is text and adds otherwise, which
            # is what a formula building a label expects.
            if isinstance(l, str) or isinstance(r, str):
                return to_str(l) + to_str(r)
            a, b = num(l), num(r)
            return None if a is None or b is None else a + b
        a, b = num(l), num(r)
        if a is None or b is None:
            return None
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return None if b == 0 else a / b
        if op == "%":
            return None if b == 0 else math.fmod(a, b)
        return None

    return None


def evaluate_source(src: str, ctx: Context) -> Value:
    """One formula against one note; null when it cannot be worked out."""
    try:
        return evaluate(parse_formula(src), ctx)
    except FormulaError:
        raise
    except Exception:                             # noqa: BLE001
        return None
