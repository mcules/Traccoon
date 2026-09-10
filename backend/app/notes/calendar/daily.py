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

import hashlib
import re
from dataclasses import dataclass

from .fetch import Event

HEADING = "# Termine"
# The end time is optional and is thrown away: a line may have been written as
# `14:00-14:50`, by a person or by an earlier writer, and a pattern that only
# knew `14:00 ` did not recognise it. That is not a cosmetic miss — an
# unrecognised line is not found again, so the appointment is appended a second
# time and the day shows it twice. Both dashes count, the hyphen and the en dash.
EVENT_LINE = re.compile(
    r"^(\s*)- (?:(\d{2}:\d{2})(?:[-\u2013]\d{2}:\d{2})? )?(?:~~)?(.+?)(?:~~)?(?: · ([^·]+))?\s*$")
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
# Obsidian's block id at the end of a line. Same character set as the editor's
# own rule in `livePreview.ts`, so what is written here is what that hides.
BLOCK_ID = re.compile(r"[ \t]+\^([a-zA-Z0-9-]+)[ \t]*$")
# What this house writes UNDER an appointment: where it went, or that it was
# called off. It sits where the reader's own notes sit, so it needs a mark of its
# own — without one the next sync could not tell its own sentence from somebody
# else's and would either write a second copy or overwrite a note.
MARK = "\u21aa"                     # ↪  what became of it
NEXT_MARK = "\u27f3"                # ⟳  where the series goes next
ANNOTATION = re.compile(rf"^(?:\t|\s{{2,}})\s*{MARK}\s")
# Both marks together: the block of lines under an appointment that this house
# owns. Two kinds, because they answer different questions and one must not
# push the other out — a cancelled occurrence of a series is still part of a
# series. Anything else indented under an appointment is somebody's own note and
# is never touched.
HOUSE = re.compile(rf"^(?:\t|\s{{2,}})\s*[{MARK}{NEXT_MARK}]\s")
# The same indent the agenda blocks use, so the two line up under an appointment.
INDENT = "    "


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


def block_id(event: Event) -> str:
    """The name this appointment answers to in a note.

    Obsidian's own block id (`^name` at the end of a line), so both readers
    already know what it is: the reading view drops it, the editor shows it only
    while the caret is in that line, and it can be linked to.

    Computed from the UID and nothing else, which is the whole point. The index
    in the database is then only an index — throw it away, copy the vault to
    another machine, and a line still says which appointment it is. An id handed
    out by a counter would have made the database the truth and the vault a
    printout of it.

    `Event.id` would have been the obvious choice and is the wrong one: it is
    `uid@start`, so it changes the moment an appointment is moved — which is
    exactly the case this has to survive. Occurrences of a series do share an id
    this way, but they live in different notes, and a block id only ever has to
    be unique inside its own note.
    """
    return "ev-" + hashlib.sha1(event.uid.encode("utf-8")).hexdigest()[:8]


def split_block_id(line: str) -> tuple[str, str]:
    """`(line without its block id, the id)` — the id is "" when there is none."""
    m = BLOCK_ID.search(line)
    return (line[: m.start()].rstrip(), m.group(1)) if m else (line, "")


def line_for(event: Event) -> str:
    """One appointment as one line.

    With the end time where there is one: "when is it" is a span, not a moment,
    and a list that only says when things start makes the reader work out from
    the next entry how long they have. An end that equals the start says nothing
    and is left off.
    """
    if event.allDay:
        time = ""
    elif event.endTime and event.endTime != event.time:
        time = f"{event.time}\u2013{event.endTime} "
    else:
        time = f"{event.time} "
    title = f"~~{event.title}~~" if event.cancelled else event.title
    return f"- {time}{title} · {event.calendar} ^{block_id(event)}"


def matches(line: str, event: Event) -> bool:
    """Does this line describe that appointment?"""
    bare, ident = split_block_id(line)
    m = EVENT_LINE.match(bare)
    if not m:
        return False
    indent, time, title, calendar = m.groups()
    if indent:
        return False                       # a sub-bullet is the reader's own note
    if ident:
        # A line that carries a name is answered by the name alone. Everything
        # else about it may have changed — that is what the name is for: an
        # appointment renamed in the calendar used to look like a different one
        # and landed in the note a second time.
        return ident == block_id(event)
    # Nothing written by this house yet: fall back to what it looked like before
    # there were ids, so the lines already in the vault keep being recognised.
    if (calendar or "").strip() != event.calendar:
        return False
    # A moved appointment keeps its title: same title, same calendar, other time.
    written = title.strip().replace("~~", "")
    return written == event.title or DAY_COUNTER.sub("", written) == event.title


