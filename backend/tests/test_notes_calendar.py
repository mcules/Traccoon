"""Turning a calendar feed into appointments.

An ICS feed is a set of rules, not a list, and every test here stands for a rule
that is easy to expand slightly wrongly — in a way that shows up as an
appointment at the wrong hour or a meeting that quietly stops a fortnight early,
rather than as an error.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.notes.calendar import fetch as cal
from app.notes.calendar import store


def ics(*blocks: str) -> str:
    body = "\n".join(blocks)
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
            f"{body}\nEND:VCALENDAR\n")


def event(**fields: str) -> str:
    lines = "\n".join(f"{k}:{v}" for k, v in fields.items())
    return f"BEGIN:VEVENT\n{lines}\nEND:VEVENT"


def days(text: str, first: str, last: str) -> list[tuple[str, str, str]]:
    found = cal.expand(text, "Test", dt.date.fromisoformat(first), dt.date.fromisoformat(last))
    return [(e.date, e.time, e.title) for e in found]


# ------------------------------------------------------------------- basics

def test_one_appointment_comes_back_with_its_local_time() -> None:
    text = ics(event(UID="1", SUMMARY="Besprechung",
                     DTSTART="20260902T090000Z", DTEND="20260902T100000Z"))
    out = cal.expand(text, "Test", dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    assert len(out) == 1
    assert out[0].title == "Besprechung"
    assert out[0].allDay is False
    assert out[0].time and out[0].endTime


def test_a_whole_day_ends_on_the_day_it_is_on() -> None:
    """In a feed the end of an all-day event is the next morning — a one-day
    event ends on the day after. Taken literally it would show as two days."""
    text = ics(event(UID="2", SUMMARY="Feiertag",
                     **{"DTSTART;VALUE=DATE": "20260902", "DTEND;VALUE=DATE": "20260903"}))
    out = cal.expand(text, "Test", dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    assert [(e.date, e.allDay) for e in out] == [("2026-09-02", True)]
    assert out[0].time == "" and out[0].endTime == ""


def test_an_event_without_a_title_still_has_one() -> None:
    text = ics(event(UID="3", DTSTART="20260902T090000Z", DTEND="20260902T100000Z"))
    assert cal.expand(text, "Test", dt.date(2026, 9, 1), dt.date(2026, 9, 3))[0].title


# --------------------------------------------------------------- repetition

def test_a_weekly_series_repeats() -> None:
    text = ics(event(UID="4", SUMMARY="Jour fixe",
                     DTSTART="20260907T080000Z", DTEND="20260907T090000Z",
                     RRULE="FREQ=WEEKLY;COUNT=3"))
    assert [d for d, _, _ in days(text, "2026-09-01", "2026-10-01")] == \
        ["2026-09-07", "2026-09-14", "2026-09-21"]


def test_a_cancelled_occurrence_is_gone() -> None:
    text = ics(event(UID="5", SUMMARY="Jour fixe",
                     DTSTART="20260907T080000Z", DTEND="20260907T090000Z",
                     RRULE="FREQ=WEEKLY;COUNT=3", EXDATE="20260914T080000Z"))
    assert [d for d, _, _ in days(text, "2026-09-01", "2026-10-01")] == \
        ["2026-09-07", "2026-09-21"]


def test_a_moved_occurrence_keeps_its_own_time() -> None:
    """One of a series was shifted. It must appear once, where it was moved to,
    and not a second time where the rule would have put it."""
    text = ics(
        event(UID="6", SUMMARY="Jour fixe",
              DTSTART="20260907T080000Z", DTEND="20260907T090000Z",
              RRULE="FREQ=WEEKLY;COUNT=2"),
        event(UID="6", SUMMARY="Jour fixe (verschoben)",
              **{"RECURRENCE-ID": "20260914T080000Z"},
              DTSTART="20260915T120000Z", DTEND="20260915T130000Z"),
    )
    found = days(text, "2026-09-01", "2026-10-01")
    assert [d for d, _, _ in found] == ["2026-09-07", "2026-09-15"]
    assert found[1][2] == "Jour fixe (verschoben)"


def test_the_last_occurrence_of_a_series_is_not_lost(monkeypatch) -> None:
    """`UNTIL` is an instant in UTC, and the last occurrence often sits just
    inside it. The service this replaces dropped exactly that one: a fortnightly
    meeting stopped showing two weeks before it actually ended, and nothing said
    so. The dates here are the real ones that found it."""
    text = ics(event(UID="7", SUMMARY="Staffing",
                     **{"DTSTART;TZID=Europe/Berlin": "20261207T090000"},
                     **{"DTEND;TZID=Europe/Berlin": "20261207T093000"},
                     RRULE="FREQ=WEEKLY;INTERVAL=2;UNTIL=20261221T083000Z"))
    # 09:00 in Berlin in December is 08:00 UTC, and UNTIL is half an hour later.
    assert [d for d, _, _ in days(text, "2026-12-01", "2026-12-31")] == \
        ["2026-12-07", "2026-12-21"]


def test_a_series_in_a_zone_without_daylight_saving_keeps_its_hour() -> None:
    """The occurrence is read on the clock the series was written on, not on the
    reader's. A zone without a summer shift therefore moves against the local
    one twice a year — and that is correct, not a fault to be corrected."""
    text = ics(event(UID="8", SUMMARY="Fest",
                     **{"DTSTART;TZID=Etc/GMT-1": "20260601T180000"},
                     **{"DTEND;TZID=Etc/GMT-1": "20260601T190000"},
                     RRULE="FREQ=MONTHLY;COUNT=8"))
    found = {d: t for d, t, _ in days(text, "2026-06-01", "2027-02-01")}
    assert found["2026-06-01"] == "19:00"        # summer here, so an hour later
    assert found["2026-12-01"] == "18:00"        # winter, and the clocks agree


# -------------------------------------------------------------- the fetching

@pytest.mark.asyncio
async def test_one_unreachable_feed_does_not_empty_the_calendar(monkeypatch) -> None:
    """Keep what the others gave, and say which one failed."""
    good = ics(event(UID="9", SUMMARY="Da", DTSTART="20260902T090000Z",
                     DTEND="20260902T100000Z"))

    async def read(source):
        if source.name == "kaputt":
            raise RuntimeError("HTTP 500")
        return good

    monkeypatch.setattr(cal, "read_feed", read)
    snapshot = await cal.refresh([cal.Source(name="kaputt", url="x"),
                                  cal.Source(name="heil", url="y")])
    assert [e.calendar for e in snapshot.events] == ["heil"]
    assert snapshot.errors == [{"calendar": "kaputt", "message": "HTTP 500"}]
    assert snapshot.fetched_at


@pytest.mark.asyncio
async def test_a_day_is_ordered_with_the_whole_day_first() -> None:
    text = ics(
        event(UID="a", SUMMARY="Mittags", DTSTART="20260902T100000Z", DTEND="20260902T110000Z"),
        event(UID="b", SUMMARY="Früh", DTSTART="20260902T060000Z", DTEND="20260902T070000Z"),
        event(UID="c", SUMMARY="Ganztags",
              **{"DTSTART;VALUE=DATE": "20260902", "DTEND;VALUE=DATE": "20260903"}),
    )
    snapshot = cal.Snapshot(events=cal.expand(text, "Test", dt.date(2026, 9, 1),
                                              dt.date(2026, 9, 3)))
    assert [e.title for e in cal.on_day(snapshot, "2026-09-02")] == \
        ["Ganztags", "Früh", "Mittags"]


@pytest.mark.asyncio
async def test_no_calendars_is_an_empty_answer_and_not_an_error() -> None:
    store.forget()
    snapshot = await store.ensure(1, [])
    assert snapshot.events == [] and snapshot.errors == []
    assert snapshot.fetched_at
    store.forget()


@pytest.mark.asyncio
async def test_what_was_fetched_is_kept_until_it_is_asked_for_again(monkeypatch) -> None:
    """Reading five feeds over the network per view of a calendar would put a
    second of somebody else's server in front of every click."""
    store.forget()
    calls = {"n": 0}

    async def read(source):
        calls["n"] += 1
        return ics(event(UID="z", SUMMARY="X", DTSTART="20260902T090000Z",
                         DTEND="20260902T100000Z"))

    monkeypatch.setattr(cal, "read_feed", read)
    sources = [cal.Source(name="A", url="x")]
    await store.ensure(2, sources)
    await store.ensure(2, sources)
    assert calls["n"] == 1
    await store.ensure(2, sources, force=True)
    assert calls["n"] == 2
    store.forget()


