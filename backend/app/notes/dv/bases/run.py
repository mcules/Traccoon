"""A table file, run: the notes it collects, as rows.

The page index the query language uses is the source — a table file is a query
over the same notes, so there is nothing to index twice. What a view asks for is
worked out per note (its columns, its filters, its formulas), then the result is
filtered, sorted, grouped and summed, and only then cut to `limit`, so a limited
view is the top of the sorted list and not an arbitrary handful.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any

from ..js import collate_plain, js_round
from ..pages import Page, PageIndex
from ..values import Value, compare, to_str, truthy
from .evaluate import Context, evaluate
from .formula import FormulaError, parse_formula
from .parse import Config, Filter, View, parse as parse_base

# What a summary of an empty column reads as.
NOTHING = "—"


def _context(index: PageIndex, page: Page, config: Config, locale: str) -> Context:
    return Context(index=index, page=page, formulas=config.formulas, locale=locale)


def _matches(f: Filter | None, ctx: Context, errors: list[str]) -> bool:
    if f is None:
        return True
    if f.kind == "and":
        return all(_matches(x, ctx, errors) for x in f.items)
    if f.kind == "or":
        return any(_matches(x, ctx, errors) for x in f.items)
    if f.kind == "not":
        return not _matches(f.item, ctx, errors)
    try:
        return truthy(evaluate(parse_formula(f.source), ctx))
    except Exception as err:                      # noqa: BLE001
        # A broken filter is the file's mistake, not the note's: say so once,
        # rather than silently showing everything or nothing.
        msg = f'Filter "{f.source}": {err}'
        if msg not in errors:
            errors.append(msg)
        return False


def _value_of(column: str, ctx: Context, errors: list[str]) -> Value:
    try:
        return evaluate(parse_formula(column), ctx)
    except FormulaError as err:
        msg = f'Column "{column}": {err}'
        if msg not in errors:
            errors.append(msg)
        return None
    except Exception:                             # noqa: BLE001
        return None


def _columns_for(view: View, config: Config, pages: list[Page]) -> list[str]:
    """What a view shows: its own order, else everything worth showing."""
    if view.order:
        return view.order
    seen = {"file.name": None}
    for p in pages:
        for k in p.fields:
            seen[f"note.{k}"] = None
    for name in config.formulas:
        seen[f"formula.{name}"] = None
    return list(seen)


def _label_for(column: str, config: Config) -> str:
    configured = (config.properties.get(column) or {}).get("displayName")
    if configured:
        return configured
    bare = column.split(".", 1)[1] if "." in column else column
    return bare[:1].upper() + bare[1:]


# --------------------------------------------------------------- summaries

def _as_numbers(rows: list[dict], column: str) -> list[float]:
    out = []
    for r in rows:
        v = r["values"].get(column)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            n = float(v)
        elif isinstance(v, str):
            from ..js import js_number
            n = js_number(v.replace(",", ".", 1))
        else:
            continue
        if not math.isnan(n) and not math.isinf(n):
            out.append(n)
    return out


def _is_checked(v: Value) -> bool:
    return v is True or v == "true" or v == "yes"


def _number(n: float) -> str:
    from ..js import js_num_str
    return js_num_str(n)


def summarize(fn: str, rows: list[dict], column: str) -> str:
    name = "".join(c for c in fn.lower() if c not in " _-")
    values = [r["values"].get(column) for r in rows]
    numbers = _as_numbers(rows, column)
    filled = [v for v in values if v is not None and v != ""]

    if name in ("count", "countall"):
        return str(len(rows))
    if name in ("countvalues", "notempty", "countnotempty"):
        return str(len(filled))
    if name in ("empty", "countempty"):
        return str(len(rows) - len(filled))
    if name in ("unique", "countunique"):
        return str(len({to_str(v) for v in filled}))
    if name == "sum":
        return _number(js_round(sum(numbers) * 1e6) / 1e6)
    if name in ("average", "mean"):
        if not numbers:
            return NOTHING
        return _number(js_round(sum(numbers) / len(numbers) * 100) / 100)
    if name == "median":
        if not numbers:
            return NOTHING
        s = sorted(numbers)
        m = len(s) // 2
        return _number(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2)
    if name == "min":
        return _number(min(numbers)) if numbers else NOTHING
    if name == "max":
        return _number(max(numbers)) if numbers else NOTHING
    if name == "range":
        return _number(max(numbers) - min(numbers)) if numbers else NOTHING
    if name == "checked":
        return f"{sum(1 for v in values if _is_checked(v))} / {len(rows)}"
    if name == "unchecked":
        return str(sum(1 for v in values if not _is_checked(v)))
    if name == "percentchecked":
        if not rows:
            return NOTHING
        return f"{js_round(sum(1 for v in values if _is_checked(v)) / len(rows) * 100)} %"
    return ""


# --------------------------------------------------------------------- run

def run(index: PageIndex, source: str, view_name: str | None = None,
        locale: str = "en") -> dict:
    config = parse_base(source)
    errors: list[str] = []
    view = next((v for v in config.views if v.name == view_name), None) if view_name else None
    if view is None:
        view = config.views[0]

    # A table file is about notes; other files in the vault are not rows.
    candidates = [p for p in index.all() if p.ext in ("md", "markdown")]

    kept: list[tuple[Page, Context]] = []
    for page in candidates:
        ctx = _context(index, page, config, locale)
        if not _matches(config.filters, ctx, errors):
            continue
        if not _matches(view.filters, ctx, errors):
            continue
        kept.append((page, ctx))

    column_ids = _columns_for(view, config, [p for p, _ in kept])
    columns = [{"id": c, "label": _label_for(c, config)} for c in column_ids]

    rows: list[dict] = []
    for page, ctx in kept:
        values: dict[str, Value] = {}
        for c in column_ids:
            values[c] = _value_of(c, ctx, errors)
        # Grouping and sorting may use a property the view does not show.
        for s in view.sort:
            if s.property not in values:
                values[s.property] = _value_of(s.property, ctx, errors)
        if view.group_by and view.group_by["property"] not in values:
            values[view.group_by["property"]] = _value_of(view.group_by["property"], ctx, errors)
        rows.append({"path": page.path, "values": values})

    for s in reversed(view.sort):
        rows = sorted(rows, key=cmp_to_key(
            lambda a, b, _s=s: (-1 if _s.direction == "DESC" else 1) * compare(
                a["values"].get(_s.property), b["values"].get(_s.property))))

    matched = len(rows)
    if view.limit:
        rows = rows[:view.limit]

    groups = None
    if view.group_by:
        prop = view.group_by["property"]
        buckets: dict[str, dict] = {}
        for r in rows:
            value = r["values"].get(prop)
            key = NOTHING if (value is None or value == "") else to_str(value)
            g = buckets.get(key)
            if g is None:
                g = {"key": key, "value": value, "rows": []}
                buckets[key] = g
            g["rows"].append(r)
        descending = view.group_by["direction"] == "DESC"
        groups = sorted(buckets.values(), key=cmp_to_key(
            lambda a, b: collate_plain(b["key"], a["key"]) if descending
            else collate_plain(a["key"], b["key"])))

    summaries: dict[str, str] = {}
    for column, fn in view.summaries.items():
        text = summarize(fn, rows, column)
        if text:
            summaries[column] = text

    return {
        "views": [{"name": v.name, "type": v.type} for v in config.views],
        "view": {"name": view.name, "type": view.type},
        "columns": columns,
        "rows": rows,
        "groups": groups,
        "summaries": summaries,
        "total": len(rows),
        "matched": matched,
        "errors": errors,
    }
