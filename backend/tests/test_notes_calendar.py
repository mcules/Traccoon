"""Turning a calendar feed into appointments.

An ICS feed is a set of rules, not a list, and every test here stands for a rule
that is easy to expand slightly wrongly — in a way that shows up as an
appointment at the wrong hour or a meeting that quietly stops a fortnight early,
rather than as an error.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

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


def days(text: str, first: str, last: str,
         zone: dt.tzinfo | None = None) -> list[tuple[str, str, str]]:
    found = cal.expand(text, "Test", dt.date.fromisoformat(first),
                       dt.date.fromisoformat(last), zone)
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
    reader's. A zone without a summer shift therefore moves against the reader's
    twice a year — and that is correct, not a fault to be corrected.

    The reader's zone is named here rather than taken from the process. It used
    to be taken from the process, and then this test said something about the
    machine it ran on: green in a container started on Berlin time, red in one
    on UTC, with nothing in the calendar having changed."""
    berlin = ZoneInfo("Europe/Berlin")
    text = ics(event(UID="8", SUMMARY="Fest",
                     **{"DTSTART;TZID=Etc/GMT-1": "20260601T180000"},
                     **{"DTEND;TZID=Etc/GMT-1": "20260601T190000"},
                     RRULE="FREQ=MONTHLY;COUNT=8"))
    found = {d: t for d, t, _ in days(text, "2026-06-01", "2027-02-01", zone=berlin)}
    assert found["2026-06-01"] == "19:00"        # summer in Berlin, so an hour later
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
    # Nothing new, but the line without an id gets one — that is how the notes
    # written before there were ids grow into them.
    assert out.added == 0 and out.updated == 1


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


def test_a_path_says_which_day_it_is_the_note_of_or_says_nothing() -> None:
    """The inverse of the path rule, and the gate for creating a note on a 404.

    What it refuses matters more than what it accepts: a name that looks like a
    date is not enough, or a mistyped link would leave files behind in places
    nobody asked for.
    """
    folder, fmt = "05 Daily Notes", "YYYY/MM/YYYY-MM-DD"
    assert cd.day_of_daily_path("05 Daily Notes/2026/09/2026-09-03.md", folder, fmt) \
        == dt.date(2026, 9, 3)
    # Right name, wrong month folder — the round trip does not come back the same.
    assert cd.day_of_daily_path("05 Daily Notes/2026/01/2026-09-03.md", folder, fmt) is None
    # Right name, somewhere else entirely.
    assert cd.day_of_daily_path("02 Projekte/2026-09-03.md", folder, fmt) is None
    # Not a date at all.
    assert cd.day_of_daily_path("05 Daily Notes/2026/09/Notizen.md", folder, fmt) is None
    # A vault without a folder.
    assert cd.day_of_daily_path("2026-09-03.md", "", "YYYY-MM-DD") == dt.date(2026, 9, 3)


def test_a_path_is_read_by_the_vault_s_own_format_not_by_a_guess() -> None:
    """Not everybody files their days the way this vault does.

    The pattern is built from the same `TOKENS` the forward direction uses, so
    whatever a vault was set to is understood — and what stands between the
    tokens is taken literally, or a format with dots in it would accept anything
    in their place.
    """
    assert cd.day_of_daily_path("Journal/03.09.2026.md", "Journal", "DD.MM.YYYY") \
        == dt.date(2026, 9, 3)
    assert cd.day_of_daily_path("Journal/2026/09/03.md", "Journal", "YYYY/MM/DD") \
        == dt.date(2026, 9, 3)
    assert cd.day_of_daily_path("26-09-03.md", "", "YY-MM-DD") == dt.date(2026, 9, 3)
    # A dot in the format is a dot.
    assert cd.day_of_daily_path("Journal/03x09x2026.md", "Journal", "DD.MM.YYYY") is None
    # A day that does not exist is not a day.
    assert cd.day_of_daily_path("Journal/2026-13-01.md", "Journal", "YYYY-MM-DD") is None
    # A format that names no single day — weekly notes — has no answer here.
    assert cd.day_of_daily_path("Wochen/2026-W40.md", "Wochen", "YYYY-[W]WW") is None