# --------------------------------------------------- appointments in a note

from app.notes.calendar import daily as cd            # noqa: E402


def ev(title: str, time: str = "", calendar: str = "Privat", *, cancelled: bool = False,
       date: str = "2026-09-02") -> cal.Event:
    return cal.Event(id=f"{title}@{date}{time}", uid=title, calendar=calendar,
                     title=title, date=date, time=time,
                     endTime=time, allDay=not time,
                     start=f"{date}T{time or '00:00'}", end=f"{date}T23:59",
                     cancelled=cancelled)


NOTE = """# Tag

etwas eigenes

# Termine

- 09:00 Daily Dev · Vostura
\t- was ich mir dazu notiert habe

# Danach

steht auch noch etwas
"""


def test_a_note_without_the_heading_is_not_touched() -> None:
    """Writing the section in would be this deciding what somebody's note looks
    like. A note that has no such section is a note that does not want one."""
    out = cd.apply_lines("nur Text\n", [ev("Etwas", "10:00")])
    assert out.text == "nur Text\n"
    assert out.added == 0


def test_a_new_appointment_lands_in_the_section_and_nowhere_else() -> None:
    out = cd.apply_lines(NOTE, [ev("Daily Dev", "09:00", "Vostura"),
                                ev("Hausarzt", "13:45")])
    assert out.added == 1
    assert "- 13:45 Hausarzt · Privat" in out.text
    # everything around it survives
    assert "etwas eigenes" in out.text
    assert out.text.count("# Danach") == 1
    assert "steht auch noch etwas" in out.text


