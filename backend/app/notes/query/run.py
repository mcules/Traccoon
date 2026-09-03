"""Running a search over a vault.

The parsed query decides, note by note, and the index provides the notes. There
is no ranking here yet: the other side orders by a text index it keeps beside
this one, and what is compared while both run is which notes answer, not in
which order they are listed. Order is a later question and a smaller one.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..index.links import LinkGraph
from ..model.note import strip_note_suffix
from .evaluate import Doc, Range, match_doc
from .search import parse_query
from .words import WordIndex, uses_operators

SNIPPET = 280


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


def _hit(rel: str, doc: Doc, hits: list[Range]) -> Hit:
    title = str(doc.frontmatter.get("title") or "") or strip_note_suffix(doc.filename)
    return Hit(path=rel, title=title, tags=doc.tags, snippet=_snippet(doc), hits=hits)
