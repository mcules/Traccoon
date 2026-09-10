"""One day's appointments into that day's note, and the day one left.

The writing itself is `daily.apply_lines`, which is pure and knows nothing about
databases; what stands here is everything around it that does — reading the
note, creating it when it is not there yet, working out that an appointment has
moved away, and keeping the index of where each line was put.

Two things are worth saying about the shape of it.

**A day is reconciled, not appended to.** The appointments of that day are what
the calendar says now, and a line in the note whose appointment is no longer
among them has to be accounted for: it moved, or it was called off, or it was
deleted. Left alone, the note would go on claiming a meeting that is not
happening — which is the failure the person actually notices.

**Where an appointment WENT cannot be seen from the day it left.** That day's
events simply do not contain it. So the whole fetched window is searched for the
same block id, and only when it turns up somewhere else is the old line marked
as moved. Not finding it means the appointment is gone from the calendar
altogether, and then the line is left exactly as it stands: a deletion in a feed
is not a reason to rewrite somebody's note.
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.notes import NotesCalendarMark
from . import daily as cal_daily
from .fetch import Event, Snapshot, on_day

log = logging.getLogger("notes.calendar.sync")


def _ids_in(content: str) -> dict[str, int]:
    """Every appointment line's block id, and the line it stands on."""
    out: dict[str, int] = {}
    for i, line in enumerate(content.split("\n")):
        if cal_daily.INDENTED.match(line):
            continue                       # a sub-bullet is the reader's own note
        bare, ident = cal_daily.split_block_id(line)
        if ident and cal_daily.EVENT_LINE.match(bare):
            out[ident] = i
    return out


def where_now(snapshot: Snapshot, ident: str, *, not_on: str) -> Event | None:
    """The appointment with that block id, on some other day than `not_on`.

    The earliest one, so a series that lost a single occurrence points at the
    next one rather than at some date a year out.
    """
    later = sorted((e for e in snapshot.events
                    if e.date != not_on and cal_daily.block_id(e) == ident),
                   key=lambda e: (e.date, e.time))
    return later[0] if later else None


def label_for(day: _dt.date) -> str:
    """How the link to another day reads. Short, because it sits mid-sentence."""
    return day.strftime("%d.%m.%Y")


async def record_marks(db: AsyncSession, user_id: int, note_path: str,
                       day: _dt.date, present: dict[str, tuple[str, str]]) -> None:
    """Note where each line was put; forget the ones that are no longer there.

    `present` is block id → (UID, kind of series). Rows for this note that are not in it any more
    are dropped rather than kept: the table is meant to say where a line IS, and
    a stale row would send a later move to a note that has not carried that
    appointment for weeks.
    """
    rows = (await db.execute(
        select(NotesCalendarMark).where(NotesCalendarMark.user_id == user_id,
                                        NotesCalendarMark.note_path == note_path))
            ).scalars().all()
    known = {r.block_id: r for r in rows}
    now = _dt.datetime.now(_dt.timezone.utc)
    for ident, (uid, series) in present.items():
        row = known.pop(ident, None)
        if row is None:
            db.add(NotesCalendarMark(user_id=user_id, block_id=ident, note_path=note_path,
                                     day=day, uid=uid, series=series))
        else:
            row.day, row.uid, row.series, row.seen_at = day, uid, series, now
    for gone in known.values():
        await db.execute(delete(NotesCalendarMark).where(NotesCalendarMark.id == gone.id))


async def notes_carrying(db: AsyncSession, user_id: int, ident: str,
                         *, except_path: str) -> list[NotesCalendarMark]:
    """Which other notes still have a line with that block id.

    This is the whole reason the index exists: an appointment can move out of a
    day nobody is syncing right now, and reading the vault to find its old line
    would cost a pass over every note.
    """
    return list((await db.execute(
        select(NotesCalendarMark).where(NotesCalendarMark.user_id == user_id,
                                        NotesCalendarMark.block_id == ident,
                                        NotesCalendarMark.note_path != except_path))
                 ).scalars().all())


# How many occurrences of a never-ending series are written ahead. Two, so there
# is always a next one to hang a note on before the first has happened.
AHEAD = 2


def endless_ahead(snapshot: Snapshot, today: str, *, ahead: int = AHEAD) -> dict[str, list[str]]:
    """The next few days of each series that never ends, by UID.

    Three quarters of the appointments here come from such a series — 592 of 803
    on the day this was written, and one weekly class accounts for 57 of them.
    Writing them all out would put a line into every note of the next year, and
    the note of a Tuesday in eight months is not a note anybody asked for.

    Two is enough to write a note against the next one and the one after it,
    which is what they are written for at all. The window slides on its own: what
    is second today is first next week, and a third appears behind it.
    """
    days: dict[str, list[str]] = {}
    for event in snapshot.events:
        if event.series == "endless" and event.date >= today:
            days.setdefault(event.uid, []).append(event.date)
    return {uid: sorted(set(d))[:ahead] for uid, d in days.items()}