def annotation(text: str) -> str:
    """One line of this house's own, under the appointment it belongs to."""
    return f"{INDENT}{MARK} {text}"


def next_note(target_note: str, label: str) -> str:
    """Where this series meets again. A link, so it is a button in both readers."""
    return f"{INDENT}{NEXT_MARK} nächster Termin: [[{target_note}|{label}]]"


def moved_note(target_note: str, label: str) -> str:
    """Where an appointment went. The link is the button: a wikilink is a link in
    both readers, so nothing has to be rendered for it to be clickable."""
    return annotation(f"verschoben auf [[{target_note}|{label}]]")


CANCELLED_NOTE = annotation("abgesagt")
# An appointment that is no longer in the feed at all. Different from cancelled,
# which the feed still carries and still calls an appointment.
GONE_NOTE = annotation("entfällt")


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

    # Lines this pass has already dealt with: the annotation under an appointment
    # is rewritten together with it, so it must not be copied over a second time
    # when the loop reaches it.
    consumed: set[int] = set()

    for i, line in enumerate(section):
        if i in consumed:
            continue
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
        # The house's own note under the appointment. An appointment that is on
        # this day says only whether it was called off — where one WENT is written
        # by `mark_moved` on the day it left, which this pass never sees.
        following = section[i + 1] if i + 1 < len(section) else ""
        had = ANNOTATION.match(following)
        if had:
            consumed.add(i + 1)
        want_note = CANCELLED_NOTE if event.cancelled else ""
        if want_note:
            out.append(want_note)
            if not had:
                updated += 1
        elif had:
            updated += 1                  # a cancellation withdrawn: the note goes
        # A recurring appointment can bring its own agenda — but only once: if
        # anything is written under it already, that is the note and it stays.
        template = _template_for(templates, event.title)
        after = section[i + 2] if had and i + 2 < len(section) else following
        if template and not INDENTED.match(after):
            out.extend(template.lines)

    fresh = [e for e in events if e.id not in seen]
    if fresh:
        # At the end of the section, before its trailing blank lines.
        while out and not out[-1].strip():
            out.pop()
        for event in fresh:
            out.append(line_for(event))
            added += 1
            if event.cancelled:
                out.append(CANCELLED_NOTE)
            template = _template_for(templates, event.title)
            if template:
                out.extend(template.lines)
        out.append("")

    return Result(text="\n".join(lines[:head + 1] + out + lines[end:]),
                  added=added, updated=updated, cancelled=cancelled)


KEEP = object()                     # "leave that annotation as it is"


def _house_under(lines: list[str], at: int) -> tuple[list[str], int]:
    """The house's own annotation lines directly under `at`, and how many."""
    n = 0
    while at + 1 + n < len(lines) and HOUSE.match(lines[at + 1 + n]):
        n += 1
    return lines[at + 1: at + 1 + n], n


def annotate(content: str, ident: str, *, state=KEEP, nav=KEEP,
             strike: bool = False) -> Result:
    """Change what this house says about the appointment with that block id.

    `state` is what became of it (called off, moved away, fell away) and `nav` is
    where its series meets next; either can be a line, None to take it away, or
    left alone. They are kept apart because they answer different questions — an
    occurrence can be cancelled AND still be part of a series that goes on.

    Whatever the reader wrote under the appointment stays where it is. That is
    the whole reason for a marked block: without it, this could not tell its own
    sentence from somebody else's.
    """
    lines = content.split("\n")
    for i, line in enumerate(lines):
        bare, found = split_block_id(line)
        if found != ident or INDENTED.match(line):
            continue
        m = EVENT_LINE.match(bare)
        if not m:
            continue

        before = list(lines)
        if strike and "~~" not in bare:
            _, time, title, calendar = m.groups()
            head = f"- {time + ' ' if time else ''}~~{title.strip()}~~"
            lines[i] = f"{head} · {calendar.strip()} ^{found}" if calendar else f"{head} ^{found}"

        had, count = _house_under(lines, i)
        keep_state = next((l for l in had if ANNOTATION.match(l)), None)
        keep_nav = next((l for l in had if not ANNOTATION.match(l)), None)
        want = [x for x in (keep_state if state is KEEP else state,
                            keep_nav if nav is KEEP else nav) if x]
        lines[i + 1: i + 1 + count] = want
        return (Result(text="\n".join(lines), updated=1) if lines != before
                else Result(text=content))
    return Result(text=content)