def test_a_template_loses_its_properties_block_and_gains_an_indent() -> None:
    lines = cd.template_lines("---\ntags: [x]\n---\n\n## Ablauf\n- eins\n")
    assert lines == ["## Ablauf", "- eins"] or lines == ["    ## Ablauf", "    - eins"]
    assert all(not l or l.startswith("    ") for l in lines)


# ------------------------------------------------------- writing to a calendar

from app.notes.calendar import caldav                # noqa: E402

PRINCIPAL = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:"><d:response>
  <d:href>/dav/</d:href>
  <d:propstat><d:prop><d:current-user-principal>
    <d:href>/dav/principals/me/</d:href>
  </d:current-user-principal></d:prop></d:propstat>
</d:response></d:multistatus>"""

HOME = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:response>
  <d:href>/dav/principals/me/</d:href>
  <d:propstat><d:prop><cal:calendar-home-set>
    <d:href>/dav/calendars/me/</d:href>
  </cal:calendar-home-set></d:prop></d:propstat>
</d:response></d:multistatus>"""

# One writable calendar, one shared read only, and the home set itself — which
# is not a calendar and must not be offered as one.
LISTING = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:response><d:href>/dav/calendars/me/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:displayname>Zuhause</d:displayname></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/calendars/me/privat/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <d:displayname>Privat</d:displayname>
      <d:current-user-privilege-set>
        <d:privilege><d:read/></d:privilege><d:privilege><d:write/></d:privilege>
      </d:current-user-privilege-set></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/calendars/me/geteilt/</d:href>
    <d:propstat><d:prop>
      <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
      <d:displayname>Geteilt</d:displayname>
      <d:current-user-privilege-set>
        <d:privilege><d:read/></d:privilege>
      </d:current-user-privilege-set></d:prop></d:propstat></d:response>
