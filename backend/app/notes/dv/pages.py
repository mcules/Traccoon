"""One note, as everything that asks about notes sees it.

A page is the note taken apart: its properties, whether they were written in the
block at the top or as `key:: value` in the middle, its tags, its tasks, its
list items, its headings and everything it points at. The three query languages
all read this and nothing else, so a note that is parsed wrong here is wrong in
all three at once, in the same way, which is at least a failure that can be
found.

Built for the whole vault once and then kept per file, because six thousand
notes must not be read again because one of them was saved.

The rules are copied rather than improved, and the reason is always the same: a
tag that is a tag on one side and prose on the other, a field that is a number
here and text there, moves notes in and out of somebody's answers without
anybody touching a query.
"""
from __future__ import annotations

import re
from datetime import date as _date, datetime as _datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from ..model.frontmatter import split as split_frontmatter
from . import settings as plugin_settings
from .values import (
    Value, as_local, date_from_ms, date_from_string, duration_from_string,
    is_link, make_link,
)

# ------------------------------------------------------------------ patterns
# `\p{L}` and `\p{N}` have no spelling in this engine. `\w` is letters, digits
# and the underscore once Unicode is on, which is the same set, and the classes
# below are written out from there.
LETTER = r"[^\W\d]"                       # a Unicode letter or the underscore
TAG_CHAR = r"[\w\-/]"
TAG_INNER = rf"(?:{LETTER}|[-/])"

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")
INLINE_FIELD_RE = re.compile(r"[\[(]\s*([^\[\]():]{1,60}?)\s*::\s*([^\])]*)\s*[\])]")
LINE_FIELD_RE = re.compile(r"^\s*([^-*+>#\s][^:]{0,60}?)\s*::\s*(.*)$")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\]]+?)\]\]")
TAG_RE = re.compile(rf"(?:^|[\s(\[])#({TAG_CHAR}*{TAG_INNER}{TAG_CHAR}*)")
TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[(.)\]\s?(.*)$")
LIST_RE = re.compile(r"^(\s*)[-*+]\s+(?!\[.\])(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMERIC_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
DAY_IN_NAME_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})")
NOTE_SUFFIX_RE = re.compile(r"\.(md|markdown)$", re.I)


def mask_fences(lines: list[str]) -> list[str]:
    """Blank out fenced blocks, keeping the line count.

    A ```dataviewjs block is full of `#` and `[[` that are code, not tags and not
    links. Blanking rather than dropping keeps every later line number pointing
    at the line it came from, which is what lets a task be written back.
    """
    out = list(lines)
    fence: str | None = None
    for i, text in enumerate(out):
        m = FENCE_RE.match(text)
        if fence is not None:
            if m and text.strip().startswith(fence):
                fence = None
            out[i] = ""
        elif m:
            fence = m.group(2)[0] * 3
            out[i] = ""
    return out


# -------------------------------------------------------------------- values

def _ms_of_yaml_date(v: _date | _datetime) -> float:
    """A date out of the block at the top, as a moment.

    The YAML reader on the other side hands back a point in time read as UTC —
    `2026-09-02` is midnight UTC, not midnight here. Reading it as local would
    move every such date by the offset of the zone, so it is read the same way.
    """
    if isinstance(v, _datetime):
        base = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return base.timestamp() * 1000.0
    return _datetime(v.year, v.month, v.day, tzinfo=timezone.utc).timestamp() * 1000.0


