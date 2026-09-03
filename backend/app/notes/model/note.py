"""What a note is made of: its fields, its links, its tags, its headings.

Layer two. It knows markdown and nothing about the disk or about HTTP.

The rules below are the ones the notes were written under, so they are copied
exactly rather than improved. A link that resolves here and not there is a link
that disappears from a person's graph, and nobody would look for the cause in a
regular expression.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .frontmatter import split

# `[[target]]`, `[[target|label]]`, `[[target#heading]]`, and the same with a
# leading `!` for an embed. The label and the heading are not part of the target.
WIKILINK = re.compile(r"!?\[\[([^\]]+?)\]\]")

# A tag starts at the beginning or after whitespace, never in the middle of a
# word: `C#` in a sentence is not a tag, and neither is the anchor of a URL.
TAG = re.compile(r"(?:^|\s)#([A-Za-z0-9_\-/]+)")

HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)

NOTE_SUFFIXES = (".md", ".markdown")


def strip_note_suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in NOTE_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def link_key(target: str) -> str:
    """A link target reduced to what two of them are compared by.

    Without the extension and lower case, because `[[Note]]`, `[[note]]` and
    `[[Note.md]]` are one and the same link to a person writing them.
    """
    return strip_note_suffix(target).lower()


@dataclass
class Note:
    """One parsed note. `body` is the text without the frontmatter block."""

    title: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)


def _links_into(value: Any, links: list[str], seen: set[str]) -> None:
    """Wikilinks inside a property value, at whatever depth it has."""
    if isinstance(value, str):
        for m in WIKILINK.finditer(value):
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if target and target not in seen:
                seen.add(target)
                links.append(target)
    elif isinstance(value, list):
        for item in value:
            _links_into(item, links, seen)
    elif isinstance(value, dict):
        for item in value.values():
            _links_into(item, links, seen)


def parse(rel: str, raw: str) -> Note:
    data, body = split(raw)

    links: list[str] = []
    seen: set[str] = set()
    # A link in a property is a link. This vault uses them as relations — the
    # supplier of an order, the company of a person, the host of a service — and
    # nearly three thousand of them stand there. Counted from the parsed values
    # rather than from the raw block, so a link in a comment inside the
    # properties is not one.
    for value in data.values():
        _links_into(value, links, seen)
    for m in WIKILINK.finditer(body):
        target = m.group(1).split("|")[0].split("#")[0].strip()
        if target and target not in seen:
            seen.add(target)
            links.append(target)

    tags: list[str] = []
    seen_tags: set[str] = set()
    for m in TAG.finditer(body):
        if m.group(1) not in seen_tags:
            seen_tags.add(m.group(1))
            tags.append(m.group(1))
    # Tags declared in the frontmatter count as well, as a list or as one string
    # with separators, because both spellings are in use in the same vault.
    declared = data.get("tags")
    if isinstance(declared, list):
        weitere = [str(t) for t in declared]
    elif isinstance(declared, str):
        weitere = [t for t in re.split(r"[,\s]+", declared) if t]
    else:
        weitere = []
    for t in weitere:
        if t not in seen_tags:
            seen_tags.add(t)
            tags.append(t)

    headings = [m.group(1) for m in HEADING.finditer(body)]

    title = data.get("title")
    if not isinstance(title, str) or not title:
        title = strip_note_suffix(PurePosixPath(rel).name)

    return Note(title=title, frontmatter=data, body=body,
                tags=tags, links=links, headings=headings)
