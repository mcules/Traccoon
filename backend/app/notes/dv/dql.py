"""The query language of the notes: parsed, not approximated.

A block of

    TABLE file.mtime AS "Changed" FROM "02 Projects" WHERE status != "done"
    SORT file.name ASC LIMIT 20

is a real query, and about a thousand of them are written into this vault. The
shape is fixed —

    (TABLE [WITHOUT ID] columns | LIST [expr] | TASK | CALENDAR expr)
    [FROM source] (WHERE | SORT | GROUP BY | FLATTEN | LIMIT)*

— and the clauses run in the order they were written, so a `WHERE` after a
`FLATTEN` sees the flattened rows and not the ones before. That is not a detail:
it is the difference between "notes with an overdue task" and "overdue tasks".

Translated line for line from the implementation these queries were written
against. Where the two languages disagree the difference is named in `js.py` and
used here; nothing is decided differently on the way.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any, Callable

from .js import js_num_str, json_key
from .pages import Page, PageIndex
from .values import (
    Value, compare, equals, is_date, is_duration, is_link, is_number, is_task,
    make_link, to_str, truthy,
)

# ---------------------------------------------------------------- tokenizer

OPS = ["<=", ">=", "!=", "==", "=>", "&&", "||", "=", "<", ">", "+", "-", "*",
       "/", "%", "(", ")", "[", "]", "{", "}", ",", ".", ":", "!"]

NUMBER_RE = re.compile(r"^[0-9]+(\.[0-9]+)?")
IDENT_RE = re.compile(r"^(?:[^\W\d]|\$)[\w$]*")
TAG_RE = re.compile(r"^#([\w\-/]+)")
SPACE_RE = re.compile(r"\s")
IDENT_START_RE = re.compile(r"(?:[^\W\d]|\$)")


class QueryError(Exception):
    """A query that cannot be read. Its text reaches the person who wrote it."""


@dataclass
class Tok:
    t: str
    v: Any = None
    embed: bool = False


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if SPACE_RE.match(c):
            i += 1
            continue
        if c == '"' or c == "'":
            j = i + 1
            s = ""
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    s += src[j + 1]
                    j += 2
                else:
                    s += src[j]
                    j += 1
            out.append(Tok("str", s))
            i = j + 1
            continue
        if src.startswith("[[", i) or src.startswith("![[", i):
            embed = c == "!"
            start = i + (3 if embed else 2)
            end = src.find("]]", start)
            if end < 0:
                raise QueryError("unclosed [[link]]")
            out.append(Tok("link", src[start:end], embed))
            i = end + 2
            continue
        if c == "#":
            m = TAG_RE.match(src[i:])
            if m:
                out.append(Tok("tag", m.group(1)))
                i += len(m.group(0))
                continue
        if c.isdigit() and c.isascii():
            m = NUMBER_RE.match(src[i:])
            text = m.group(0)
            out.append(Tok("num", float(text) if "." in text else int(text)))
            i += len(text)
            continue
        if IDENT_START_RE.match(c):
            m = IDENT_RE.match(src[i:])
            out.append(Tok("id", m.group(0)))
            i += len(m.group(0))
            continue
        op = next((o for o in OPS if src.startswith(o, i)), None)
        if op:
            out.append(Tok("op", op))
            i += len(op)
            continue
        raise QueryError(f"unexpected character '{c}'")
    out.append(Tok("end"))
    return out


# ---------------------------------------------------------------------- ast

@dataclass
class Lit:
    v: Value


@dataclass
class Var:
    name: str


@dataclass
class Field:
    obj: Any
    name: str


@dataclass
class Index:
    obj: Any
    idx: Any


@dataclass
class Call:
    name: str
    args: list


@dataclass
class Bin:
    op: str
    l: Any
    r: Any


@dataclass
class Un:
    op: str
    e: Any


@dataclass
class ListExpr:
    items: list


@dataclass
class ObjExpr:
    entries: list


@dataclass
class Lambda:
    params: list[str]
    body: Any


@dataclass
class Source:
    """Where a query looks: a folder, a tag, links in or out, or all of them."""
    s: str
    path: str = ""
    tag: str = ""
    target: str = ""
    l: Any = None
    r: Any = None
    e: Any = None


@dataclass
class Step:
    k: str
    e: Any = None
    keys: list = field(default_factory=list)
    as_: str | None = None
    n: int = 0


@dataclass
class Query:
    type: str = "TABLE"
    without_id: bool = False
    cols: list = field(default_factory=list)
    list_expr: Any = None
    source: Source | None = None
    steps: list = field(default_factory=list)


HEADS = ("TABLE", "LIST", "TASK", "CALENDAR")
CLAUSES = ("FROM", "WHERE", "SORT", "GROUP", "FLATTEN", "LIMIT")


class Parser:
    def __init__(self, toks: list[Tok]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_id(self, word: str) -> bool:
        t = self.peek()
        return t.t == "id" and str(t.v).upper() == word

    def eat_id(self, word: str) -> bool:
        if self.is_id(word):
            self.i += 1
            return True
        return False

    def is_op(self, v: str) -> bool:
        t = self.peek()
        return t.t == "op" and t.v == v

    def eat_op(self, v: str) -> bool:
        if self.is_op(v):
            self.i += 1
            return True
        return False

    def at_clause(self) -> bool:
        """True where an expression ends: on a clause keyword or at the end."""
        t = self.peek()
        if t.t == "end":
            return True
        if t.t != "id":
            return False
        return str(t.v).upper() in CLAUSES

    # -- the query

    def parse_query(self) -> Query:
        q = Query()
        head = self.peek()
        if head.t == "id" and str(head.v).upper() in HEADS:
            q.type = str(head.v).upper()
            self.i += 1
        else:
            q.type = "LIST"

        if q.type == "TABLE":
            if self.is_id("WITHOUT"):
                self.i += 1
                self.eat_id("ID")
                q.without_id = True
            while not self.at_clause():
                e = self.parse_expr()
                name = expr_label(e)
                if self.eat_id("AS"):
                    t = self.next()
                    if t.t in ("str", "id"):
                        name = str(t.v)
                q.cols.append({"e": e, "name": name})
                if not self.eat_op(","):
                    break
        elif q.type in ("LIST", "CALENDAR"):
            if not self.at_clause():
                q.list_expr = self.parse_expr()

        while True:
            if self.eat_id("FROM"):
                q.source = self.parse_source()
                continue
            if self.eat_id("WHERE"):
                q.steps.append(Step("where", e=self.parse_expr()))
                continue
            if self.eat_id("SORT"):
                keys = []
                while True:
                    e = self.parse_expr()
                    direction = 1
                    if self.eat_id("DESC"):
                        direction = -1
                    else:
                        self.eat_id("ASC")
                    keys.append({"e": e, "dir": direction})
                    if not self.eat_op(","):
                        break
                q.steps.append(Step("sort", keys=keys))
                continue
            if self.is_id("GROUP"):
                self.i += 1
                self.eat_id("BY")
                e = self.parse_expr()
                as_ = None
                if self.eat_id("AS"):
                    t = self.next()
                    as_ = str(t.v) if t.t in ("str", "id") else None
                q.steps.append(Step("group", e=e, as_=as_))
                continue
            if self.eat_id("FLATTEN"):
                e = self.parse_expr()
                as_ = None
                if self.eat_id("AS"):
                    t = self.next()
                    as_ = str(t.v) if t.t in ("str", "id") else None
                q.steps.append(Step("flatten", e=e, as_=as_))
                continue
            if self.eat_id("LIMIT"):
                t = self.next()
                q.steps.append(Step("limit", n=int(t.v) if t.t == "num" else 0))
                continue
            break
        return q

    # -- sources

    def parse_source(self) -> Source:
        left = self.parse_source_and()
        while self.is_id("OR") or self.is_op("||"):
            self.i += 1
            left = Source("or", l=left, r=self.parse_source_and())
        return left

    def parse_source_and(self) -> Source:
        left = self.parse_source_atom()
        while self.is_id("AND") or self.is_op("&&"):
            self.i += 1
            left = Source("and", l=left, r=self.parse_source_atom())
        return left

    def parse_source_atom(self) -> Source:
        if self.eat_op("-") or self.eat_id("NOT") or self.eat_op("!"):
            return Source("not", e=self.parse_source_atom())
        if self.eat_op("("):
            s = self.parse_source()
            self.eat_op(")")
            return s
        t = self.next()
        if t.t == "str":
            text = str(t.v)
            return Source("all") if text.strip() == "" else Source(
                "folder", path=re.sub(r"/+$", "", text))
        if t.t == "tag":
            return Source("tag", tag="#" + str(t.v))
        if t.t == "link":
            return Source("incoming",
                          target=str(t.v).split("|")[0].split("#")[0].strip())
        if t.t == "id" and str(t.v).lower() == "outgoing":
            self.eat_op("(")
            inner = self.next()
            target = str(inner.v) if inner.t in ("link", "str") else ""
            self.eat_op(")")
            return Source("outgoing",
                          target=target.split("|")[0].split("#")[0].strip())
        if t.t == "id" and str(t.v).lower() == "csv":
            raise QueryError("FROM csv() is not supported")
        return Source("all")

    # -- expressions, by precedence

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        l = self.parse_and()
        while self.is_id("OR") or self.is_op("||"):
            self.i += 1
            l = Bin("or", l, self.parse_and())
        return l

    def parse_and(self):
        l = self.parse_compare()
        while self.is_id("AND") or self.is_op("&&"):
            self.i += 1
            l = Bin("and", l, self.parse_compare())
        return l

    def parse_compare(self):
        l = self.parse_add()
        while True:
            t = self.peek()
            if t.t == "op" and t.v in ("=", "==", "!=", "<", ">", "<=", ">="):
                self.i += 1
                l = Bin("=" if t.v == "==" else str(t.v), l, self.parse_add())
                continue
            break
        return l

    def parse_add(self):
        l = self.parse_mul()
        while True:
            t = self.peek()
            if t.t == "op" and t.v in ("+", "-"):
                self.i += 1
                l = Bin(str(t.v), l, self.parse_mul())
                continue
            break
        return l

    def parse_mul(self):
        l = self.parse_unary()
        while True:
            t = self.peek()
            if t.t == "op" and t.v in ("*", "/", "%"):
                self.i += 1
                l = Bin(str(t.v), l, self.parse_unary())
                continue
            break
        return l

    def parse_unary(self):
        if self.is_op("!") or self.is_id("NOT"):
            self.i += 1
            return Un("!", self.parse_unary())
        if self.is_op("-"):
            self.i += 1
            return Un("-", self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            if self.eat_op("."):
                t = self.next()
                e = Field(e, js_string(t.v) if t.v is not None else "")
                continue
            if self.is_op("["):
                self.i += 1
                idx = self.parse_expr()
                self.eat_op("]")
                e = Index(e, idx)
                continue
            if self.is_op("(") and isinstance(e, Var):
                self.i += 1
                args = []
                if not self.is_op(")"):
                    while True:
                        args.append(self.parse_expr())
                        if not self.eat_op(","):
                            break
                self.eat_op(")")
                e = Call(e.name, args)
                continue
            break
        return e

    def parse_primary(self):
        t = self.next()
        if t.t == "num":
            return Lit(t.v)
        if t.t == "str":
            return Lit(t.v)
        if t.t == "link":
            return Lit(make_link(str(t.v), t.embed))
        if t.t == "tag":
            return Lit("#" + str(t.v))
        if t.t == "op" and t.v == "(":
            # Either `(a, b) => expr` or a parenthesised expression. Which one
            # only shows at the arrow, so the position is kept and rewound.
            save = self.i
            params: list[str] = []
            is_lambda = True
            if not self.is_op(")"):
                while True:
                    p = self.next()
                    if p.t != "id":
                        is_lambda = False
                        break
                    params.append(str(p.v))
                    if not self.eat_op(","):
                        break
            if is_lambda and self.eat_op(")") and self.eat_op("=>"):
                return Lambda(params, self.parse_expr())
            self.i = save
            e = self.parse_expr()
            self.eat_op(")")
            return e
        if t.t == "op" and t.v == "[":
            items = []
            if not self.is_op("]"):
                while True:
                    items.append(self.parse_expr())
                    if not self.eat_op(","):
                        break
            self.eat_op("]")
            return ListExpr(items)
        if t.t == "op" and t.v == "{":
            entries = []
            if not self.is_op("}"):
                while True:
                    k = self.next()
                    self.eat_op(":")
                    entries.append((str(k.v) if k.v is not None else "",
                                    self.parse_expr()))
                    if not self.eat_op(","):
                        break
            self.eat_op("}")
            return ObjExpr(entries)
        if t.t == "id":
            u = str(t.v).lower()
            if u == "null":
                return Lit(None)
            if u == "true":
                return Lit(True)
            if u == "false":
                return Lit(False)
            if self.is_op("=>"):
                # One parameter may stand without brackets: `x => x + 1`.
                self.i += 1
                return Lambda([str(t.v)], self.parse_expr())
            return Var(str(t.v))
        raise QueryError("unexpected token in expression")


def js_string(v: Value) -> str:
    """`String(v)`: null is the word, not the empty string.

    `to_str` is the other one — what a value reads as inside a sentence, where a
    missing value contributes nothing. Both exist there and they are not
    interchangeable: one decides what a person sees, the other decides which
    rows land in the same group.
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if is_number(v):
        return js_num_str(v)
    if isinstance(v, str):
        return v
    return to_str(v)