def coerce_value(v: Any, from_yaml: bool = False) -> Value:
    """A raw scalar as a value the query languages can work with.

    `from_yaml` is the whole difference between the two places fields come from.
    A property in the block at the top is only ever tried as a date, a span or a
    link and otherwise stays the text it is — which is what keeps a telephone
    number written `0602183139` from becoming a number and losing its zero. A
    field written inline is read with the full grammar, where numbers and
    true/false do apply.
    """
    if v is None:
        return None
    if isinstance(v, (_datetime, _date)):
        ms = _ms_of_yaml_date(v)
        local = as_local({"ts": ms})
        return date_from_ms(ms, local.hour != 0 or local.minute != 0)
    if isinstance(v, list):
        return [coerce_value(x, from_yaml) for x in v]
    if isinstance(v, dict):
        return {k: coerce_value(val, from_yaml) for k, val in v.items()}
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return ""
    link = None
    m = re.match(r"^!?\[\[([^\]]+)\]\]$", s)
    if m:
        link = make_link(m.group(1), s.startswith("!"))
    if link:
        return link
    d = date_from_string(s)
    if d:
        return d
    if from_yaml:
        dur = duration_from_string(s)
        if dur and re.search(r"[a-zA-Z]", s) and re.search(r"[0-9]", s):
            return dur
        return s
    if NUMERIC_RE.match(s):
        return float(s) if "." in s else int(s)
    if s == "true" or s == "false":
        return s == "true"
    # Text that is nothing but links (`[[A]], [[B]]`) is a list of them.
    links = list(WIKILINK_RE.finditer(s))
    if links and re.sub(r"[\s,]", "", WIKILINK_RE.sub("", s)) == "":
        return [make_link(m.group(2), m.group(1) == "!") for m in links]
    return s


# --------------------------------------------------------------------- tasks

