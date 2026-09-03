"""The appointments as they stand in a daily note.

Deliberately thin: time, title, calendar — an anchor to write under, and nothing
more. What the appointment is about belongs in the calendar; in the note it only
pushes the reader's own words further down.

    - 09:00 Daily Dev · Vostura
        - my own notes stay here, untouched
    - 18:30 OV-Abend · B37

An appointment is matched to its line by time and title rather than by a marker
hidden in the text, so the note stays readable in any editor. The price is that
renaming an appointment in the calendar loses the connection to what was written
under it — which is why a line is only ever rewritten, never removed: an
appointment that disappears is struck through and kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .fetch import Event

HEADING = "# Termine"
EVENT_LINE = re.compile(r"^(\s*)- (?:(\d{2}:\d{2}) )?(?:~~)?(.+?)(?:~~)?(?: · ([^·]+))?\s*$")
ANY_HEADING = re.compile(r"^#{1,6}\s")
# Something written under an appointment. A tab counts as much as two spaces:
# this vault indents its sub-bullets with tabs, and a rule that only knew spaces
# would decide that nothing was written there and put an agenda on top of
# somebody's notes. The side this comes from asks for two whitespace characters
# and has the same blind spot.
INDENTED = re.compile(r"^(?:\t|\s{2,})\S")


@dataclass
class Template:
    """An agenda that belongs under a recurring appointment."""
    match: str
    lines: list[str]


@dataclass
class Result:
    text: str
    added: int = 0
    updated: int = 0
    cancelled: int = 0


def line_for(event: Event) -> str:
    time = "" if event.allDay else f"{event.time} "
    title = f"~~{event.title}~~" if event.cancelled else event.title
    return f"- {time}{title} · {event.calendar}"


def matches(line: str, event: Event) -> bool:
    """Does this line describe that appointment?"""
    m = EVENT_LINE.match(line)
    if not m:
        return False
    indent, time, title, calendar = m.groups()
    if indent:
        return False                       # a sub-bullet is the reader's own note
    if (calendar or "").strip() != event.calendar:
        return False
    # A moved appointment keeps its title: same title, same calendar, other time.
    return title.strip().replace("~~", "") == event.title


def _template_for(templates: list[Template], title: str) -> Template | None:
    return next((t for t in templates if t.match and t.match in title), None)


def apply_lines(content: str, events: list[Event], *, heading: str = HEADING,
                templates: list[Template] | None = None) -> Result:
    """Put the day's appointments under the heading, leaving everything else —
    including the sub-bullets under each one — alone.

    A note without the heading is not touched. Writing one in would mean this
    deciding what somebody's note looks like, and a note that has no such
    section is a note that does not want one.
    """
    templates = templates or []
    lines = content.split("\n")
    head = next((i for i, l in enumerate(lines) if l.strip() == heading), -1)
    if head < 0:
        return Result(text=content)

    # The section runs to the next heading of the same or a higher level.
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if ANY_HEADING.match(lines[i]):
            end = i
            break

    section = lines[head + 1:end]
    out: list[str] = []
    added = updated = cancelled = 0
    seen: set[str] = set()

    for i, line in enumerate(section):
        event = next((e for e in events if matches(line, e)), None)
        if event is None:
            out.append(line)
            continue
        seen.add(event.id)
        wanted = line_for(event)
        if wanted != line:
            updated += 1
            if event.cancelled:
                cancelled += 1
        out.append(wanted)
        # A recurring appointment can bring its own agenda — but only once: if
        # anything is written under it already, that is the note and it stays.
        template = _template_for(templates, event.title)
        following = section[i + 1] if i + 1 < len(section) else ""
        if template and not INDENTED.match(following):
            out.extend(template.lines)

    fresh = [e for e in events if e.id not in seen]
    if fresh:
        # At the end of the section, before its trailing blank lines.
        while out and not out[-1].strip():
            out.pop()
        for event in fresh:
            out.append(line_for(event))
            added += 1
            template = _template_for(templates, event.title)
            if template:
                out.extend(template.lines)
        out.append("")

    return Result(text="\n".join(lines[:head + 1] + out + lines[end:]),
                  added=added, updated=updated, cancelled=cancelled)


# ------------------------------------------------------------- where it goes

TOKENS = (("YYYY", "%Y"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"))


def daily_note_path(day, folder: str, fmt: str) -> str:
    """Where the note of one day lives.

    The format is the vault's own (`YYYY/MM/YYYY-MM-DD` here), so it decides the
    folders as well as the file name — a daily note in this vault sits in a year
    and a month.
    """
    name = fmt
    for token, code in TOKENS:
        name = name.replace(token, day.strftime(code))
    return f"{folder}/{name}.md" if folder else f"{name}.md"


def template_lines(raw: str) -> list[str]:
    """A template note as the lines that go under an appointment.

    Indented one level, so the agenda belongs to the appointment rather than
    standing beside it, and without the properties block at the top — that
    belongs to the template, not to what is being inserted.
    """
    body = re.sub(r"\A---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?", "", raw)
    out = [f"    {l}" if l.strip() else "" for l in body.split("\n")]
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return out
