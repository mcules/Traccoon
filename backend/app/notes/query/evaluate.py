"""Does this note answer that query, and where.

Everything a query can ask about is prepared once per note — its text, its lines,
its sections, its blocks, its tasks, its properties — and the parsed tree is
walked against it. Matches carry their offsets so a result can be shown in the
sentence it sits in.

Translated from the implementation the notes were written against. Four places
where the two languages disagree quietly, and all four are handled rather than
inherited:

  * `\\b` and `\\w` are ASCII there and Unicode here. A phrase ending in a German
    word would find one boundary in one language and not in the other, so the
    boundaries are written out as character classes instead of using `\\b`.
  * `String(true)` is `"true"` there and `"True"` here. A property test against a
    boolean would compare against the wrong word.
  * `Number("")` is `0` there and an exception here, `Number("abc")` is `NaN`
    there. A comparison against a missing value has to come out false, not blow
    up.
  * A regular expression somebody wrote for the other engine may not compile in
    this one. That is not an error either: it matches nothing, the same as there.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from .search import (And, Cmp, Const, Node, Not, Op, Or, Phrase, Prop, Regex,
                     Text)

# The word characters of the other language, written out. Used instead of `\b`
# so case-insensitivity stays Unicode while the boundary stays ASCII.
WORD = "A-Za-z0-9_"
LEFT_BOUNDARY = f"(?<![{WORD}])"
RIGHT_BOUNDARY = f"(?![{WORD}])"


@dataclass
class Doc:
    path: str
    filename: str
    content: str
    tags: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Range:
    start: int
    end: int


@dataclass
class Ctx:
    doc: Doc
    scope: Range
    case_sensitive: bool = False
    target: str = "both"          # both | content | filename | path
    hits: list[Range] = field(default_factory=list)


# ------------------------------------------------------- the other language's
# ---------------------------------------------------------- idea of a value

def js_str(value: Any) -> str:
    """What the other language would have printed for this value."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def js_number(value: Any) -> float:
    """`Number(x)`: empty is zero, nonsense is not-a-number, never an exception."""
    if value is None or value is False:
        return 0.0
    if value is True:
        return 1.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return math.nan


# ------------------------------------------------------------------ regions

HEADING_LINE = re.compile(r"^#{1,6}\s.*$", re.M)
BLANK_LINE = re.compile(r"\n[ \t]*\n")
TASK_LINE = re.compile(r"^[ \t]*[-*+] \[(.)\].*$", re.M)


def sections(content: str) -> list[Range]:
    """What lies under one heading, as `section:` understands it."""
    starts = [m.start() for m in HEADING_LINE.finditer(content)]
    if not starts:
        return [Range(0, len(content))]
    out: list[Range] = []
    if starts[0] > 0:
        out.append(Range(0, starts[0]))
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(content)
        out.append(Range(start, end))
    return out


def blocks(content: str) -> list[Range]:
    """Paragraphs and list items, separated by a blank line."""
    out: list[Range] = []
    start = 0
    for m in BLANK_LINE.finditer(content):
        out.append(Range(start, m.start()))
        start = m.end()
    out.append(Range(start, len(content)))
    return out


def lines(content: str) -> list[Range]:
    out: list[Range] = []
    start = 0
    for i, ch in enumerate(content):
        if ch == "\n":
            out.append(Range(start, i))
            start = i + 1
    out.append(Range(start, len(content)))
    return out


def tasks(content: str) -> list[tuple[Range, str]]:
    return [(Range(m.start(), m.end()), m.group(1)) for m in TASK_LINE.finditer(content)]


# ------------------------------------------------------------------ matching

def _flags(ctx: Ctx) -> int:
    return re.M if ctx.case_sensitive else (re.M | re.I)


def _compile(node: Node, ctx: Ctx, *, boundaries: bool) -> re.Pattern | None:
    try:
        if isinstance(node, Regex):
            return re.compile(node.source, _flags(ctx))
        value = node.value                                    # type: ignore[union-attr]
        core = re.escape(value)
        if boundaries and isinstance(node, Phrase):
            left = LEFT_BOUNDARY if value[:1] and re.match(f"[{WORD}]", value[0]) else ""
            right = RIGHT_BOUNDARY if value[-1:] and re.match(f"[{WORD}]", value[-1]) else ""
            core = f"{left}{core}{right}"
        return re.compile(core, _flags(ctx))
    except re.error:
        # A pattern written for the other engine that this one will not take.
        # It matches nothing, which is what happened there too.
        return None


def _find_all(text: str, pattern: re.Pattern, offset: int, hits: list[Range]) -> bool:
    found = False
    for m in pattern.finditer(text):
        if m.end() == m.start():
            continue                     # an empty match says nothing and never ends
        found = True
        hits.append(Range(offset + m.start(), offset + m.end()))
    return found