</d:multistatus>"""


class Answer:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code = status
        self.text = text


def _server(monkeypatch, *, root_speaks: bool = True, seen: list | None = None):
    """A CalDAV server that answers the three questions, and remembers what it
    was asked."""
    async def dav(account, method, url, body=None, headers=None):
        if seen is not None:
            seen.append((method, url))
        if method == "PROPFIND" and url.endswith("/.well-known/caldav"):
            return Answer(207, PRINCIPAL)
        if method == "PROPFIND" and "principals" in url:
            return Answer(207, HOME)
        if method == "PROPFIND" and "calendars" in url:
            return Answer(207, LISTING)
        if method == "PROPFIND":
            return Answer(207, PRINCIPAL) if root_speaks else Answer(405)
        if method == "HEAD":
            return Answer(404)
        return Answer(201)
    monkeypatch.setattr(caldav, "_dav", dav)


ACCOUNT = caldav.Account(url="https://server.example", user="me", password="x")


@pytest.mark.asyncio
async def test_an_account_without_a_password_is_simply_not_configured() -> None:
    assert await caldav.calendars(caldav.Account(url="https://x", user="me")) == []


@pytest.mark.asyncio
async def test_the_well_known_address_is_used_when_the_root_says_no(monkeypatch) -> None:
    """Somebody types the address of their server, not of its calendar endpoint,
    and a web root answers "method not allowed". Building one vendor's folder
    layout into the code instead is what the side this replaces did."""
    seen: list = []
    _server(monkeypatch, root_speaks=False, seen=seen)
    home = await caldav.calendar_home(ACCOUNT)
    assert home.endswith("/dav/calendars/me/")
    assert any(url.endswith("/.well-known/caldav") for _, url in seen)


@pytest.mark.asyncio
async def test_a_root_that_answers_is_left_alone(monkeypatch) -> None:
    seen: list = []
    _server(monkeypatch, root_speaks=True, seen=seen)
    await caldav.calendar_home(ACCOUNT)
    assert not any(url.endswith("/.well-known/caldav") for _, url in seen)


@pytest.mark.asyncio
async def test_the_home_set_is_not_offered_as_a_calendar(monkeypatch) -> None:
    _server(monkeypatch)
    found = await caldav.calendars(ACCOUNT)
    assert [c.name for c in found] == ["Privat", "Geteilt"]


@pytest.mark.asyncio
async def test_a_calendar_shared_read_only_says_so(monkeypatch) -> None:
    _server(monkeypatch)
    found = {c.name: c.read_only for c in await caldav.calendars(ACCOUNT)}
    assert found == {"Privat": False, "Geteilt": True}


@pytest.mark.asyncio
async def test_writing_into_a_read_only_calendar_is_refused(monkeypatch) -> None:
    _server(monkeypatch)
    with pytest.raises(PermissionError):
        await caldav.save_event(ACCOUNT, "geteilt", timezone="Europe/Berlin",
                                title="X", start="2026-09-02T10:00", end="2026-09-02T11:00")
    with pytest.raises(LookupError):
        await caldav.save_event(ACCOUNT, "gibtsnicht", timezone="Europe/Berlin",
                                title="X", start="2026-09-02T10:00", end="2026-09-02T11:00")


@pytest.mark.asyncio
async def test_a_new_appointment_gets_an_identity_of_its_own(monkeypatch) -> None:
    seen: list = []
    _server(monkeypatch, seen=seen)
    out = await caldav.save_event(ACCOUNT, "privat", timezone="Europe/Berlin",
                                  title="Zahnarzt", start="2026-09-02T10:00",
                                  end="2026-09-02T11:00")
    assert out["created"] is True and out["uid"].endswith("@notes")
    assert out["url"].endswith(".ics")


@pytest.mark.asyncio
async def test_deleting_something_that_is_already_gone_is_not_a_failure(monkeypatch) -> None:
    """It is the state that was asked for."""
    async def dav(account, method, url, body=None, headers=None):
        if method == "PROPFIND" and "calendars" in url:
            return Answer(207, LISTING)
        if method == "PROPFIND" and "principals" in url:
            return Answer(207, HOME)
        if method == "PROPFIND":
            return Answer(207, PRINCIPAL)
        return Answer(404)
    monkeypatch.setattr(caldav, "_dav", dav)
    await caldav.delete_event(ACCOUNT, "privat", "weg@notes")


def test_an_appointment_is_written_on_the_local_clock() -> None:
    """With the zone named, so the time means what it says wherever it is read."""
    ics_text = caldav.build_ics(uid="u1", title="Zahnarzt", start="2026-09-02T10:00",
                                end="2026-09-02T11:00", timezone="Europe/Berlin")
    assert "DTSTART;TZID=Europe/Berlin:20260902T100000" in ics_text
    assert "SUMMARY:Zahnarzt" in ics_text


def test_a_whole_day_is_written_as_a_day() -> None:
    ics_text = caldav.build_ics(uid="u2", title="Urlaub", start="2026-09-02",
                                end="2026-09-03", timezone="Europe/Berlin", all_day=True)
    assert "DTSTART;VALUE=DATE:20260902" in ics_text
    assert "TZID" not in ics_text


def test_a_comma_in_a_title_does_not_end_the_field() -> None:
    """Unescaped it would make the rest of the title a second value, and the
    appointment would arrive with half a name."""
    ics_text = caldav.build_ics(uid="u3", title="Essen, dann Kino", start="2026-09-02T18:00",
                                end="2026-09-02T21:00", timezone="Europe/Berlin")
    assert "SUMMARY:Essen\\, dann Kino" in ics_text


# ------------------------------------------------- folding the old long form

CALENDARS = {"Vostura", "B37", "Privat"}

LEGACY = """# Termine

- 📅 *09:00* [[Firma/Termine|Vostura]] Daily Dev <!-- uid:abc@google.com@20260902 -->
    - Ort <!-- loc -->
      Microsoft Teams-Besprechung
    - Beschreibung <!-- desc -->
      Tägliche Abstimmung:
      Was wurde gemacht?
    - was ich mir dazu notiert habe
- *ganztägig* [[Verein/Termine|B37]] Fieldday (Tag 1/3)

