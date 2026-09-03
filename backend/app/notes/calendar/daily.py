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
# The counter the previous writer put behind the title of an appointment that
# runs over several days. The feed's own title does not carry it, so a line that
# has one looks like a different appointment, is not recognised, and the same
# multi-day event lands in the note a second time. It is data written by that
# writer, in its language, which is why the word stands here in German.
DAY_COUNTER = re.compile(r"\s*\(Tag \d+/\d+\)\s*$")
# The same thing wherever it stands. Comparing two whole lines needs this: there
# the counter sits inside, before the calendar, not at the end.
DAY_COUNTER_ANY = re.compile(r"\s*\(Tag \d+/\d+\)")


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
    written = title.strip().replace("~~", "")
    return written == event.title or DAY_COUNTER.sub("", written) == event.title


def _template_for(templates: list[Template], title: str) -> Template | None:
    return next((t for t in templates if t.match and t.match in title), None)


def apply_lines(content: str, events: list[Event], *, heading: str = HEADING,
                templates: list[Template] | None = None,
                keep_strikes: bool = False) -> Result:
    """Put the day's appointments under the heading, leaving everything else —
    including the sub-bullets under each one — alone.

    A note without the heading is not touched. Writing one in would mean this
    deciding what somebody's note looks like, and a note that has no such
    section is a note that does not want one.

    `keep_strikes` is for a day that is over. A feed carries the state of an
    appointment now, not the state it was in back then: a meeting cancelled in
    July is simply gone from the series today, and syncing that day again would
    quietly take the strike off and say it had taken place. A past note is a
    record of what happened, so what is struck there stays struck.
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
        # A line that says which day of the appointment this is says more than
        # the feed does, so it is left as it stands rather than shortened to the
        # plain title. Rewriting it would throw that away every sync.
        if DAY_COUNTER.search(EVENT_LINE.match(line).group(3).strip()):
            out.append(line)
            continue
        if keep_strikes and "~~" in line and "~~" not in wanted:
            out.append(line)
            template = _template_for(templates, event.title)
            following = section[i + 1] if i + 1 < len(section) else ""
            if template and not INDENTED.match(following):
                out.extend(template.lines)
            continue
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


# ------------------------------------------------------- folding the old form

# What the previous writer put into the section: a bell, a time label between
# asterisks, the calendar as a wiki link, and a tracking comment at the end.
#
#     - 📅 *09:00* [[Firma/Termine|Firma]] Daily Dev <!-- uid:… -->
#         - Ort <!-- loc -->
#           Somewhere
#
# Two generations of it are in the vault, with and without the comment: a
# cancelled appointment was written struck through and lost its marker.
LEGACY_WITH_UID = re.compile(
    r"^(\s*)- (?:📅 )?(?:\*([^*]+)\*\s*)?(.*?)\s*<!--\s*uid:[^>]*-->\s*$")
LEGACY_STRUCK = re.compile(
    r"^(\s*)- ~~(?:📅 )?(?:\*([^*]+)\*\s*)?(.*?)~~\s*$")
# A third generation, and it is the previous folding's own leftover: it dropped
# the tracking comment but shortened nothing, because it did not recognise the
# time label. What is left is a line with the label and the link and no marker
# at all, so the shape has to carry the recognition — hence the piped wiki link
# is required, and hence folding only happens inside the appointment section.
LEGACY_BARE = re.compile(
    r"^(\s*)- (?:📅 )?\*([^*]+)\*\s*(\[\[[^\]|]+\|[^\]]+\]\].*)$")
LEGACY_BLOCK = re.compile(r"<!--\s*(?:loc|desc)\s*-->")
LEGACY_LINK = re.compile(r"^\[\[([^\]|]+)\|([^\]]+)\]\]\s*(.*)$")


def indent_width(line: str, tab: int = 4) -> int:
    """How far a line is pushed in, counting a tab as four.

    The fold needs this rather than a fixed number of spaces: what belongs to a
    location or description block is everything indented *deeper than its own
    bullet*, and a reader's own sub-bullet sits at the same depth as that
    bullet. The side this comes from asks for four whitespace characters, which
    is the same depth a reader writes at — so it swallows their notes when they
    happen to follow a description.
    """
    width = 0
    for char in line:
        if char == "\t":
            width += tab - (width % tab)
        elif char == " ":
            width += 1
        else:
            break
    return width

SHORT_LINE = re.compile(r"^- (?:\d{2}:\d{2} )?.+ · .+$")
# A short line that somebody — or an earlier folding — struck through as a
# whole. Its parts have to be read back out to put the strike where the short
# form keeps it, around the title.
SHORT_INNER = re.compile(r"^(?:(\d{2}:\d{2}) )?(.+?) · ([^·]+)$")
# A fence opens or closes a code block. The daily note of this vault carries
# `dataviewjs` blocks, and the JavaScript in them has lines that read like
# markdown — a heading, a bullet. Walking those as text would let a code
# comment decide that the appointment section had begun.
# The byte-order mark is part of the class on purpose: six notes in this vault
# open with one, and Python's `\s` does not count it as space. Without it the
# very first fence of such a note goes unseen, the count of open and closed
# fences is off by one, and from there the whole note reads as code.
FENCE = re.compile(r"^[\ufeff\s]*(?:```|~~~)")
CLOCK = re.compile(r"^\d{2}:\d{2}$")


@dataclass
class Tidied:
    text: str
    changed: int = 0


def tidy_legacy_lines(content: str, calendars: set[str] | None = None) -> Tidied:
    """Bring the old long form down to the short one.

    The control comments and the location and description bullets the previous
    writer produced go; what somebody wrote themselves stays. Then the same
    appointment written twice — once by each writer — becomes one line.

    **One thing is done differently on purpose.** The side this comes from
    reads only a clock as the time label, so an all-day appointment
    (`*ganztägig*`) keeps its whole old line: it is neither shortened nor
    recognised as a duplicate of the short line beside it. That is why the
    vault has all-day appointments standing twice. Any label is accepted here
    and one that is not a clock means all day, which is what the short form
    writes as no time at all.

    `calendars` are the names of the calendars this person has, and giving them
    is what keeps a hand-edited line intact: one appointment in this vault
    points at the note about the event rather than at a calendar. Folding that
    shape blindly reads the note as the calendar and throws the link away, so a
    line whose label is not one of these names is left exactly as it stands.
    """
    lines = content.split("\n")
    out: list[str] = []
    changed = 0
    # The indent of the location or description bullet currently being dropped,
    # or None when nothing is.
    dropping: int | None = None
    inside = False
    fenced = False

    for line in lines:
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        if ANY_HEADING.match(line):
            inside = line.strip() == HEADING
            dropping = None
            out.append(line)
            continue
        if not inside:
            out.append(line)
            continue
        if LEGACY_BLOCK.search(line):
            dropping = indent_width(line)
            changed += 1
            continue
        if dropping is not None:
            if not line.strip() or indent_width(line) > dropping:
                if line.strip():
                    changed += 1
                continue
            dropping = None

        found = (LEGACY_WITH_UID.match(line) or LEGACY_STRUCK.match(line)
                 or LEGACY_BARE.match(line))
        if not found:
            out.append(line)
            continue
        struck = "~~" in line
        indent, label, rest = found.groups()
        link = LEGACY_LINK.match(rest.strip())
        if calendars is not None and link and link.group(2).strip() not in calendars:
            out.append(line)
            continue
        # A label that is not a clock says "all day", and the short form says
        # that by leaving the time off.
        time = label if label and CLOCK.match(label) else ""
        if link:
            calendar = link.group(2).strip()
            title = link.group(3).strip()
        else:
            inner = SHORT_INNER.match(rest.strip())
            if inner:
                time = time or (inner.group(1) or "")
                title, calendar = inner.group(2).strip(), inner.group(3).strip()
            else:
                calendar, title = "", rest.strip()
        # The strike goes around the title, which is where `line_for` puts it.
        # Around the whole line — the shape the old writer used — it reads the
        # same but matches nothing: the calendar then ends in two tildes, the
        # appointment is not recognised as already standing there, and the sync
        # writes it a second time.
        body = f"~~{title}~~" if struck else title
        folded = (f"{indent}- {time + ' ' if time else ''}{body}"
                  f"{f' · {calendar}' if calendar else ''}")
        out.append(folded)
        # Only a line that actually came out different is a change. A short line
        # that was already short matches the struck-through pattern and is
        # written back identically; counting that would report work where none
        # happened, and a caller that shows the number would say a note had been
        # tidied when nothing in it moved.
        if folded != line:
            changed += 1

    return _dedupe("\n".join(out), changed)


def _dedupe(text: str, changed: int) -> Tidied:
    """Drop an appointment line that says exactly the same thing twice.

    This is what happens where two writers met: the old one put its long block
    back while the short line was already there, and folding the block then
    produces that same line a second time. The copy with something written
    under it is the one to keep — that is where the notes are.
    """
    lines = text.split("\n")
    keep = [True] * len(lines)
    seen: dict[str, int] = {}
    has_child = lambda i: bool(INDENTED.match(lines[i + 1])) if i + 1 < len(lines) else False  # noqa: E731

    for i, line in enumerate(lines):
        if not SHORT_LINE.match(line):
            continue
        # Two lines for the same appointment need not read the same: the old
        # writer put a day counter behind a multi-day title and the short form
        # does not, so they are compared without it.
        key = DAY_COUNTER_ANY.sub("", line)
        prev = seen.get(key)
        if prev is None:
            seen[key] = i
            continue
        if has_child(i) != has_child(prev):
            # Whichever has something written under it is the one to keep.
            drop = prev if has_child(i) else i
        else:
            # Otherwise the one that says which day it is says more.
            drop = i if DAY_COUNTER_ANY.search(lines[prev]) else prev
        keep[drop] = False
        changed += 1
        if drop == prev:
            seen[key] = i

    return Tidied(text="\n".join(l for l, k in zip(lines, keep) if k), changed=changed)