def expr_label(e: Any) -> str:
    """The column heading when the query gives no `AS` name."""
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Field):
        return f"{expr_label(e.obj)}.{e.name}"
    if isinstance(e, Call):
        return f"{e.name}({', '.join(expr_label(a) for a in e.args)})"
    if isinstance(e, Lit):
        return to_str(e.v)
    if isinstance(e, Bin):
        return f"{expr_label(e.l)} {e.op} {expr_label(e.r)}"
    if isinstance(e, Index):
        return f"{expr_label(e.obj)}[{expr_label(e.idx)}]"
    return "expr"


COMMENT_RE = re.compile(r"^\s*//.*$")


def parse_query(src: str) -> Query:
    """Read a query. Line comments go first, so a documented one still parses."""
    clean = "\n".join(COMMENT_RE.sub("", line) for line in src.split("\n"))
    clean = re.sub(r";\s*$", "", clean)
    return Parser(tokenize(clean)).parse_query()


# --------------------------------------------------------------- evaluation

@dataclass
class Row:
    page: Page
    item: dict | None = None
    vars: dict = field(default_factory=dict)


@dataclass
class Ctx:
    index: PageIndex
    row: Row
    this_page: Page | None = None


def file_object(index: PageIndex, p: Page) -> dict:
    """The `file` of a page, built when asked for."""
    link = make_link(p.path)
    link["display"] = p.name
    return {
        "name": p.name, "path": p.path, "folder": p.folder, "ext": p.ext,
        "link": link, "size": p.size,
        "ctime": p.ctime, "cday": p.ctime, "mtime": p.mtime, "mday": p.mtime,
        "day": p.day if p.day is not None else None,
        "tags": p.tags, "etags": p.etags, "aliases": p.aliases,
        "tasks": p.tasks, "lists": p.lists, "outlinks": p.outlinks,
        "inlinks": index.inlinks_of(p.path), "frontmatter": p.fields,
        "starred": False,
    }