# Notizen
"""


def test_the_old_long_form_becomes_the_short_one() -> None:
    out = cd.tidy_legacy_lines(LEGACY, CALENDARS)
    lines = [l for l in out.text.split("\n") if l.startswith("- ")]
    assert lines == ["- 09:00 Daily Dev · Vostura", "- Fieldday (Tag 1/3) · B37"]


def test_what_the_reader_wrote_under_an_appointment_survives() -> None:
    """The location and description blocks go, the reader's own bullet stays —
    and it stays because the drop ends at the depth of the bullet it started on.
    A rule of "four spaces or more", which is what the side this replaces asks
    for, is exactly the depth a reader writes at."""
    out = cd.tidy_legacy_lines(LEGACY, CALENDARS)
    assert "    - was ich mir dazu notiert habe" in out.text.split("\n")
    assert "Microsoft Teams-Besprechung" not in out.text
    assert "<!--" not in out.text


def test_an_all_day_appointment_is_shortened_too() -> None:
    """The side this replaces reads only a clock as the time label, so an
    all-day line kept its whole old shape — and then stood beside the short line
    for the same appointment, which is why this vault has them twice."""
    out = cd.tidy_legacy_lines(
        "# Termine\n\n- *ganztägig* [[Verein/Termine|B37]] Fieldday\n", CALENDARS)
    assert "- Fieldday · B37" in out.text.split("\n")


def test_a_link_that_is_not_a_calendar_is_left_alone() -> None:
    """One appointment in this vault was pointed at the note about the event
    instead of at a calendar. Read as a calendar, the note's name becomes the
    calendar and the link is thrown away."""
    line = "- 📅 *13:00* [[06 Archiv/Grillfest 2026|Grillfest]] — mit den Nachbarn"
    out = cd.tidy_legacy_lines(f"# Termine\n\n{line}\n", CALENDARS)
    assert line in out.text.split("\n")
    assert out.changed == 0


def test_the_same_appointment_twice_becomes_once() -> None:
    """Where both writers met: the long block and the short line say the same
    thing, and the copy with something written under it is the one to keep."""
    text = ("# Termine\n\n"
            "- 📅 *09:00* [[Firma/Termine|Vostura]] Daily Dev <!-- uid:x -->\n"
            "- 09:00 Daily Dev · Vostura\n"
            "\t- meine Notiz\n")
    out = cd.tidy_legacy_lines(text, CALENDARS)
    lines = out.text.split("\n")
    assert lines.count("- 09:00 Daily Dev · Vostura") == 1
    assert "\t- meine Notiz" in lines


def test_folding_twice_changes_nothing_more() -> None:
    once = cd.tidy_legacy_lines(LEGACY, CALENDARS)
    assert cd.tidy_legacy_lines(once.text, CALENDARS).text == once.text


def test_a_line_inside_a_code_block_is_not_an_appointment() -> None:
    """The daily note of this vault carries `dataviewjs` blocks, and the
    JavaScript in them has lines that read like markdown."""
    text = ("# Termine\n\n"
            "```dataviewjs\n"
            "- 📅 *09:00* [[Firma/Termine|Vostura]] Beispiel <!-- uid:x -->\n"
            "```\n")
    assert cd.tidy_legacy_lines(text, CALENDARS).text == text


def test_a_byte_order_mark_does_not_turn_the_note_into_code() -> None:
    """Six notes in this vault open with one, and Python's `\\s` does not count
    it as space. Missing that first fence puts the count of open and closed
    fences off by one, and from there the whole note reads as code."""
    text = ("﻿```dataviewjs\nconst x = 1;\n```\n\n"
            "# Termine\n\n"
            "- 📅 *09:00* [[Firma/Termine|Vostura]] Daily Dev <!-- uid:x -->\n")
    out = cd.tidy_legacy_lines(text, CALENDARS)
    assert "- 09:00 Daily Dev · Vostura" in out.text.split("\n")


def test_a_struck_through_line_outside_the_section_stays() -> None:
    """A finished to-do is written struck through, and so was a cancelled
    appointment. Twenty-eight of the reader's own to-dos in this vault have that
    shape, which is why the fold does not leave the appointment section."""
    text = "# Aufgaben\n\n\t- ~~Logo tauschen~~\n\n# Termine\n\n- 09:00 X · Vostura\n"
    assert cd.tidy_legacy_lines(text, CALENDARS).text == text


def test_the_indent_of_a_kept_bullet_may_be_a_tab() -> None:
    assert cd.indent_width("\t- x") == 4
    assert cd.indent_width("    - x") == 4
    assert cd.indent_width("  \t- x") == 4
    assert cd.indent_width("      x") == 6


def test_a_past_day_keeps_what_was_struck_through() -> None:
    """A feed carries the state of an appointment now, not the state it was in
    back then. A meeting cancelled in July is simply gone from the series today,
    and syncing that day again would quietly take the strike off and say it had
    taken place."""
    note = "# Termine\n\n- 09:00 ~~AI Exchange~~ · Vostura\n"
    live = [ev("AI Exchange", "09:00", "Vostura")]          # no longer cancelled
    assert cd.apply_lines(note, live, keep_strikes=True).text == note
    assert "~~" not in cd.apply_lines(note, live).text


def test_a_line_carries_the_end_time_when_there_is_one() -> None:
    """When something is is a span, not a moment. A list that only says when
    things start makes the reader work it out from the next entry."""
    with_end = ev("Daily Dev", "09:00", "Vostura")
    with_end.endTime = "09:30"
    assert cd.line_for(with_end).startswith("- 09:00\u201309:30 Daily Dev · Vostura ^")

    # An end that equals the start says nothing.
    same = ev("Kurz", "09:00", "Vostura")
    same.endTime = "09:00"
    assert cd.line_for(same).startswith("- 09:00 Kurz · Vostura ^")

    # All day has no time at all, and no dash where one would be.
    whole = ev("Urlaub", "", "Privat")
    whole.allDay = True
    whole.endTime = "23:59"
    assert cd.line_for(whole).startswith("- Urlaub · Privat ^")


def test_a_line_written_with_a_time_range_is_recognised_not_repeated() -> None:
    """Found in the vault on 2026-09-09, a single line among 432.

    It is the shape of the failure that matters, not the count: a line this
    house does not recognise is not found again, so the appointment is appended
    a second time and the day shows it twice — with whatever was written under
    the first one now hanging under the wrong copy.
    """
    event = ev("Community Session", "14:00", "Vostura")
    note = "# Termine\n\n- 14:00\u201314:50 Community Session · Vostura\n"
    out = cd.apply_lines(note, [event])
    lines = [l for l in out.text.split("\n") if l.startswith("- ")]
    assert len(lines) == 1, lines
    assert lines[0] == f"- 14:00 Community Session · Vostura ^{cd.block_id(event)}"
    assert out.added == 0
    # The plain hyphen just as much as the en dash.
    assert cd.apply_lines("# Termine\n\n- 14:00-14:50 Community Session · Vostura\n",
                          [event]).added == 0


def test_a_renamed_appointment_is_recognised_instead_of_written_twice() -> None:
    """The case the id exists for.

    Before it, a line was found again by its title and calendar. Rename the
    appointment in the calendar and the old line no longer looked like it, so the
    new one was appended and the day showed the same meeting twice — with the
    reader's notes hanging under the old one.
    """
    event = ev("Daily Dev", "09:00", "Vostura")
    first = cd.apply_lines("# Termine\n", [event])
    assert first.added == 1

    renamed = ev("Daily Dev (neuer Name)", "09:00", "Vostura")
    renamed.uid = event.uid                      # same appointment, other title
    out = cd.apply_lines(first.text, [renamed])
    lines = [l for l in out.text.split("\n") if l.startswith("- ")]
    assert len(lines) == 1, lines
    assert "neuer Name" in lines[0]
    assert out.added == 0 and out.updated == 1


def test_an_id_belongs_to_the_appointment_and_not_to_the_day_it_falls_on() -> None:
    """It has to survive a move, so it must not be built from the start time.

    `Event.id` is `uid@start` and changes with it; that is why the id comes from
    the UID alone.
    """
    monday = ev("Daily Dev", "09:00", "Vostura")
    moved = ev("Daily Dev", "14:30", "Vostura")
    moved.uid = monday.uid
    assert cd.block_id(monday) == cd.block_id(moved)
    assert cd.block_id(ev("Anderes", "09:00", "Vostura")) != cd.block_id(monday)
    # And it is a name Obsidian accepts: letters, digits and the hyphen only.
    assert re.fullmatch(r"[a-zA-Z0-9-]+", cd.block_id(monday))


def test_a_line_gives_its_id_back_and_says_nothing_when_it_has_none() -> None:
    assert cd.split_block_id("- 09:00 X · Y ^ev-1234abcd") == ("- 09:00 X · Y", "ev-1234abcd")
    assert cd.split_block_id("- 09:00 X · Y") == ("- 09:00 X · Y", "")
    # A caret in the middle of a line is not an id.
    assert cd.split_block_id("- 2^3 ist acht") == ("- 2^3 ist acht", "")


def test_a_cancelled_appointment_says_so_under_its_line() -> None:
    """Struck through says something did not happen; the note says why."""
    event = ev("Daily Dev", "09:00", "Vostura")
    event.cancelled = True
    out = cd.apply_lines("# Termine\n", [event])
    assert out.text.split("\n")[1:3] == [
        f"- 09:00 ~~Daily Dev~~ · Vostura ^{cd.block_id(event)}",
        cd.CANCELLED_NOTE,
    ]
    # Twice does not say it twice.
    again = cd.apply_lines(out.text, [event])
    assert again.text == out.text


def test_a_withdrawn_cancellation_takes_its_note_with_it() -> None:
    event = ev("Daily Dev", "09:00", "Vostura")
    event.cancelled = True
    cancelled = cd.apply_lines("# Termine\n", [event]).text
    back = ev("Daily Dev", "09:00", "Vostura")
    back.uid = event.uid
    out = cd.apply_lines(cancelled, [back])
    assert cd.CANCELLED_NOTE not in out.text
    assert "~~" not in out.text


def test_the_day_an_appointment_left_says_where_it_went() -> None:
    """The half a sync cannot work out: the appointment is simply not among that
    day's events any more."""
    event = ev("Daily Dev", "09:00", "Vostura")
    note = cd.apply_lines("# Termine\n", [event]).text + "\t- meine Notiz dazu\n"
    out = cd.mark_moved(note, cd.block_id(event),
                        "05 Daily Notes/2026/09/2026-09-15", "15.09.2026")
    lines = out.text.split("\n")
    at = next(i for i, l in enumerate(lines) if l.startswith("- "))
    assert lines[at] == f"- 09:00 ~~Daily Dev~~ · Vostura ^{cd.block_id(event)}"
    assert lines[at + 1] == \
        "    \u21aa verschoben auf [[05 Daily Notes/2026/09/2026-09-15|15.09.2026]]"
    # What the reader wrote under it is still there, and still under it.
    assert lines[at + 2] == "\t- meine Notiz dazu"
    assert out.updated == 1
    # Saying it again changes nothing.
    assert cd.mark_moved(out.text, cd.block_id(event),
                         "05 Daily Notes/2026/09/2026-09-15", "15.09.2026").text == out.text