def test_what_was_written_under_an_appointment_stays_there() -> None:
    """That is the whole point of matching a line instead of rewriting the
    section: the sub-bullets are somebody's notes."""
    out = cd.apply_lines(NOTE, [ev("Daily Dev", "09:00", "Vostura")])
    assert "\t- was ich mir dazu notiert habe" in out.text
    assert out.added == 0 and out.updated == 0


def test_an_appointment_that_moved_keeps_its_line() -> None:
    """Same title, same calendar, another time — the line is rewritten rather
    than a second one added, so what is written under it stays with it."""
    out = cd.apply_lines(NOTE, [ev("Daily Dev", "11:00", "Vostura")])
    assert out.updated == 1 and out.added == 0
    assert "- 11:00 Daily Dev · Vostura" in out.text
    assert "- 09:00 Daily Dev · Vostura" not in out.text
    assert "\t- was ich mir dazu notiert habe" in out.text


def test_a_cancelled_appointment_is_struck_through_and_kept() -> None:
    """Removing it would take the notes written under it with it."""
    out = cd.apply_lines(NOTE, [ev("Daily Dev", "09:00", "Vostura", cancelled=True)])
    assert "- 09:00 ~~Daily Dev~~ · Vostura" in out.text
    assert out.cancelled == 1


def test_a_whole_day_appointment_has_no_time_in_front_of_it() -> None:
    out = cd.apply_lines(NOTE, [ev("Feiertag")])
    assert "- Feiertag · Privat" in out.text


def test_an_agenda_is_written_once_and_not_again() -> None:
    """A recurring appointment can bring its running order. If anything is
    written under it already, that is the note and it stays."""
    templates = [cd.Template(match="Daily Dev", lines=["    - Punkt eins"])]
    first = cd.apply_lines(NOTE, [ev("Daily Dev", "09:00", "Vostura")],
                           templates=templates)
    assert "- Punkt eins" not in first.text        # something is written already
    fresh = cd.apply_lines("# Termine\n", [ev("Lehrgang", "18:00")],
                           templates=[cd.Template(match="Lehrgang",
                                                  lines=["    - Ablauf"])])
    assert "    - Ablauf" in fresh.text


def test_running_it_twice_changes_nothing_the_second_time() -> None:
    events = [ev("Daily Dev", "09:00", "Vostura"), ev("Hausarzt", "13:45")]
    once = cd.apply_lines(NOTE, events)
    twice = cd.apply_lines(once.text, events)
    assert twice.text == once.text
    assert twice.added == 0 and twice.updated == 0


def test_the_note_of_a_day_is_where_the_vault_puts_it() -> None:
    day = dt.date(2026, 9, 3)
    assert cd.daily_note_path(day, "05 Daily Notes", "YYYY/MM/YYYY-MM-DD") == \
        "05 Daily Notes/2026/09/2026-09-03.md"
    assert cd.daily_note_path(day, "", "YYYY-MM-DD") == "2026-09-03.md"


def test_a_template_loses_its_properties_block_and_gains_an_indent() -> None:
    lines = cd.template_lines("---\ntags: [x]\n---\n\n## Ablauf\n- eins\n")
    assert lines == ["## Ablauf", "- eins"] or lines == ["    ## Ablauf", "    - eins"]
    assert all(not l or l.startswith("    ") for l in lines)
