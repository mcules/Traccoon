"""The search language of the notes, parsed rather than approximated.

A search is not a bag of words here. `tag:idea` means tagged, `path:` filters,
`-` negates, `OR` widens, quotes hold a phrase together, slashes hold a regular
expression, and brackets ask about a property. An implementation that only
re-weights results by these words answers plausibly and wrongly, which is worse
than answering nothing.

Grammar, as it was written:

    query    := andGroup (OR andGroup)*      space binds tighter than OR
    andGroup := primary+
    primary  := op ":" primary | "[" query (":" query)? "]" | "-" primary
              | "(" query ")" | TRUE | FALSE | EMPTY | text | "phrase" | /regex/

Translated from the implementation the notes were written against, deliberately
line for line rather than improved: a query that means one thing there and
another here is a person's saved search quietly changing its answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Union

OPERATORS = {
    "match-case", "ignore-case", "path", "file", "content", "line",
    "block", "section", "task", "task-todo", "task-done", "tag",
}

# Operators that still look at ordinary text, so the words inside them can be
# used to pre-select candidates before anything is evaluated in full.
TEXTY = {"content", "line", "block", "section", "match-case", "ignore-case"}


# --------------------------------------------------------------------- nodes

@dataclass
class And:
    items: list["Node"]


@dataclass
class Or:
    items: list["Node"]


@dataclass
class Not:
    item: "Node"


@dataclass
class Op:
    op: str
    item: "Node"


@dataclass
class Prop:
    name: "Node"
    value: "Node | None" = None


@dataclass
class Cmp:
    dir: Literal[">", "<"]
    item: "Node"


@dataclass
class Text:
    value: str


@dataclass
class Phrase:
    value: str


@dataclass
class Regex:
    source: str


@dataclass
class Const:
    value: Literal["true", "false", "empty"]


Node = Union[And, Or, Not, Op, Prop, Cmp, Text, Phrase, Regex, Const]


# ----------------------------------------------------------------- tokenizer

@dataclass
class Token:
    type: str
    value: str


BARE = re.compile(r"[\s()\[\]:\"/-]")
SPACE = re.compile(r"\s")


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if SPACE.match(c):
            i += 1
            continue
        if c in "()[]:":
            typ = {"(": "lparen", ")": "rparen", "[": "lbracket",
                   "]": "rbracket", ":": "colon"}[c]
            out.append(Token(typ, c))
            i += 1
            continue
        if c in "><":
            out.append(Token("cmp", c))
            i += 1
            continue
        if c == "-":
            # A minus negates when it opens a term: at the start, after
            # whitespace, or right after a bracket or a colon. Inside a word it
            # is a hyphen, which is why this looks at the input and not at the
            # previous token — `a -b` negates, `task-done` does not.
            if i == 0 or re.match(r"[\s([:]", text[i - 1]):
                out.append(Token("minus", c))
                i += 1
                continue
        if c == '"':
            end = text.find('"', i + 1)
            value = text[i + 1:] if end < 0 else text[i + 1:end]
            out.append(Token("phrase", value))
            i = n if end < 0 else end + 1
            continue
        if c == "/":
            # A regular expression, unless the slash belongs to a path-like word.
            end = text.find("/", i + 1)
            if end > i + 1:
                out.append(Token("regex", text[i + 1:end]))
                i = end + 1
                continue
        j = i
        while j < n and (not BARE.match(text[j]) or (text[j] == "-" and j > i)
                         or text[j] == "/"):
            j += 1
        word = text[i:j] or text[i]
        i = j if j > i else i + 1
        if word == "OR":
            out.append(Token("or", word))
        elif word in ("TRUE", "FALSE", "EMPTY"):
            out.append(Token("const", word.lower()))
        elif i < n and text[i] == ":" and word.lower() in OPERATORS:
            out.append(Token("op", word.lower()))
        else:
            out.append(Token("text", word))
    return out


# -------------------------------------------------------------------- parser

class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self) -> Node | None:
        return self.parse_or()

    def parse_or(self) -> Node | None:
        first = self.parse_and()
        if first is None:
            return None
        items = [first]
        while (t := self.peek()) and t.type == "or":
            self.pos += 1
            nxt = self.parse_and()
            if nxt is not None:
                items.append(nxt)
        return items[0] if len(items) == 1 else Or(items)

    def parse_and(self) -> Node | None:
        items: list[Node] = []
        while True:
            t = self.peek()
            if t is None or t.type in ("or", "rparen", "rbracket", "colon"):
                break
            item = self.parse_primary()
            if item is None:
                break
            items.append(item)
        if not items:
            return None
        return items[0] if len(items) == 1 else And(items)

    def parse_primary(self) -> Node | None:
        t = self.peek()
        if t is None:
            return None
        if t.type == "minus":
            self.pos += 1
            item = self.parse_primary()
            return Not(item) if item is not None else None
        if t.type == "cmp":
            self.pos += 1
            item = self.parse_primary()
            return Cmp(t.value, item) if item is not None else None      # type: ignore[arg-type]
        if t.type == "lparen":
            self.pos += 1
            inner = self.parse_or()
            if (p := self.peek()) and p.type == "rparen":
                self.pos += 1
            return inner
        if t.type == "lbracket":
            self.pos += 1
            name = self.parse_or()
            value = None
            if (p := self.peek()) and p.type == "colon":
                self.pos += 1
                value = self.parse_or()
            if (p := self.peek()) and p.type == "rbracket":
                self.pos += 1
            return Prop(name, value) if name is not None else None
        if t.type == "op":
            self.pos += 1
            if (p := self.peek()) and p.type == "colon":
                self.pos += 1
            item = self.parse_primary()
            # An operator with nothing after it asks whether the note has that
            # thing at all, so it keeps an empty term rather than disappearing.
            return Op(t.value, item if item is not None else Text(""))
        if t.type == "phrase":
            self.pos += 1
            return Phrase(t.value)
        if t.type == "regex":
            self.pos += 1
            return Regex(t.value)
        if t.type == "const":
            self.pos += 1
            return Const(t.value)                                        # type: ignore[arg-type]
        if t.type == "text":
            self.pos += 1
            return Text(t.value)
        self.pos += 1
        return None


def parse_query(text: str) -> Node | None:
    return _Parser(tokenize(text)).parse()


def plain_terms(node: Node | None) -> list[str]:
    """The ordinary words in a query, enough to pre-select candidates."""
    if node is None:
        return []
    if isinstance(node, (Text, Phrase)):
        return [node.value]
    if isinstance(node, (And, Or)):
        out: list[str] = []
        for item in node.items:
            out.extend(plain_terms(item))
        return out
    if isinstance(node, Op):
        return plain_terms(node.item) if node.op in TEXTY else []
    return []
