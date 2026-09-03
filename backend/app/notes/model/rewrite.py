"""Keeping links pointing at a note that was renamed or moved.

Without this every rename leaves dead links behind in however many notes
referred to the one that moved — and silently, because a link to a missing note
looks exactly like a link to one that exists. The vault is shared with other
programs that do this too, so the result has to agree with theirs.

Deliberately careful in two places:

  * **A link is only rewritten when it currently means the note that moved.** A
    bare `[[Note]]` that could mean two different files is left alone rather
    than pointed somewhere by guesswork.
  * **Code and comments are not text.** A link inside a fenced block, inside
    backticks or inside `%%…%%` is an example or a note to self, not a
    reference, and rewriting it changes something somebody wrote on purpose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote

MD_EXT = re.compile(r"\.(md|markdown)$", re.I)

# Fenced blocks (the closing fence must be the one that opened it, hence the
# back reference), inline code, and the vault's own comment form.
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[\s\S]*?^[ \t]*\1[ \t]*$", re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")
COMMENT = re.compile(r"%%[\s\S]*?%%")

WIKI = re.compile(r"(!?)\[\[([^\]\n|#^]+)((?:[#^][^\]\n|]*)?)((?:\|[^\]\n]*)?)\]\]")
MDLINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)<>\s]+)>?\s*\)")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)

# What `encodeURI` leaves alone. Round brackets are then taken out again,
# because a bracket inside a markdown link ends it.
URI_SAFE = ";,/?:@&=+$-_.!~*'()#"


def strip_ext(p: str) -> str:
    return MD_EXT.sub("", p)


def masked(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for pattern in (FENCE, INLINE_CODE, COMMENT):
        for m in pattern.finditer(text):
            out.append((m.start(), m.end()))
    return out


def _in_mask(masks: list[tuple[int, int]], at: int) -> bool:
    return any(a <= at < b for a, b in masks)


def shortest_name(target: str, all_paths: list[str]) -> str:
    """The shortest text that still names this note without ambiguity.

    The bare name when only one note carries it, the whole path otherwise. That
    is the form the vault is written in, and writing full paths everywhere would
    make every rename show up as a change in the text of the link.
    """
    base = MD_EXT.sub("", PurePosixPath(target).name)
    clashes = [p for p in all_paths if MD_EXT.sub("", PurePosixPath(p).name) == base]
    return strip_ext(target) if len(clashes) > 1 else base


def href_for(target: str) -> str:
    return quote(target, safe=URI_SAFE).replace("(", "%28").replace(")", "%29")


@dataclass
class Rewrite:
    text: str
    count: int


def rewrite_in_text(text: str, *, new_name: str, new_href: str,
                    points_at_moved) -> Rewrite:
    """Rewrite every reference to the moved note inside one note. Pure: the
    caller decides what happens to the result."""
    masks = masked(text)
    count = 0

    def wiki(m: re.Match) -> str:
        nonlocal count
        if _in_mask(masks, m.start()):
            return m.group(0)
        if not points_at_moved(m.group(2).strip()):
            return m.group(0)
        count += 1
        return f"{m.group(1)}[[{new_name}{m.group(3)}{m.group(4)}]]"

    out = WIKI.sub(wiki, text)
    # The offsets of the masks belong to the text they were taken from. After
    # the first pass the text has moved, so they are taken again rather than
    # carried over — a shift of a few characters is enough to protect the wrong
    # region, which is worse than not protecting one at all.
    masks = masked(out)

    def mdlink(m: re.Match) -> str:
        nonlocal count
        if _in_mask(masks, m.start()):
            return m.group(0)
        href = m.group(3)
        if SCHEME.match(href) or href.startswith("#"):
            return m.group(0)                      # somewhere else entirely
        cut = re.search(r"[#?]", href)
        file = href[:cut.start()] if cut else href
        rest = href[cut.start():] if cut else ""
        try:
            from urllib.parse import unquote
            decoded = unquote(file)
        except ValueError:
            decoded = file
        if not points_at_moved(decoded):
            return m.group(0)
        count += 1
        return f"{m.group(1)}[{m.group(2)}]({new_href}{rest})"

    out = MDLINK.sub(mdlink, out)
    return Rewrite(out, count)


@dataclass
class RenameContext:
    """What was true before the move, read while it still is.

    Once the note has moved the link graph answers for the new path, so asking
    it afterwards would find nothing and rewrite nothing — quietly.
    """
    sources: list[str] = field(default_factory=list)
    base_resolves_to_moved: bool = False


def collect_context(graph, source: str) -> RenameContext:
    base = MD_EXT.sub("", PurePosixPath(source).name)
    resolved = graph.resolve(base)
    return RenameContext(
        sources=graph.backlinks(source),
        base_resolves_to_moved=bool(resolved) and resolved.lower() == source.lower(),
    )


@dataclass
class LinkUpdate:
    files: list[dict] = field(default_factory=list)
    links: int = 0


def points_at(source: str, context: RenameContext, graph=None):
    """Whether a link target, as written, currently means the note that moved."""
    from_key = strip_ext(source).lower()
    from_base = MD_EXT.sub("", PurePosixPath(source).name).lower()

    def decide(target: str) -> bool:
        t = target.strip()
        if not t:
            return False
        key = strip_ext(t).lower()
        if key == from_key:
            return True                            # a full path is unambiguous
        if key == from_base:
            # A bare name only counts when it really resolves to the note that
            # moved, so two notes sharing a name never get rewritten by accident.
            if context is not None:
                return context.base_resolves_to_moved
            if graph is None:
                return False
            resolved = graph.resolve(t)
            return bool(resolved) and resolved.lower() == source.lower()
        return False

    return decide