def remove_line(content: str, ident: str) -> tuple[Result, bool]:
    """Take the appointment with that block id out of the note altogether.

    For a series that ended: its future occurrences never happen, and a
    struck-through phantom in every note of the next year is noise rather than
    history. The one-off that was simply dropped is kept instead — that had been
    planned, and the record of it is worth something.

    Refuses when the reader wrote something under it, and says so in the second
    value. Their sentences are not this program's to throw away, and an
    appointment somebody made notes against is one they cared about. The caller
    then strikes it through instead.
    """
    lines = content.split("\n")
    for i, line in enumerate(lines):
        bare, found = split_block_id(line)
        if found != ident or INDENTED.match(line):
            continue
        if not EVENT_LINE.match(bare):
            continue
        _, count = _house_under(lines, i)
        after = lines[i + 1 + count] if i + 1 + count < len(lines) else ""
        if INDENTED.match(after):
            return Result(text=content), True          # somebody wrote under it
        del lines[i: i + 1 + count]
        return Result(text="\n".join(lines), updated=1), False
    return Result(text=content), False


def mark_next(content: str, ident: str, target_note: str, label: str) -> Result:
    """Point an occurrence of a series at the next one."""
    return annotate(content, ident, nav=next_note(target_note, label))


def mark_gone(content: str, ident: str) -> Result:
    """Say that an appointment is not in the calendar any more.

    Only ever on a calendar that answered. A feed that could not be read carries
    no appointments either, and striking a day's meetings through because a
    server was briefly down would be the worst kind of wrong: quiet, plausible,
    and in somebody's record of what they did.
    """
    return annotate(content, ident, state=GONE_NOTE, strike=True)


def mark_moved(content: str, ident: str, target_note: str, label: str) -> Result:
    """Say, on the day an appointment left, where it went.

    The day it moved TO gets its line the ordinary way; this is the other half,
    and it is the half a sync cannot work out on its own: the appointment is
    simply not among that day's events any more, and without this the line would
    either be left standing as though it still took place, or quietly dropped
    together with whatever was written under it.

    The link underneath is the way to the new day; a wikilink is a link in both
    readers, so there is nothing to render for it to be clickable.
    """
    return annotate(content, ident, state=moved_note(target_note, label), strike=True)


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


def _path_pattern(folder: str, fmt: str) -> tuple[re.Pattern, list[str]] | None:
    """The path rule as something that can be read backwards.

    Built from the same `TOKENS` the forward direction uses, so a vault that
    files its days as `DD.MM.YYYY` or `YYYY/MM/DD` is understood as readily as
    this one's `YYYY/MM/YYYY-MM-DD`. Everything between the tokens is taken
    literally — a dot in a format is a dot, not "any character".

    A token may appear more than once (the year does, above); each occurrence
    gets its own group and they are checked against each other afterwards.
    """
    groups = {"YYYY": r"(\d{4})", "YY": r"(\d{2})", "MM": r"(\d{2})", "DD": r"(\d{2})"}
    order: list[str] = []
    out, rest = "", fmt
    while rest:
        for token, _ in TOKENS:
            if rest.startswith(token):
                out += groups[token]
                order.append(token)
                rest = rest[len(token):]
                break
        else:
            out += re.escape(rest[0])
            rest = rest[1:]
    if not ({"YYYY", "YY"} & set(order)) or "MM" not in order or "DD" not in order:
        return None            # not a rule that names a single day
    head = re.escape(folder.strip("/") + "/") if folder.strip("/") else ""
    # The order comes back beside the pattern: a group cannot carry a name that
    # repeats, and the year repeats in this vault's own format.
    return re.compile(rf"\A{head}{out}\.(?:md|markdown)\Z", re.IGNORECASE), order


def day_of_daily_path(rel: str, folder: str, fmt: str):
    """Which day a path is the daily note of, or None if it is not one.

    The inverse of `daily_note_path`, built from the same rule so the format
    stays defined in exactly one place. Whatever the pattern reads out is handed
    back through the forward direction, and only a path that comes out identical
    counts — a name that merely looks like a date, filed somewhere else entirely,
    is not mistaken for one.

    What it refuses matters more than what it accepts. This decides whether
    opening a note that is not there creates a file, so a rule that guessed would
    leave notes behind in places nobody asked for.
    """
    import datetime as _d

    built = _path_pattern(folder, fmt)
    if built is None:
        return None
    pattern, order = built
    m = pattern.match(rel)
    if not m:
        return None

    parts: dict[str, str] = {}
    for token, value in zip(order, m.groups()):
        if parts.setdefault(token, value) != value:
            return None                  # the same token twice, saying two things
    try:
        year = (int(parts["YYYY"]) if "YYYY" in parts
                else _d.datetime.strptime(parts["YY"], "%y").year)
        day = _d.date(year, int(parts["MM"]), int(parts["DD"]))
    except ValueError:
        return None
    return day if daily_note_path(day, folder, fmt) == rel else None


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