def page_value(index: PageIndex, p: Page, key: str) -> Value:
    if key == "file":
        return file_object(index, p)
    if key in p.fields:
        return p.fields[key]
    return None


_TASK_KEYS = {
    "text": "text", "status": "status", "completed": "completed",
    "fullyCompleted": "fullyCompleted", "checked": "checked", "path": "path",
    "line": "line", "tags": "tags",
}
_TASK_OPTIONAL = ("section", "due", "done", "scheduled", "start", "created",
                  "priority")

_MISSING = object()


def task_value(t: dict, key: str) -> Any:
    """A field of a task, or `_MISSING` when the task has no such thing.

    Missing and null are two different answers here: a name the task does not
    know falls through to the page's own fields, and a name it knows but has no
    value for is null and stops there.
    """
    if key in _TASK_KEYS:
        return t[key]
    if key in ("subtasks", "children"):
        return t["children"]
    if key in _TASK_OPTIONAL:
        return t.get(key, None)
    if key == "link":
        return make_link(t["path"])
    if key in t["fields"]:
        return t["fields"][key]
    return _MISSING


def evaluate(e: Any, ctx: Ctx) -> Value:
    if isinstance(e, Lit):
        return e.v
    if isinstance(e, ListExpr):
        return [evaluate(x, ctx) for x in e.items]
    if isinstance(e, ObjExpr):
        return {k: evaluate(v, ctx) for k, v in e.entries}
    if isinstance(e, Lambda):
        # A lambda only means something as an argument; the function library
        # reaches the tree through the argument itself, not through this.
        return {"__lambda": True}
    if isinstance(e, Var):
        row, this_page = ctx.row, ctx.this_page
        if e.name == "this":
            if this_page is None:
                return None
            return {"file": file_object(ctx.index, this_page), **this_page.fields}
        if e.name in row.vars:
            return row.vars[e.name]
        if row.item is not None:
            tv = task_value(row.item, e.name)
            if tv is not _MISSING:
                return tv
        return page_value(ctx.index, row.page, e.name)
    if isinstance(e, Field):
        return field_of(evaluate(e.obj, ctx), e.name, ctx)
    if isinstance(e, Index):
        obj = evaluate(e.obj, ctx)
        idx = evaluate(e.idx, ctx)
        if isinstance(obj, list) and is_number(idx):
            i = int(idx)
            return obj[i] if 0 <= i < len(obj) else None
        return field_of(obj, to_str(idx), ctx)
    if isinstance(e, Call):
        from .functions import call_function
        return call_function(e.name, e.args, ctx, evaluate)
    if isinstance(e, Un):
        v = evaluate(e.e, ctx)
        if e.op == "!":
            return not truthy(v)
        if e.op == "-":
            return -v if is_number(v) else None
        return None
    if isinstance(e, Bin):
        return binop(e.op, e, ctx)
    return None