EMOJI_FIELDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"📅\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "due"),
    (re.compile(r"✅\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "done"),
    (re.compile(r"⏳\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "scheduled"),
    (re.compile(r"🛫\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "start"),
    (re.compile(r"➕\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "created"),
]
RECURRENCE_RE = re.compile(r"🔁\s*([^📅✅⏳🛫➕]*)")


def parse_task(rel: str, line: int, indent: str, status: str, text: str) -> dict:
    meta = plugin_settings.status_for(status)
    task: dict = {
        "kind": "task",
        "text": text.strip(),
        "status": status,
        "statusType": meta["type"],
        "statusName": meta["name"],
        # `completed` keeps the meaning the query language gives it — the box
        # holds an x. The task engine works off the status type instead, where a
        # cancelled task also counts as closed. Two questions, two answers.
        "completed": status.lower() == "x",
        "fullyCompleted": status.lower() == "x",
        "checked": status != " ",
        "path": rel,
        "line": line,
        "level": len(indent.replace("\t", "    ")) // 2,
        "tags": [],
        "children": [],
        "fields": {},
    }
    for pattern, key in EMOJI_FIELDS:
        m = pattern.search(text)
        if m:
            d = date_from_string(m.group(1))
            if d:
                task[key] = d
    if "⏫" in text:
        task["priority"] = "high"
    elif "🔼" in text:
        task["priority"] = "medium"
    elif "🔽" in text:
        task["priority"] = "low"
    rec = RECURRENCE_RE.search(text)
    if rec:
        task["recurrence"] = rec.group(1).strip()
    for m in TAG_RE.finditer(text):
        task["tags"].append("#" + m.group(1))
    for m in INLINE_FIELD_RE.finditer(text):
        task["fields"][m.group(1).strip()] = coerce_value(m.group(2))
    return task


# ---------------------------------------------------------------- one page

class Page:
    """A parsed note. Plain attributes, because everything reads it and nothing
    but the parser writes it."""

    __slots__ = ("path", "name", "folder", "ext", "fields", "tags", "etags",
                 "aliases", "tasks", "lists", "outlinks", "headings", "size",
                 "ctime", "mtime", "day")

    def __init__(self, **kw: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))


def parse_page(rel: str, raw_input: str, stat: dict) -> Page:
    """Take a note apart.

    The first thing that happens is that CRLF goes, and it has to: a pattern that
    ends in `(.*)$` never matches a line that still carries a carriage return,
    which silently dropped every task and every list item in a file written on
    Windows. The line count does not change, so the line numbers a task is
    written back by stay right.
    """
    raw = raw_input.replace("\r\n", "\n") if "\r\n" in raw_input else raw_input
    if raw.startswith("﻿"):
        raw = raw[1:]
    frontmatter, body = split_frontmatter(raw)
    fm_lines = raw[:len(raw) - len(body)].count("\n")

    fields: dict[str, Value] = {}
    for k, v in frontmatter.items():
        fields[str(k)] = coerce_value(v, True)

    raw_lines = body.split("\n")
    lines = mask_fences(raw_lines)

    tags: dict[str, None] = {}
    etags: dict[str, None] = {}
    outlinks: list[dict] = []
    flat_tasks: list[dict] = []
    lists: list[dict] = []
    headings: list[dict] = []
    section: str | None = None

    for i, line in enumerate(lines):
        if not line:
            continue
        line_no = i + fm_lines

        h = HEADING_RE.match(line)
        if h:
            headings.append({"heading": h.group(2), "level": len(h.group(1)),
                             "line": line_no})
            section = h.group(2)
            continue
        for m in TAG_RE.finditer(line):
            tags["#" + m.group(1)] = None
            etags["#" + m.group(1)] = None
        for m in WIKILINK_RE.finditer(line):
            outlinks.append(make_link(m.group(2), m.group(1) == "!"))

        t = TASK_RE.match(line)
        if t:
            task = parse_task(rel, line_no, t.group(1), t.group(2), t.group(3))
            if section is not None:
                task["section"] = section
            flat_tasks.append(task)
            continue
        li = LIST_RE.match(line)
        if li:
            lists.append({"kind": "list-item", "text": li.group(2).strip(),
                          "line": line_no,
                          "level": len(li.group(1).replace("\t", "    ")) // 2,
                          "path": rel})
        # Inline fields: bracketed anywhere, or a bare `Key:: Value` line.
        saw_inline = False
        for m in INLINE_FIELD_RE.finditer(line):
            fields[m.group(1).strip()] = coerce_value(m.group(2))
            saw_inline = True
        if not saw_inline:
            lf = LINE_FIELD_RE.match(line)
            if lf and "[[" not in lf.group(1):
                fields[lf.group(1).strip()] = coerce_value(lf.group(2))

    # Nest subtasks under their parent by indentation.
    roots: list[dict] = []
    stack: list[dict] = []
    for t in flat_tasks:
        while stack and stack[-1]["level"] >= t["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(t)
        else:
            roots.append(t)
        stack.append(t)

    def mark_fully(t: dict) -> bool:
        kids = [mark_fully(c) for c in t["children"]]
        t["fullyCompleted"] = t["completed"] and all(kids)
        return t["fullyCompleted"]

    for t in roots:
        mark_fully(t)

    fm_tags = frontmatter.get("tags", frontmatter.get("tag"))
    def add_tag(x: Any) -> None:
        s = str(x).strip().lstrip("#")
        if s:
            tags["#" + s] = None
    if isinstance(fm_tags, list):
        for x in fm_tags:
            add_tag(x)
    elif isinstance(fm_tags, str):
        for x in re.split(r"[,\s]+", fm_tags):
            add_tag(x)

    aliases: list[str] = []
    fm_aliases = frontmatter.get("aliases", frontmatter.get("alias"))
    if isinstance(fm_aliases, list):
        aliases = [str(a) for a in fm_aliases]
    elif isinstance(fm_aliases, str):
        aliases = [fm_aliases]

    name = NOTE_SUFFIX_RE.sub("", PurePosixPath(rel).name)
    folder = rel[:rel.rfind("/")] if "/" in rel else ""
    suffix = PurePosixPath(rel).suffix

    # `file.day`: a date somebody wrote wins over one that is only in the name.
    day = None
    day_field = fields.get("day", fields.get("date"))
    if isinstance(day_field, dict) and day_field.get("kind") == "date":
        day = day_field
    if day is None:
        m = DAY_IN_NAME_RE.search(name)
        if m:
            day = date_from_string(m.group(1))

    return Page(path=rel, name=name, folder=folder, ext=suffix.lstrip("."),
                fields=fields, tags=list(tags), etags=list(etags), aliases=aliases,
                tasks=roots, lists=lists, outlinks=outlinks, headings=headings,
                size=stat["size"], ctime=date_from_ms(stat["ctimeMs"]),
                mtime=date_from_ms(stat["mtimeMs"]), day=day)


# ------------------------------------------------------------------- index

def name_key(p: str) -> str:
    base = p[p.rfind("/") + 1:]
    return NOTE_SUFFIX_RE.sub("", base).lower()


class PageIndex:
    """Every note of one vault, and the two lookups queries need.

    `by_name` is what makes `[[Meeting]]` find the note wherever it lies, and
    `inlinks` is the same graph read backwards. Both are kept as the index is
    updated rather than recomputed, because both are asked about per row.
    """

    def __init__(self) -> None:
        self.pages: dict[str, Page] = {}
        self.by_name: dict[str, list[str]] = {}
        self.inlinks: dict[str, dict[str, None]] = {}

    # -- names

    def _add_name(self, p: str) -> None:
        k = name_key(p)
        arr = self.by_name.setdefault(k, [])
        if p not in arr:
            arr.append(p)

    def _drop_name(self, p: str) -> None:
        k = name_key(p)
        arr = self.by_name.get(k)
        if not arr:
            return
        if p in arr:
            arr.remove(p)
        if not arr:
            del self.by_name[k]

    def resolve(self, target: str) -> str | None:
        clean = NOTE_SUFFIX_RE.sub("", target)
        if clean + ".md" in self.pages:
            return clean + ".md"
        if target in self.pages:
            return target
        hits = self.by_name.get(name_key(clean))
        if hits:
            if "/" in clean:
                # A link that carries folders means that one, if it is there.
                for h in hits:
                    if NOTE_SUFFIX_RE.sub("", h) == clean:
                        return h
            return hits[0]
        return None

    # -- inlinks

    def _link_targets(self, page: Page) -> set[str]:
        out: set[str] = set()
        for link in page.outlinks:
            p = self.resolve(link["target"])
            if p:
                out.add(p)

        def walk(x: Value) -> None:
            if is_link(x):
                p = self.resolve(x["target"])
                if p:
                    out.add(p)
            elif isinstance(x, list):
                for item in x:
                    walk(item)

        for v in page.fields.values():
            walk(v)
        return out

    def _apply_inlinks(self, page: Page, remove: bool) -> None:
        for t in self._link_targets(page):
            bucket = self.inlinks.get(t)
            if bucket is None:
                if remove:
                    continue
                bucket = {}
                self.inlinks[t] = bucket
            if remove:
                bucket.pop(page.path, None)
            else:
                bucket[page.path] = None

    # -- upkeep

    def put(self, page: Page) -> None:
        old = self.pages.get(page.path)
        if old is not None:
            self._apply_inlinks(old, True)
        self.pages[page.path] = page
        self._add_name(page.path)
        self._apply_inlinks(page, False)

    def remove(self, rel: str) -> None:
        old = self.pages.pop(rel, None)
        if old is not None:
            self._apply_inlinks(old, True)
        self._drop_name(rel)
        self.inlinks.pop(rel, None)

    def build(self, entries: list[tuple[str, str, dict]]) -> None:
        """Read the whole vault. `entries` is (path, text, stat) per note.

        Links are resolved only once every name is known: a note that points at
        one read later would otherwise not resolve, and the graph would depend on
        the order the disk handed the files over.
        """
        self.pages.clear()
        self.by_name.clear()
        self.inlinks.clear()
        for rel, raw, stat in entries:
            try:
                self.pages[rel] = parse_page(rel, raw, stat)
                self._add_name(rel)
            except Exception:                     # noqa: BLE001 - see below
                # One note that cannot be read must not take the index with it.
                # It is left out, which is visible; raising would leave the vault
                # without any answers at all.
                continue
        for page in list(self.pages.values()):
            self._apply_inlinks(page, False)

    # -- asking

    def get(self, rel: str) -> Page | None:
        return self.pages.get(rel)

    def by_link(self, target: str) -> Page | None:
        p = self.resolve(target)
        return self.pages.get(p) if p else None

    def all(self) -> list[Page]:
        return list(self.pages.values())

    def inlinks_of(self, rel: str) -> list[dict]:
        return [make_link(p) for p in self.inlinks.get(rel, {})]