def writable_on(snapshot: Snapshot, day: str, ahead: dict[str, list[str]]) -> list[Event]:
    """The appointments of one day that belong in its note.

    Everything except the far reaches of a never-ending series: those are held
    to `endless_ahead`. A day in the past keeps whatever was written when it was
    still ahead — this only decides what gets written now.
    """
    return [e for e in on_day(snapshot, day)
            if e.series != "endless" or day in ahead.get(e.uid, ())]


def days_to_visit(snapshot: Snapshot, today: str, *, window: int,
                  since: str, ahead: dict[str, list[str]]) -> list[str]:
    """Which days a run has to look at, rather than a fixed stretch of them.

    A window alone is the wrong question. An appointment moved into next
    October is a change now, and waiting until October to write it down is the
    same as not writing it. So three sets come together:

    * the near window, which is where notes are also created,
    * every day an appointment changed on since the last run, however far out,
    * the days the next occurrences of the endless series fall on.

    `since` empty means "no idea when we last ran" and pulls in every day that
    has an appointment at all — right for a first run, and it settles after it.
    """
    first = _dt.date.fromisoformat(today)
    out = {(first + _dt.timedelta(days=i)).isoformat() for i in range(window)}
    for event in snapshot.events:
        if event.series == "endless" and event.date not in ahead.get(event.uid, ()):
            continue
        if not since or not event.changed_at or event.changed_at > since:
            out.add(event.date)
    for dates in ahead.values():
        out.update(dates)
    return sorted(out)


def verdict(days_of: dict[str, set[str]], ident: str, on: str,
            *, reach: tuple[str, str]) -> tuple[str, str | None]:
    """What became of the appointment whose line stands on day `on`.

    Pulled out of the walk so the rule can be read and tested on its own — it is
    the one piece here that is easy to get subtly wrong, and getting it wrong
    rewrites lines in somebody's notes.

    Four answers, and the difference between them is the whole point:

    * `("unknown", None)` — the day lies outside what was fetched. No events
      there means "not asked about", not "nothing happens".
    * `("stay", None)`    — the appointment still occurs on that day.
    * `("moved", day)`    — it occurs, but elsewhere.
    * `("gone", None)`    — it occurs nowhere in the whole window any more. The
      caller still has to check that the calendar it came from actually
      answered before believing this.
    """
    if not (reach[0] <= on <= reach[1]):
        return "unknown", None
    occurs = days_of.get(ident, set())
    if on in occurs:
        return "stay", None
    if not occurs:
        return "gone", None
    later = sorted(d for d in occurs if d > on)
    return "moved", (later or sorted(occurs))[0]