def field_of(obj: Value, name: str, ctx: Ctx) -> Value:
    if obj is None:
        return None
    if is_task(obj):
        v = task_value(obj, name)
        return None if v is _MISSING else v
    if is_link(obj):
        # `somelink.field` reaches through to the note it points at.
        p = ctx.index.by_link(obj["target"])
        if p is None:
            return None
        return page_value(ctx.index, p, name)
    if isinstance(obj, list):
        # `rows.file.link` takes the field of every element and flattens once.
        out: list = []
        for x in obj:
            v = field_of(x, name, ctx)
            if isinstance(v, list):
                out.extend(v)
            elif v is not None:
                out.append(v)
        return out
    if isinstance(obj, dict):
        return obj[name] if name in obj else None
    return None


def binop(op: str, e: Bin, ctx: Ctx) -> Value:
    if op == "and":
        return truthy(evaluate(e.r, ctx)) if truthy(evaluate(e.l, ctx)) else False
    if op == "or":
        return True if truthy(evaluate(e.l, ctx)) else truthy(evaluate(e.r, ctx))
    l = evaluate(e.l, ctx)
    r = evaluate(e.r, ctx)
    if op == "=":
        return equals(l, r)
    if op == "!=":
        return not equals(l, r)
    if op == "<":
        return compare(l, r) < 0
    if op == ">":
        return compare(l, r) > 0
    if op == "<=":
        return compare(l, r) <= 0
    if op == ">=":
        return compare(l, r) >= 0
    if op == "+":
        if is_number(l) and is_number(r):
            return l + r
        if is_date(l) and is_duration(r):
            return {"kind": "date", "ts": l["ts"] + r["ms"], "hasTime": l["hasTime"]}
        if is_duration(l) and is_duration(r):
            return {"kind": "duration", "ms": l["ms"] + r["ms"]}
        if isinstance(l, list) and isinstance(r, list):
            return [*l, *r]
        if l is None or r is None:
            return None if (l is None and r is None) else to_str(l) + to_str(r)
        return to_str(l) + to_str(r)
    if op == "-":
        if is_number(l) and is_number(r):
            return l - r
        if is_date(l) and is_date(r):
            return {"kind": "duration", "ms": l["ts"] - r["ts"]}
        if is_date(l) and is_duration(r):
            return {"kind": "date", "ts": l["ts"] - r["ms"], "hasTime": l["hasTime"]}
        if is_duration(l) and is_duration(r):
            return {"kind": "duration", "ms": l["ms"] - r["ms"]}
        return None
    if op == "*":
        if is_number(l) and is_number(r):
            return l * r
        if is_duration(l) and is_number(r):
            return {"kind": "duration", "ms": l["ms"] * r}
        return None
    if op == "/":
        if is_number(l) and is_number(r):
            return None if r == 0 else l / r
        if is_duration(l) and is_number(r):
            return None if r == 0 else {"kind": "duration", "ms": l["ms"] / r}
        return None
    if op == "%":
        if is_number(l) and is_number(r):
            if r == 0:
                return None
            # The remainder keeps the sign of the left side there; Python's `%`
            # keeps the sign of the right, so -1 % 3 would be 2 instead of -1.
            return math.fmod(l, r)
        return None
    return None


