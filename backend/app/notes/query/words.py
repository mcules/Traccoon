"""The other search: plain words, over an index of them.

A search here goes down one of two roads, and which one is decided by the query
itself. Anything that uses the language — an operator, a quote, a bracket, a
slash, a leading minus, an `OR` — is answered by the evaluator, which filters.
Everything else is answered here, by a word index, which ranks.

That split is not a design choice made now, it is how the notes have been
searched all along, and a query that means one thing yesterday and another today
is worse than either behaviour on its own.

What this index does, because the one it replaces does:

  * **Prefix.** `schreib` finds `Schreibstil`. Somebody typing a search expects
    the results to narrow as they type, not to appear only at the last letter.
  * **Fuzzy, within a fifth of the word.** `Schreibstl` still finds it. The
    allowance grows with the word, so short words stay strict: two letters have
    no room for a typo, twelve have two.
  * **Fields weigh differently.** A word in the title counts four times, in a
    heading twice, in a tag three times. It is the same note either way, but not
    the same answer to the question.

What it does not do is match the ranking of the index it replaces down to the
score. Which notes answer is what gets compared while both run; in which order
they are listed is a smaller question, and one worth deciding rather than
inheriting.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .evaluate import Doc
from .search import OPERATORS

# Everything that makes a query a query rather than a phrase. Copied from the
# other side one for one: this decides which of the two roads a search takes,
# and the two roads answer differently.
_PUNCT = re.compile(r'["/()\[\]]')
_LEADING_MINUS = re.compile(r"(^|\s)-\S")
_OR = re.compile(r"\bOR\b")
_OPS = [re.compile(rf"(^|\s){re.escape(op)}:", re.I) for op in OPERATORS]


def uses_operators(query: str) -> bool:
    if _PUNCT.search(query) or _LEADING_MINUS.search(query) or _OR.search(query):
        return True
    return any(p.search(query) for p in _OPS)


# A word is a run of letters, digits and the marks that hold a word together.
# Unicode on purpose: a vault written in German is full of them, and splitting
# on ASCII alone would make two words out of one.
WORD = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)

FIELD_BOOST = {"title": 4.0, "headings": 2.0, "tags": 3.0, "path": 1.0, "body": 1.0}
FUZZY = 0.2


def terms_of(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD.finditer(text)]


def _distance_at_most(a: str, b: str, most: int) -> bool:
    """Levenshtein, but it gives up as soon as it cannot stay within `most`.

    Over a vault this is asked hundreds of thousands of times, and the full
    matrix for two words that are obviously too far apart is wasted work.
    """
    if abs(len(a) - len(b)) > most:
        return False
    if most <= 0:
        return a == b
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > most:
            return False
        previous = current
    return previous[-1] <= most


@dataclass
class WordIndex:
    """Term -> field -> the notes that carry it."""

    postings: dict[str, dict[str, set[str]]] = field(default_factory=lambda: defaultdict(dict))
    docs: int = 0
    # Which terms one note put into the index. Without it, taking a note back
    # out means walking every term there is, and that happens on every save.
    terms_of_doc: dict[str, set[str]] = field(default_factory=dict)

    def _fields(self, doc: Doc, headings: list[str]) -> dict[str, list[str]]:
        title = str(doc.frontmatter.get("title") or "") or doc.filename
        return {
            "title": [title],
            "headings": headings,
            "tags": doc.tags,
            "path": [doc.path],
            "body": [doc.content],
        }

    def build(self, docs: dict[str, Doc], headings: dict[str, list[str]] | None = None) -> None:
        self.postings = defaultdict(dict)
        self.terms_of_doc = {}
        headings = headings or {}
        for rel, doc in docs.items():
            self._add(rel, doc, headings.get(rel, []))
        self.docs = len(docs)

    def _add(self, rel: str, doc: Doc, headings: list[str]) -> None:
        mine: set[str] = set()
        for field_name, texts in self._fields(doc, headings).items():
            for text in texts:
                for term in terms_of(text):
                    self.postings[term].setdefault(field_name, set()).add(rel)
                    mine.add(term)
        self.terms_of_doc[rel] = mine

    def update(self, rel: str, doc: Doc, headings: list[str]) -> None:
        """One note changed. The rest of the index stays as it is.

        Rebuilding the whole index instead would be a second or two per save,
        which is a second or two on every keystroke that lands.
        """
        known = rel in self.terms_of_doc
        self.remove(rel)
        self._add(rel, doc, headings)
        if not known:
            self.docs += 1

    def remove(self, rel: str) -> None:
        for term in self.terms_of_doc.pop(rel, set()):
            bucket = self.postings.get(term)
            if not bucket:
                continue
            for rels in bucket.values():
                rels.discard(rel)
            # A term nothing carries any more has to go, or it keeps counting
            # towards how rare the words around it are, and every ranking that
            # uses it shifts a little for no reason anybody can see.
            if not any(bucket.values()):
                del self.postings[term]

    def forget(self, rel: str) -> None:
        """Take a note out of the index for good."""
        if rel in self.terms_of_doc:
            self.remove(rel)
            self.docs = max(0, self.docs - 1)

    def _matching_terms(self, term: str) -> list[str]:
        """Every indexed word this query word reaches: itself, what it starts,
        and what is close enough to be a typo."""
        most = int(FUZZY * len(term))
        out = []
        for known in self.postings:
            if known == term or known.startswith(term):
                out.append(known)
            elif most and _distance_at_most(term, known, most):
                out.append(known)
        return out

    def search(self, query: str) -> list[tuple[str, float]]:
        """Notes that answer, best first. Terms are combined with OR."""
        wanted = terms_of(query)
        if not wanted:
            return []
        scores: dict[str, float] = defaultdict(float)
        for term in wanted:
            for known in self._matching_terms(term):
                felder = self.postings.get(known, {})
                # Rarer words say more about a note than common ones do.
                treffer = len({rel for rels in felder.values() for rel in rels})
                idf = math.log(1 + (self.docs / max(1, treffer)))
                nachlass = 1.0 if known == term else 0.6
                for feld, rels in felder.items():
                    gewicht = FIELD_BOOST.get(feld, 1.0) * idf * nachlass
                    for rel in rels:
                        scores[rel] += gewicht
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