def test_a_move_note_goes_to_the_line_with_that_id_and_nowhere_else() -> None:
    one, two = ev("Daily Dev", "09:00", "Vostura"), ev("Weekly", "10:00", "Vostura")
    note = cd.apply_lines("# Termine\n", [one, two]).text
    out = cd.mark_moved(note, cd.block_id(two), "05 Daily Notes/2026/09/2026-09-16", "16.09.")
    assert f"- 09:00 Daily Dev · Vostura ^{cd.block_id(one)}" in out.text
    assert f"- 10:00 ~~Weekly~~ · Vostura ^{cd.block_id(two)}" in out.text
    assert out.text.count("verschoben auf") == 1
    # An id nobody wrote leaves the note alone.
    assert cd.mark_moved(note, "ev-doesnotexist", "x", "y").text == note


def test_a_coming_day_may_take_the_strike_off_again() -> None:
    """A withdrawn cancellation is news worth carrying — for a day still ahead."""
    note = "# Termine\n\n- 09:00 ~~AI Exchange~~ · Vostura\n"
    event = ev("AI Exchange", "09:00", "Vostura")
    out = cd.apply_lines(note, [event])
    assert f"- 09:00 AI Exchange · Vostura ^{cd.block_id(event)}" in out.text.split("\n")
    assert out.updated == 1


