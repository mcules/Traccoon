"""Running a search over a vault.

The parsed query decides, note by note, and the index provides the notes. There
is no ranking here yet: the other side orders by a text index it keeps beside
this one, and what is compared while both run is which notes answer, not in
which order they are listed. Order is a later question and a smaller one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..index.links import LinkGraph
from ..model.note import strip_note_suffix
from .evaluate import Doc, Range, match_doc
from .search import And, Node, parse_query, render
from .words import WordIndex, uses_operators

SNIPPET = 280
# How many parts of a query are counted on their own when it found nothing. Each is one more
# pass over the notes, and a query with more parts than this has a different problem than a
# reader who cannot see which part is to blame.
MAX_PARTS = 6
# How long the whole explanation may take. It exists to save a model a round trip of about
# ten seconds, so spending six of them counting is a bad trade — and a regex over the full
# text of six thousand notes does exactly that. Whatever is counted when the budget runs out
# is reported as the lower bound it is; a number with `partial` on it still separates "there
# are none of those anywhere" from "not in that folder", which is the whole job.
EXPLAIN_BUDGET_SEC = 0.5


@dataclass
class Hit:
    path: str
    title: str
    tags: list[str]
    snippet: str
    hits: list[Range]

    def as_json(self) -> dict:
        return {"path": self.path, "title": self.title, "tags": self.tags,
                "snippet": self.snippet, "score": 0,
                "matches": [{"from": h.start, "to": h.end} for h in self.hits[:20]]}


def _snippet(doc: Doc) -> str:
    return " ".join(doc.content.split())[:SNIPPET]


def run(graph: LinkGraph, query: str, words: WordIndex | None = None) -> list[Hit]:
    """Which notes answer this query.

    Two roads, and the query picks. Anything that uses the search language is
    filtered by the evaluator; plain words are ranked by the word index. That
    split is how these notes have always been searched, and a query that means
    one thing yesterday and another today is worse than either road alone.
    """
    if not uses_operators(query) and words is not None:
        out = []
        for rel, _score in words.search(query):
            doc = graph.docs.get(rel)
            if doc is None:
                continue
            out.append(_hit(rel, doc, []))
        return out

    node = parse_query(query)
    if node is None:
        return []
    out: list[Hit] = []
    for rel, doc in graph.docs.items():
        matched, hits = match_doc(node, doc)
        if matched:
            out.append(_hit(rel, doc, hits))
    out.sort(key=lambda h: h.path)
    return out


def explain_empty(graph: LinkGraph, query: str,
                  words: WordIndex | None = None) -> list[dict]:
    """Which part of a query that found nothing is the one that found nothing.

    A bare `total: 0` is a negative with nothing under it, and a negative like that gets
    asked again. Run 2517 searched five times for the same thing in three spellings, each
    time correctly getting none, because none of the answers gave it anything to stand on:
    it could not tell "those checkboxes do not exist" from "not in that folder" from "I
    wrote the query wrong".

    So every part of the query is counted on its own. Two parts that each match plenty while
    the whole matches nothing is a different answer from a part that matches nowhere, and
    both are answers rather than an absence.

    Empty when there is nothing to take apart: one part cannot be to blame for itself, and
    past `MAX_PARTS` the extra passes cost more than the answer is worth.
    """
    node = parse_query(query)
    parts: list[Node] = []
    if isinstance(node, And):
        parts = list(node.items)
    elif not uses_operators(query) and words is not None:
        # The word index road. Its terms are combined with OR, so nothing came back only
        # when NO term is anywhere — and then which of them is missing is the whole answer.
        terms = [t for t in query.split() if t.strip()]
        if len(terms) < 2:
            return []
        return [{"part": t, "matches": len(words.search(t))} for t in terms[:MAX_PARTS]]
    if len(parts) < 2:
        return []
    deadline = time.monotonic() + EXPLAIN_BUDGET_SEC
    out: list[dict] = []
    for part in parts[:MAX_PARTS]:
        found = seen = 0
        for doc in graph.docs.values():
            seen += 1
            if match_doc(part, doc)[0]:
                found += 1
            # Not only between the parts: a single regex over the whole vault is itself
            # slower than the round trip this is meant to save.
            if not seen % 200 and time.monotonic() > deadline:
                break
        entry = {"part": render(part), "matches": found}
        if seen < len(graph.docs):
            entry["partial"] = f"counting stopped after {seen} of {len(graph.docs)} notes"
        out.append(entry)
        if time.monotonic() > deadline:
            break
    return out


def _hit(rel: str, doc: Doc, hits: list[Range]) -> Hit:
    title = str(doc.frontmatter.get("title") or "") or strip_note_suffix(doc.filename)
    return Hit(path=rel, title=title, tags=doc.tags, snippet=_snippet(doc), hits=hits)