# ------------------------------------------------------------------ sources

def match_source(index: PageIndex, p: Page, s: Source) -> bool:
    if s.s == "all":
        return True
    if s.s == "folder":
        return s.path == "" or p.path == s.path or p.path.startswith(s.path + "/")
    if s.s == "tag":
        want = s.tag.lower()
        # A tag stands for everything under it: `#projekt` finds `#projekt/afu`.
        return any(t.lower() == want or t.lower().startswith(want + "/")
                   for t in p.tags)
    if s.s == "incoming":
        target = index.resolve(s.target)
        if not target:
            return False
        if any(index.resolve(l["target"]) == target for l in p.outlinks):
            return True
        return _link_fields_hit(index, p, target)
    if s.s == "outgoing":
        src = index.by_link(s.target)
        if src is None:
            return False
        return any(index.resolve(l["target"]) == p.path for l in src.outlinks)
    if s.s == "and":
        return match_source(index, p, s.l) and match_source(index, p, s.r)
    if s.s == "or":
        return match_source(index, p, s.l) or match_source(index, p, s.r)
    if s.s == "not":
        return not match_source(index, p, s.e)
    return False


def _link_fields_hit(index: PageIndex, p: Page, target: str) -> bool:
    """A link written in a property counts as a link, not only one in the text."""
    def walk(v: Value) -> bool:
        if is_link(v):
            return index.resolve(v["target"]) == target
        if isinstance(v, list):
            return any(walk(x) for x in v)
        return False
    return any(walk(v) for v in p.fields.values())