def test_a_multi_day_appointment_is_not_written_a_second_time() -> None:
    """The previous writer put "(Tag 1/3)" behind the title of an appointment
    running over several days; the feed's own title has no such thing. Read
    literally, the line describes a different appointment — which is how nearly
    every multi-day event in this vault came to stand there twice."""
    note = "# Termine\n\n- Fieldday (Tag 1/3) · B37\n"
    out = cd.apply_lines(note, [ev("Fieldday", "", "B37")])
    assert out.text == note                      # recognised, and left as it is
    assert out.added == 0


def test_the_counter_survives_a_sync() -> None:
    """Which day of the appointment this is says more than the feed does."""
    note = "# Termine\n\n- 09:00 Kurs (Tag 2/4) · B37\n"
    assert cd.apply_lines(note, [ev("Kurs", "09:00", "B37")]).text == note


def test_of_two_lines_for_one_appointment_the_fuller_one_stays() -> None:
    text = ("# Termine\n\n"
            "- Fieldday (Tag 1/3) · B37\n"
            "- Fieldday · B37\n")
    out = cd.tidy_legacy_lines(text, CALENDARS).text.split("\n")
    assert "- Fieldday (Tag 1/3) · B37" in out
    assert "- Fieldday · B37" not in out


# --------------------------------------------------- what became of an appointment