async def reconcile_moves(db: AsyncSession, user, ws, options, snapshot: Snapshot,
                          *, dry_run: bool = False) -> int:
    """Mark the days appointments left, including days nobody is syncing.

    This is what the index is for. A day inside the window notices for itself
    that a line no longer belongs to it. A day outside it never gets read, so its
    note would go on saying a meeting takes place there long after it moved — and
    the further out somebody plans, the more likely that is exactly what happened.

    Walks the marks, not the appointments, and that is not a detail. A block id
    belongs to the appointment, not to the occurrence: every Monday of a weekly
    meeting carries the same one. Asking "is this id somewhere else?" therefore
    answers yes for every other week of every series — a first attempt read 302
    moves out of four appointments that had not moved at all. The question that
    holds is the other one: **does this appointment still occur on the day this
    line is on?**

    A day the snapshot does not reach cannot be judged and is left alone: no
    events there means "not fetched", not "nothing happens".
    """
    from ...notes.calendar import daily as cd

    if not snapshot.events:
        return 0
    days_of: dict[str, set[str]] = {}
    for event in snapshot.events:
        days_of.setdefault(cd.block_id(event), set()).add(event.date)
    reach_from = min(e.date for e in snapshot.events)
    reach_to = max(e.date for e in snapshot.events)

    rows = list((await db.execute(
        select(NotesCalendarMark).where(NotesCalendarMark.user_id == user.id))
                 ).scalars().all())
    # A calendar that could not be read carries no appointments either. Striking a
    # day's meetings through because a server was briefly down would be the worst
    # kind of wrong: quiet, plausible, and in somebody's record of what they did.
    silent = {e.get("calendar") for e in snapshot.errors}

    today = _dt.date.today().isoformat()
    ended: list[tuple[str, str, str]] = []
    marked = 0
    for row in rows:
        kind, gone_to = verdict(days_of, row.block_id, row.day.isoformat(),
                                reach=(reach_from, reach_to))
        if kind in ("unknown", "stay"):
            continue
        try:
            content = ws.vault.read_text(row.note_path)
        except OSError:
            continue                        # the note is gone; the row goes with it

        if kind == "moved":
            target_day = _dt.date.fromisoformat(gone_to or "")
            belongs = cd.daily_note_path(target_day, options.daily_folder,
                                         options.daily_format)
            if belongs == row.note_path:
                continue
            out = cd.mark_moved(content, row.block_id, belongs, label_for(target_day))
            what = f"moved to {belongs}"
        else:
            if _calendar_of(content, row.block_id) in silent:
                continue                    # its feed did not answer; say nothing
            # A series that ended is cleared out of the days it had not reached
            # yet: a struck-through phantom in every note of the next year is
            # noise, not history. A single appointment that was dropped stays —
            # it had been planned, and that is the record.
            future = row.day > _dt.date.fromisoformat(today)
            if row.series and future:
                out, blocked = cd.remove_line(content, row.block_id)
                if blocked:
                    out = cd.mark_gone(content, row.block_id)
                    what = "gone; kept, notes are written under it"
                else:
                    ended.append((row.block_id, _title_of(content, row.block_id),
                                  row.day.isoformat()))
                    what = "series ended, line removed"
            else:
                out = cd.mark_gone(content, row.block_id)
                what = "gone from the calendar"

        if not out.updated:
            continue
        marked += 1
        if not dry_run:
            ws.save(row.note_path, out.text)
            if what.startswith("series ended"):
                # The line is gone, so the mark pointing at it has to go too, or
                # the next run would look for it and find nothing forever.
                await db.execute(delete(NotesCalendarMark)
                                 .where(NotesCalendarMark.id == row.id))
            log.info("appointment %s in %s: %s", row.block_id, row.note_path, what)
    if ended and not dry_run:
        _record_endings(ws, options, today, ended)
    return marked


def _title_of(content: str, ident: str) -> str:
    for line in content.split("\n"):
        bare, found = split_id(line)
        if found != ident:
            continue
        m = cal_daily.EVENT_LINE.match(bare)
        return (m.group(3) or "").strip().replace("~~", "") if m else ""
    return ""


def _record_endings(ws, options, today: str, ended: list[tuple[str, str, str]]) -> None:
    """One line in today's note per series that stopped.

    The counterpart to clearing the future days out. Something was removed from
    the vault, and a removal nobody can see afterwards is the kind of tidying
    that costs trust — so the day it happened says what happened.
    """
    per: dict[str, list[str]] = {}
    for _, title, day in ended:
        per.setdefault(title or "(ohne Titel)", []).append(day)
    rel = cal_daily.daily_note_path(_dt.date.fromisoformat(today),
                                    options.daily_folder, options.daily_format)
    try:
        content = ws.vault.read_text(rel)
    except OSError:
        return                              # no note today: nothing to write into
    lines = []
    for title, days in sorted(per.items()):
        days.sort()
        lines.append(f"- {cal_daily.NEXT_MARK} Serie \u201e{title}\u201c beendet \u2014 "
                     f"{len(days)} k\u00fcnftige Termine aus den Tagesnotizen entfernt "
                     f"(bis {days[-1]}).")
    ws.save(rel, _append_under(content, "# Notizen", "\n".join(lines)))


def _append_under(content: str, heading: str, text: str) -> str:
    """At the end of that section, or at the end of the note when it has none."""
    lines = content.split("\n")
    at = next((i for i, l in enumerate(lines) if l.strip() == heading), -1)
    if at < 0:
        return content.rstrip("\n") + "\n\n" + text + "\n"
    end = len(lines)
    for i in range(at + 1, len(lines)):
        if cal_daily.ANY_HEADING.match(lines[i]):
            end = i
            break
    while end > at + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = text.split("\n")
    return "\n".join(lines)


def _calendar_of(content: str, ident: str) -> str:
    """Which calendar the line with that block id names, or "" if it names none."""
    for line in content.split("\n"):
        bare, found = split_id(line)
        if found != ident:
            continue
        m = cal_daily.EVENT_LINE.match(bare)
        return (m.group(4) or "").strip() if m else ""
    return ""


def split_id(line: str) -> tuple[str, str]:
    return cal_daily.split_block_id(line)