# ------------------------------------------------------------------- result

def _task_and_children(t: dict) -> list[dict]:
    out: list[dict] = []

    def walk(x: dict) -> None:
        out.append(x)
        for c in x["children"]:
            walk(c)
    walk(t)
    return out


def execute(index: PageIndex, src: str, source_path: str | None = None) -> dict:
    """Run one query block against the index. Never raises: a broken query is an
    answer of its own, and the person who wrote it is the one who can fix it."""
    try:
        q = parse_query(src)
    except QueryError as err:
        return {"kind": "error", "message": f"Could not read the query: {err}"}
    except Exception as err:                      # noqa: BLE001
        return {"kind": "error", "message": f"Could not read the query: {err}"}

    this_page = index.get(source_path) if source_path else None

    page_list = index.all()
    if q.source is not None:
        page_list = [p for p in page_list if match_source(index, p, q.source)]

    rows: list[Row] = []
    if q.type == "TASK":
        for p in page_list:
            for t in p.tasks:
                for x in _task_and_children(t):
                    rows.append(Row(page=p, item=x))
    else:
        rows = [Row(page=p) for p in page_list]

    groups: list[dict] | None = None

    def ctx_for(row: Row) -> Ctx:
        return Ctx(index=index, row=row, this_page=this_page)

    try:
        for step in q.steps:
            if groups is not None and step.k not in ("limit", "sort", "where"):
                # Grouping or flattening again after a GROUP BY is not carried
                # out. The vault does not do it, and guessing at it quietly
                # would be worse than leaving the step out visibly.
                continue
            if step.k == "where":
                if groups is not None:
                    groups = [g for g in groups
                              if truthy(evaluate(step.e, ctx_for(_group_row(index, g))))]
                else:
                    rows = [r for r in rows if truthy(evaluate(step.e, ctx_for(r)))]
            elif step.k == "sort":
                if groups is not None:
                    groups.sort(key=cmp_to_key(
                        lambda a, b: _cmp_keys(step.keys, _group_row(index, a),
                                               _group_row(index, b), ctx_for)))
                else:
                    rows.sort(key=cmp_to_key(
                        lambda a, b: _cmp_keys(step.keys, a, b, ctx_for)))
            elif step.k == "limit":
                if groups is not None:
                    groups = groups[:step.n]
                else:
                    rows = rows[:step.n]
            elif step.k == "flatten":
                out: list[Row] = []
                for r in rows:
                    v = evaluate(step.e, ctx_for(r))
                    items = v if isinstance(v, list) else [v]
                    if not items:
                        continue
                    for item in items:
                        variables = dict(r.vars)
                        if step.as_:
                            variables[step.as_] = item
                        out.append(Row(page=r.page,
                                       item=item if is_task(item) else r.item,
                                       vars=variables))
                rows = out
            elif step.k == "group":
                buckets: dict[str, dict] = {}
                for r in rows:
                    key = evaluate(step.e, ctx_for(r))
                    ident = _group_id(index, key)
                    g = buckets.get(ident)
                    if g is None:
                        g = {"key": key, "rows": []}
                        buckets[ident] = g
                    g["rows"].append(r)
                groups = list(buckets.values())
                if step.as_:
                    for g in groups:
                        for r in g["rows"]:
                            r.vars[step.as_] = g["key"]

        def project(rs: list[Row]) -> dict:
            return {
                "rows": [_cells(index, q, r, ctx_for(r)) for r in rs]
                        if q.type == "TABLE" else [],
                "items": [evaluate(q.list_expr, ctx_for(r)) if q.list_expr is not None
                          else file_object(index, r.page)["link"] for r in rs]
                         if q.type in ("LIST", "CALENDAR") else [],
                "tasks": [r.item for r in rs if r.item is not None]
                         if q.type == "TASK" else [],
            }

        headers = (([] if q.without_id else ["File"]) + [c["name"] for c in q.cols]
                   if q.type == "TABLE" else [])

        if groups is not None:
            gr = [{"key": g["key"], **project(g["rows"])} for g in groups]
            if q.type == "TASK":
                return {"kind": "task", "groups": gr}
            if q.type == "TABLE":
                return {"kind": "table", "headers": headers, "rows": [], "groups": gr}
            return {"kind": "list", "items": [], "groups": gr}

        p = project(rows)
        if q.type == "TASK":
            return {"kind": "task",
                    "groups": [{"key": None, "rows": [], "items": [],
                                "tasks": p["tasks"]}]}
        if q.type == "TABLE":
            return {"kind": "table", "headers": headers, "rows": p["rows"]}
        return {"kind": "list", "items": p["items"]}
    except Exception as err:                      # noqa: BLE001
        return {"kind": "error", "message": f"Could not evaluate: {err}"}