from app.notes.calendar.sync import verdict


REACH = ("2026-01-01", "2026-12-31")


def test_a_series_does_not_count_as_moved_just_because_it_meets_again() -> None:
    """The trap a block id sets, and the reason this rule is its own function.

    An id belongs to the appointment, not to the occurrence: every week of a
    weekly meeting carries the same one. Asking "is this id on another day?"
    answers yes for every series there is — the first version of the walk read
    302 moves out of four appointments that had not moved at all.
    """
    weekly = {"ev-weekly": {"2026-09-07", "2026-09-14", "2026-09-21"}}
    assert verdict(weekly, "ev-weekly", "2026-09-14", reach=REACH) == ("stay", None)
    # But an occurrence that fell away does move — to the next one there is.
    assert verdict(weekly, "ev-weekly", "2026-09-10", reach=REACH) == ("moved", "2026-09-14")


def test_an_appointment_in_no_calendar_any_more_has_fallen_away() -> None:
    assert verdict({}, "ev-weg", "2026-09-09", reach=REACH) == ("gone", None)


def test_a_day_outside_what_was_fetched_gets_no_opinion() -> None:
    """No events there means "not asked about", not "nothing happens"."""
    days = {"ev-x": {"2026-09-15"}}
    assert verdict(days, "ev-x", "2025-01-01", reach=REACH) == ("unknown", None)
    assert verdict(days, "ev-x", "2026-09-09", reach=REACH) == ("moved", "2026-09-15")


def test_a_move_backwards_still_finds_its_day() -> None:
    """Nothing says an appointment only ever moves forward."""
    days = {"ev-x": {"2026-09-02"}}
    assert verdict(days, "ev-x", "2026-09-09", reach=REACH) == ("moved", "2026-09-02")


def test_a_fallen_away_appointment_says_so_and_keeps_what_was_written_under_it() -> None:
    event = ev("Daily Dev", "09:00", "Vostura")
    note = cd.apply_lines("# Termine\n", [event]).text + "\t- meine Notiz\n"
    out = cd.mark_gone(note, cd.block_id(event))
    lines = out.text.split("\n")
    at = next(i for i, l in enumerate(lines) if l.startswith("- "))
    assert lines[at] == f"- 09:00 ~~Daily Dev~~ · Vostura ^{cd.block_id(event)}"
    assert lines[at + 1] == cd.GONE_NOTE
    assert lines[at + 2] == "\t- meine Notiz"
    # Saying it twice says it once.
    assert cd.mark_gone(out.text, cd.block_id(event)).text == out.text