def match_literal(node: Node, ctx: Ctx) -> bool:
    with_boundaries = _compile(node, ctx, boundaries=True)
    if with_boundaries is None:
        return False
    found = False
    if ctx.target in ("both", "content"):
        text = ctx.doc.content[ctx.scope.start:ctx.scope.end]
        found = _find_all(text, with_boundaries, ctx.scope.start, ctx.hits) or found
    if ctx.target in ("both", "filename"):
        # In a file name a phrase matches plainly: a name is not prose and its
        # word boundaries are not where a reader would put them.
        plain = _compile(node, ctx, boundaries=False)
        if plain is not None and plain.search(ctx.doc.filename):
            found = True
    if ctx.target == "path":
        plain = _compile(node, ctx, boundaries=False)
        if plain is not None and plain.search(ctx.doc.path):
            found = True
    return found


def any_region(regions: Iterable[Range], node: Node, ctx: Ctx) -> bool:
    found = False
    for r in regions:
        if r.end <= ctx.scope.start or r.start >= ctx.scope.end:
            continue
        clipped = Range(max(r.start, ctx.scope.start), min(r.end, ctx.scope.end))
        if evaluate(node, replace(ctx, scope=clipped)):
            found = True
    return found


def property_matches(value: Any, node: Node | None, ctx: Ctx) -> bool:
    if node is None:
        return True
    values = value if isinstance(value, list) else [value]
    for v in values:
        if isinstance(node, Const):
            if node.value == "true" and (v is True or v == "true"):
                return True
            if node.value == "false" and (v is False or v == "false"):
                return True
            if node.value == "empty" and (v is None or v == ""):
                return True
            continue
        if isinstance(node, Cmp):
            target = js_number(getattr(node.item, "value", None))
            num = js_number(v)
            if math.isnan(target) or math.isnan(num):
                continue
            if (num > target) if node.dir == ">" else (num < target):
                return True
            continue
        text = js_str(v)
        if isinstance(node, Regex):
            try:
                if re.search(node.source, text, 0 if ctx.case_sensitive else re.I):
                    return True
            except re.error:
                pass
            continue
        needle = getattr(node, "value", "")
        if ctx.case_sensitive:
            if needle in text:
                return True
        elif needle.lower() in text.lower():
            return True
    return False


def evaluate(node: Node, ctx: Ctx) -> bool:
    if isinstance(node, And):
        return all(evaluate(n, ctx) for n in node.items)
    if isinstance(node, Or):
        # Stops at the first branch that answers, exactly as the original does.
        # Walking the rest would collect highlights the other side never showed,
        # and this is a translation, not an improvement.
        return any(evaluate(n, ctx) for n in node.items)
    if isinstance(node, Not):
        # A negation must not leave highlights of its own behind.
        return not evaluate(node.item, replace(ctx, hits=[]))
    if isinstance(node, Const):
        return node.value != "false"
    if isinstance(node, (Text, Phrase, Regex)):
        return match_literal(node, ctx)
    if isinstance(node, Cmp):
        return evaluate(node.item, ctx)
    if isinstance(node, Prop):
        for key, value in ctx.doc.frontmatter.items():
            probe = replace(ctx, hits=[], target="filename",
                            doc=replace(ctx.doc, filename=key))
            if not evaluate(node.name, probe):
                continue
            if property_matches(value, node.value, ctx):
                return True
        return False
    if isinstance(node, Op):
        return _operator(node, ctx)
    return False


def _operator(node: Op, ctx: Ctx) -> bool:
    op = node.op
    if op == "match-case":
        return evaluate(node.item, replace(ctx, case_sensitive=True))
    if op == "ignore-case":
        return evaluate(node.item, replace(ctx, case_sensitive=False))
    if op == "path":
        return evaluate(node.item, replace(ctx, target="path"))
    if op == "file":
        return evaluate(node.item, replace(ctx, target="filename"))
    if op == "content":
        return evaluate(node.item, replace(ctx, target="content"))
    if op == "line":
        return any_region(lines(ctx.doc.content), node.item, replace(ctx, target="content"))
    if op == "block":
        return any_region(blocks(ctx.doc.content), node.item, replace(ctx, target="content"))
    if op == "section":
        return any_region(sections(ctx.doc.content), node.item, replace(ctx, target="content"))
    if op in ("task", "task-todo", "task-done"):
        alle = tasks(ctx.doc.content)
        if op == "task-todo":
            alle = [t for t in alle if t[1] == " "]
        elif op == "task-done":
            alle = [t for t in alle if t[1] != " "]
        # `task:""` only asks whether there is such a task at all.
        leer = isinstance(node.item, Text) and not node.item.value
        if leer:
            ctx.hits.extend(r for r, _ in alle)
            return bool(alle)
        return any_region([r for r, _ in alle], node.item, replace(ctx, target="content"))
    if op == "tag":
        needle = getattr(node.item, "value", "") or ""
        bare = needle.lstrip("#").lower()
        if not bare:
            return bool(ctx.doc.tags)
        # `tag:#a` matches `#a/b`: a tag stands for everything under it.
        return any(t.lstrip("#").lower() == bare or t.lstrip("#").lower().startswith(bare + "/")
                   for t in ctx.doc.tags)
    return evaluate(node.item, ctx)


def match_doc(node: Node, doc: Doc) -> tuple[bool, list[Range]]:
    """Run a parsed query against one note."""
    hits: list[Range] = []
    ctx = Ctx(doc=doc, scope=Range(0, len(doc.content)), hits=hits)
    matched = evaluate(node, ctx)
    hits.sort(key=lambda r: r.start)
    return matched, hits
