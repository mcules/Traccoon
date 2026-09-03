"""The formula language a table file filters and computes with.

This is not the query language. A table file has a small expression language of
its own: values, operators, and methods called on the value in front of them —
`note.price * 1.19`, `file.hasTag("project")`, `status.lower() == "done"`.
Formulas named in the file are values too, reachable as `formula.<name>`, and
they may build on one another.

Anything that cannot be worked out is null rather than an error: a table over a
few thousand notes will always contain notes that lack the property being asked
about, and a column of error messages helps nobody. A syntax error in the file
itself *is* reported, because that is a mistake somebody can fix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OPERATORS = ["&&", "||", "==", "!=", ">=", "<=", ">", "<", "+", "-", "*", "/", "%", "!"]
PUNCT = "().,[]?:"

# The identifier characters of the other side, written out: letters, digits, the
# underscore, the dollar sign and everything from the Latin-1 letters upwards.
IDENT_START = re.compile(r"[A-Za-z_$À-￿]")
IDENT_CHAR = re.compile(r"[A-Za-z0-9_$À-￿]")


class FormulaError(Exception):
    """A formula that cannot be read. Its text reaches whoever wrote the file."""


@dataclass
class Token:
    type: str
    value: str
    pos: int


def tokenize(src: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c in ('"', "'"):
            s = ""
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    j += 1
                    s += src[j]
                    j += 1
                    continue
                s += src[j]
                j += 1
            if j >= n:
                raise FormulaError(f"Unclosed string at {i}")
            out.append(Token("str", s, i))
            i = j + 1
            continue
        if (c.isdigit() and c.isascii()) or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and src[j] in "0123456789._":
                # A dot belongs to the number only while digits follow it, so
                # `2.toFixed(1)` is a method call and `2.5` is a number.
                if src[j] == "." and not (j + 1 < n and src[j + 1].isdigit()):
                    break
                j += 1
            out.append(Token("num", src[i:j].replace("_", ""), i))
            i = j
            continue
        if IDENT_START.match(c):
            j = i
            while j < n and IDENT_CHAR.match(src[j]):
                j += 1
            out.append(Token("ident", src[i:j], i))
            i = j
            continue
        op = next((o for o in OPERATORS if src.startswith(o, i)), None)
        if op:
            out.append(Token("op", op, i))
            i += len(op)
            continue
        if c in PUNCT:
            out.append(Token("punct", c, i))
            i += 1
            continue
        raise FormulaError(f'Unexpected character "{c}" at {i}')
    out.append(Token("eof", "", n))
    return out


@dataclass
class Node:
    kind: str
    value: Any = None
    name: str = ""
    op: str = ""
    target: "Node | None" = None
    item: "Node | None" = None
    index: "Node | None" = None
    left: "Node | None" = None
    right: "Node | None" = None
    cond: "Node | None" = None
    then: "Node | None" = None
    other: "Node | None" = None
    args: list["Node"] = field(default_factory=list)


PRECEDENCE = {"||": 1, "&&": 2, "==": 3, "!=": 3, ">": 4, "<": 4, ">=": 4, "<=": 4,
              "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}


class Parser:
    def __init__(self, toks: list[Token]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def eat(self, value: str) -> bool:
        t = self.peek()
        if t.value == value and t.type != "str":
            self.i += 1
            return True
        return False

    def expect(self, value: str) -> None:
        if not self.eat(value):
            raise FormulaError(f'Expected "{value}" at {self.peek().pos}')

    def parse(self) -> Node:
        n = self.expr(0)
        if self.peek().type != "eof":
            raise FormulaError(f'Unexpected "{self.peek().value}"')
        return n

    def expr(self, min_prec: int) -> Node:
        left = self.unary()
        while True:
            t = self.peek()
            if t.type == "punct" and t.value == "?" and min_prec == 0:
                self.i += 1
                then = self.expr(0)
                self.expect(":")
                other = self.expr(0)
                left = Node("ternary", cond=left, then=then, other=other)
                continue
            if t.type != "op":
                break
            prec = PRECEDENCE.get(t.value)
            if prec is None or prec <= min_prec:
                break
            self.i += 1
            left = Node("binary", op=t.value, left=left, right=self.expr(prec))
        return left

    def unary(self) -> Node:
        t = self.peek()
        if t.type == "op" and t.value in ("!", "-"):
            self.i += 1
            return Node("unary", op=t.value, item=self.unary())
        return self.postfix(self.primary())

    def postfix(self, target: Node) -> Node:
        while True:
            if self.eat("."):
                name = self.next()
                if name.type != "ident":
                    raise FormulaError(f'Expected a name after "." at {name.pos}')
                if self.eat("("):
                    target = Node("call", target=target, name=name.value, args=self.args())
                else:
                    target = Node("member", target=target, name=name.value)
                continue
            if self.eat("["):
                index = self.expr(0)
                self.expect("]")
                target = Node("index", target=target, index=index)
                continue
            return target

    def args(self) -> list[Node]:
        out: list[Node] = []
        if self.eat(")"):
            return out
        while True:
            out.append(self.expr(0))
            if self.eat(","):
                continue
            self.expect(")")
            return out

    def primary(self) -> Node:
        t = self.next()
        if t.type == "num":
            text = t.value
            return Node("lit", value=float(text) if "." in text else int(text))
        if t.type == "str":
            return Node("lit", value=t.value)
        if t.type == "punct" and t.value == "(":
            n = self.expr(0)
            self.expect(")")
            return n
        if t.type == "punct" and t.value == "[":
            items: list[Node] = []
            if not self.eat("]"):
                while True:
                    items.append(self.expr(0))
                    if self.eat(","):
                        continue
                    self.expect("]")
                    break
            return Node("call", target=None, name="list", args=items)
        if t.type == "ident":
            low = t.value.lower()
            if low == "true":
                return Node("lit", value=True)
            if low == "false":
                return Node("lit", value=False)
            if low == "null":
                return Node("lit", value=None)
            nxt = self.peek()
            if nxt.value == "(" and nxt.type == "punct":
                self.i += 1
                return Node("call", target=None, name=t.value, args=self.args())
            return Node("ident", name=t.value)
        raise FormulaError(f'Unexpected "{t.value or "end of formula"}" at {t.pos}')


_cache: dict[str, Node] = {}


def parse_formula(src: str) -> Node:
    hit = _cache.get(src)
    if hit is not None:
        return hit
    node = Parser(tokenize(src)).parse()
    _cache[src] = node
    return node