def _group_row(index: PageIndex, g: dict) -> Row:
    first: Row = g["rows"][0]
    return Row(page=first.page, item=first.item,
               vars={**first.vars, "key": g["key"],
                     "rows": [_row_as_value(index, r) for r in g["rows"]]})


def _row_as_value(index: PageIndex, r: Row) -> Value:
    if r.item is not None:
        return r.item
    return {"file": file_object(index, r.page), **r.page.fields}


def _cells(index: PageIndex, q: Query, r: Row, ctx: Ctx) -> list:
    out: list = []
    if not q.without_id:
        out.append(file_object(index, r.page)["link"])
    for c in q.cols:
        out.append(evaluate(c["e"], ctx))
    return out


def _cmp_keys(keys: list, a: Row, b: Row, ctx_for: Callable[[Row], Ctx]) -> int:
    for k in keys:
        c = compare(evaluate(k["e"], ctx_for(a)), evaluate(k["e"], ctx_for(b)))
        if c != 0:
            return c * k["dir"]
    return 0


def _group_id(index: PageIndex, v: Value) -> str:
    """What makes two group keys one group.

    Anything that is not an object is turned into text the way that language
    turns it into text — where `null` is the word "null" and not the empty
    string, which is what keeps a note without the property out of the group of
    notes whose property is empty.
    """
    if is_link(v):
        return "link:" + (index.resolve(v["target"]) or v["target"]).lower()
    if v is not None and isinstance(v, (dict, list)):
        return json_key(v)
    return js_string(v)


def evaluate_inline(index: PageIndex, expr: str, source_path: str | None = None) -> dict:
    """An inline `= expression`, run against the note it sits in.

    Both `= this.email` and `= email` work, because the note's own fields are in
    scope as well as its `this`.
    """
    page = index.get(source_path) if source_path else None
    if page is None:
        return {"ok": False, "error": "note is not in the index"}
    try:
        ast = Parser(tokenize(expr)).parse_expr()
        ctx = Ctx(index=index, row=Row(page=page), this_page=page)
        return {"ok": True, "value": evaluate(ast, ctx)}
    except Exception as err:                      # noqa: BLE001
        return {"ok": False, "error": str(err)}
