"""Where in a note the search words actually stand.

The list of results says which notes match; this says where, so somebody can
see the sentence rather than open every one of them. It is asked for separately
and in batches because it means reading the notes again, and a search that had
to do that for every hit before showing anything would show nothing for a while.

**The offsets are counted the way a browser counts.** They are handed to a page
that puts a mark around a stretch of text, and that page counts in UTF-16 code
units: a character outside the basic plane — an emoji in a heading, and this
vault has them — is two there and one in Python. Getting that wrong shifts every
mark after it by one and the highlight lands on the wrong letters.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..dv.js import js_len
from .search import parse_query, plain_terms

# How much text to show on each side of a hit, and how close two hits have to be
# to share one window rather than get two.
PADDING = 32
MERGE_GAP = 64
MOST_CONTEXTS = 20
SHORTEST_TERM = 2


@dataclass
class Context:
    text: str
    ranges: list[list[int]] = field(default_factory=list)
    # Whether the window starts or ends inside the note, so the page can show
    # that there is more.
    pre: bool = False
    post: bool = False

    def as_json(self) -> dict:
        return {"text": self.text, "ranges": self.ranges,
                "pre": self.pre, "post": self.post}


def _units(text: str) -> bytes:
    """The string as the code units that language counts in.

    Everything below works on these rather than on characters, because every
    number here ends up in a browser: where a window starts, how far the padding
    reaches, how close two hits have to be to share one window. A single emoji
    in the text is one character here and two units there, and computing in
    characters shifts every window after it — the mark then sits one letter off,
    and the window starts one letter late.
    """
    return text.encode("utf-16-le")


def _text(units: bytes) -> str:
    return units.decode("utf-16-le", "replace")


def _occurrences(hay: bytes, needles: list[bytes]) -> list[tuple[int, int]]:
    """Every place a needle stands, in code units, overlaps dropped left to right."""
    raw: list[tuple[int, int]] = []
    for needle in needles:
        if not needle:
            continue
        at = hay.find(needle)
        while at != -1:
            # Only an even offset is the start of a code unit; an odd one is the
            # second byte of one and never a real match.
            if at % 2 == 0:
                raw.append((at // 2, len(needle) // 2))
            at = hay.find(needle, at + 1)
    if not raw:
        return []
    # Earliest first, and where two start together the longer one wins.
    raw.sort(key=lambda o: (o[0], -o[1]))
    kept: list[tuple[int, int]] = []
    last_end = -1
    for start, length in raw:
        if start >= last_end:
            kept.append((start, length))
            last_end = start + length
    return kept


def _shift(untrimmed: str, ranges: list[list[int]]) -> list[list[int]]:
    """Move the marks after the window's leading whitespace is trimmed off."""
    lead = js_len(untrimmed) - js_len(untrimmed.lstrip())
    trimmed = js_len(untrimmed.strip())
    return [[max(0, start - lead), length] for start, length in ranges
            if start - lead < trimmed]


def in_body(body: str, terms: list[str], *, case_sensitive: bool = False,
            most: int = MOST_CONTEXTS) -> tuple[int, list[Context]]:
    words = [t for t in terms if t]
    if not words:
        return 0, []
    hay = _units(body if case_sensitive else body.lower())
    needles = [_units(t if case_sensitive else t.lower()) for t in words]
    found = _occurrences(hay, needles)
    if not found:
        return 0, []

    whole = _units(body)
    total = len(whole) // 2
    contexts: list[Context] = []
    group: list[tuple[int, int]] = []

    def flush() -> None:
        nonlocal group
        if not group or len(contexts) >= most:
            group = []
            return
        start = max(0, group[0][0] - PADDING)
        last_start, last_len = group[-1]
        end = min(total, last_start + last_len + PADDING)
        # Newlines and tabs become spaces one for one, so the offsets stay put.
        window = _text(whole[start * 2:end * 2])
        for ch in "\n\r\t":
            window = window.replace(ch, " ")
        ranges = [[s - start, length] for s, length in group]
        contexts.append(Context(text=window.strip(), ranges=_shift(window, ranges),
                                pre=start > 0, post=end < total))
        group = []

    for occurrence in found:
        if group:
            previous = group[-1]
            if occurrence[0] - (previous[0] + previous[1]) > MERGE_GAP:
                flush()
        group.append(occurrence)
    flush()

    return len(found), contexts


def terms_of_query(query: str) -> list[str]:
    """The words of a query, for finding them again in a note.

    The field parts of a query (`tag:`, `path:`) are not words to look for in
    the text, so what is left after them is what is marked. A single letter is
    dropped: marking every `a` in a note helps nobody.
    """
    node = parse_query(query)
    words = plain_terms(node) if node is not None else []
    if not words:
        words = query.split()
    out = []
    for word in words:
        cleaned = word.strip().strip("\"'").strip()
        if len(cleaned) >= SHORTEST_TERM:
            out.append(cleaned)
    return out
