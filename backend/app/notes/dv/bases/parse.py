"""A table file, read.

A `.base` file is YAML with a fixed shape: filters that apply to the whole file,
named formulas, per-property display settings, and a list of views of which the
first is the one that opens. An empty file is a valid table file — it means one
table over everything.

Anything not understood is kept rather than dropped, so writing the file back
from here would not quietly delete what a newer version of the format put into
it. Nothing writes them yet; this is what makes that safe when something does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ...model.frontmatter import Loader

NAMESPACED = re.compile(r"^(file|note|formula)\.")


@dataclass
class Filter:
    """An expression, or a group of them combined."""
    kind: str                                    # expr | and | or | not
    source: str = ""
    items: list["Filter"] = field(default_factory=list)
    item: "Filter | None" = None


@dataclass
class Sort:
    property: str
    direction: str = "ASC"


@dataclass
class View:
    type: str = "table"
    name: str = ""
    filters: Filter | None = None
    order: list[str] = field(default_factory=list)
    sort: list[Sort] = field(default_factory=list)
    group_by: dict | None = None
    summaries: dict[str, str] = field(default_factory=dict)
    limit: int | None = None
    unrecognized: dict = field(default_factory=dict)


@dataclass
class Config:
    filters: Filter | None = None
    formulas: dict[str, str] = field(default_factory=dict)
    properties: dict[str, dict] = field(default_factory=dict)
    new_item_folder: str | None = None
    new_item_template: str | None = None
    views: list[View] = field(default_factory=list)
    unrecognized: dict = field(default_factory=dict)


def normalize_id(raw: Any) -> str:
    """Give a property its namespace.

    A bare name is a note's own property, which is why `status` and
    `note.status` mean the same thing; `file.`, `formula.` and `note.` say so.
    """
    t = str(raw).strip()
    return t if NAMESPACED.match(t) else f"note.{t}"


def _filter(raw: Any) -> Filter | None:
    if isinstance(raw, str):
        s = raw.strip()
        return Filter("expr", source=s) if s else None
    if isinstance(raw, list):
        # A bare list is an implicit "all of these", which is how the format's
        # own examples read even where the key is usually spelled out.
        items = [f for f in (_filter(x) for x in raw) if f]
        return Filter("and", items=items) if items else None
    if isinstance(raw, dict):
        if isinstance(raw.get("and"), list):
            items = [f for f in (_filter(x) for x in raw["and"]) if f]
            return Filter("and", items=items) if items else None
        if isinstance(raw.get("or"), list):
            items = [f for f in (_filter(x) for x in raw["or"]) if f]
            return Filter("or", items=items) if items else None
        if raw.get("not") is not None:
            inner = _filter(raw["not"])
            return Filter("not", item=inner) if inner else None
    return None


def _sorts(raw: Any) -> list[Sort]:
    if not isinstance(raw, list):
        return []
    out: list[Sort] = []
    for e in raw:
        if isinstance(e, str):
            out.append(Sort(normalize_id(e)))
        elif isinstance(e, dict) and isinstance(e.get("property"), str):
            direction = "DESC" if str(e.get("direction", "ASC")).upper() == "DESC" else "ASC"
            out.append(Sort(normalize_id(e["property"]), direction))
    return out


VIEW_KEYS = {"type", "name", "filters", "order", "sort", "groupBy", "summaries", "limit"}
ROOT_KEYS = {"filters", "formulas", "properties", "newItemFolder", "newItemTemplate",
             "views"}


def _rest(obj: dict, known: set[str]) -> dict:
    return {k: v for k, v in obj.items() if k not in known}


def _view(raw: Any, index: int) -> View:
    o = raw if isinstance(raw, dict) else {}
    summaries = {}
    if isinstance(o.get("summaries"), dict):
        summaries = {normalize_id(k): str(v) for k, v in o["summaries"].items()}

    group_raw = o.get("groupBy")
    group_by = None
    if isinstance(group_raw, str):
        group_by = {"property": normalize_id(group_raw), "direction": "ASC"}
    elif isinstance(group_raw, dict) and isinstance(group_raw.get("property"), str):
        direction = "DESC" if str(group_raw.get("direction", "ASC")).upper() == "DESC" else "ASC"
        group_by = {"property": normalize_id(group_raw["property"]), "direction": direction}

    limit = o.get("limit")
    return View(
        type=o["type"] if isinstance(o.get("type"), str) and o["type"] else "table",
        name=o["name"] if isinstance(o.get("name"), str) and o["name"] else f"View {index + 1}",
        filters=_filter(o.get("filters")),
        order=[normalize_id(c) for c in o["order"]] if isinstance(o.get("order"), list) else [],
        sort=_sorts(o.get("sort")),
        group_by=group_by,
        summaries=summaries,
        limit=limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else None,
        unrecognized=_rest(o, VIEW_KEYS),
    )


def parse(source: str) -> Config:
    try:
        doc = yaml.load(source, Loader=Loader) or {}
    except yaml.YAMLError:
        doc = {}
    o = doc if isinstance(doc, dict) else {}

    formulas = {}
    if isinstance(o.get("formulas"), dict):
        formulas = {str(k): str(v) for k, v in o["formulas"].items()}

    properties: dict[str, dict] = {}
    if isinstance(o.get("properties"), dict):
        for k, v in o["properties"].items():
            cfg = v if isinstance(v, dict) else {}
            name = cfg.get("displayName")
            properties[normalize_id(k)] = {
                "displayName": name if isinstance(name, str) else None}

    views_raw = o["views"] if isinstance(o.get("views"), list) else []
    # An empty file is a table over the whole vault.
    views = ([_view(v, i) for i, v in enumerate(views_raw)] if views_raw
             else [_view({"type": "table", "name": "Table"}, 0)])

    return Config(
        filters=_filter(o.get("filters")),
        formulas=formulas,
        properties=properties,
        new_item_folder=o["newItemFolder"] if isinstance(o.get("newItemFolder"), str) else None,
        new_item_template=o["newItemTemplate"] if isinstance(o.get("newItemTemplate"), str) else None,
        views=views,
        unrecognized=_rest(o, ROOT_KEYS),
    )
