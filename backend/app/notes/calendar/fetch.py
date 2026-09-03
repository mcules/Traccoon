"""Fetching calendars and turning them into plain appointments.

A feed is ICS, and ICS is not a list of appointments but a set of rules: a
weekly meeting is one entry plus a repetition rule, minus the days it was
cancelled, plus the ones that were moved. Expanding that is the whole job here;
what comes out is what a calendar view and a daily note actually want.

The expansion is left to a library rather than written by hand, and the reason
is one specific mistake it does not make. A repetition rule works on dates, and
an occurrence has to be read on the clock the *series* was written on — which is
the event's own zone, not the calendar's and not the reader's. This vault has a
monthly appointment in a zone without daylight saving; read in the local one it
is at 18:00 in winter and 19:00 in summer, and a standing meeting that drifts by
exactly the daylight-saving offset twice a year is the kind of wrongness nobody
looks for in a calendar.

One unreachable feed must not empty the calendar: what the others gave is kept
and the one that failed is named.
"""
from __future__ import annotations

import base64
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import icalendar
import recurring_ical_events

log = logging.getLogger("notes.calendar")

TIMEOUT = 30.0
NO_TITLE = "(ohne Titel)"


@dataclass
class Source:
    """One calendar to read from. Comes out of the person's settings."""
    name: str
    url: str
    link_target: str = ""
    auth_user: str = ""
    auth_password: str = ""


@dataclass
class Event:
    """One occurrence, as flat as a view needs it."""
    id: str
    uid: str
    calendar: str
    title: str
    date: str                      # local `YYYY-MM-DD`
    time: str                      # local `HH:MM`, empty when it lasts all day
    endTime: str
    allDay: bool
    start: str                     # ISO, with its offset
    end: str
    cancelled: bool = False
    location: str | None = None
    description: str | None = None

    def as_json(self) -> dict:
        out = {
            "id": self.id, "uid": self.uid, "calendar": self.calendar,
            "title": self.title, "date": self.date, "time": self.time,
            "endTime": self.endTime, "allDay": self.allDay,
            "start": self.start, "end": self.end, "cancelled": self.cancelled,
        }
        # Absent rather than null, the way the side being replaced sends it.
        if self.location:
            out["location"] = self.location
        if self.description:
            out["description"] = self.description
        return out


@dataclass
class Snapshot:
    events: list[Event] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    fetched_at: str = ""


async def read_feed(source: Source) -> str:
    headers: dict[str, str] = {}
    if source.auth_user and source.auth_password:
        raw = f"{source.auth_user}:{source.auth_password}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        answer = await client.get(source.url, headers=headers)
    if answer.status_code >= 400:
        raise RuntimeError(f"{source.name}: HTTP {answer.status_code}")
    return answer.text


def _local(value: dt.datetime | dt.date, zone: dt.tzinfo | None) -> dt.datetime:
    """An occurrence on the reader's wall clock.

    Whose clock that is has to be said, not assumed: the reader is a person with
    a zone of their own (`users.timezone`), and the server's zone is only the
    one the container happens to have been started with. Reading it from the
    process meant the same appointment showed a different hour depending on
    where the container ran, and a test of it depended on the host clock.

    A time with a zone is converted; one without is already on that clock. A
    date is a day, and a day starts at midnight wherever it is read.
    """
    if isinstance(value, dt.datetime):
        return value.astimezone(zone) if value.tzinfo else value
    return dt.datetime(value.year, value.month, value.day)


def _is_all_day(value: Any) -> bool:
    return isinstance(value, dt.date) and not isinstance(value, dt.datetime)


def expand(ics_text: str, calendar: str, first: dt.date, last: dt.date,
           zone: dt.tzinfo | None = None) -> list[Event]:
    """Every occurrence between two days, from one feed.

    `zone` is the clock the times come out on; None means the one the process
    runs in, which is right for a script and wrong for a request.
    """
    parsed = icalendar.Calendar.from_ical(ics_text)
    out: list[Event] = []
    for item in recurring_ical_events.of(parsed).between(first, last):
        start_raw = item.get("DTSTART").dt
        end_field = item.get("DTEND")
        end_raw = end_field.dt if end_field is not None else start_raw
        all_day = _is_all_day(start_raw)

        start = _local(start_raw, zone)
        end = _local(end_raw, zone)
        # An all-day event's end is exclusive in ICS — a one-day event ends on
        # the next day — so it is pulled back before the day is taken from it.
        shown_end = end - dt.timedelta(seconds=1) if all_day else end

        uid = str(item.get("UID") or "")
        title = str(item.get("SUMMARY") or NO_TITLE).strip() or NO_TITLE
        location = str(item.get("LOCATION") or "").strip() or None
        description = str(item.get("DESCRIPTION") or "").strip() or None
        out.append(Event(
            id=f"{uid}@{start.isoformat()}",
            uid=uid,
            calendar=calendar,
            title=title,
            date=start.strftime("%Y-%m-%d"),
            time="" if all_day else start.strftime("%H:%M"),
            endTime="" if all_day else shown_end.strftime("%H:%M"),
            allDay=all_day,
            start=start.isoformat(),
            end=end.isoformat(),
            cancelled=str(item.get("STATUS") or "") == "CANCELLED",
            location=location,
            description=description,
        ))
    return out


async def refresh(sources: list[Source], *, days_back: int = 120,
                  days_ahead: int = 400, zone: dt.tzinfo | None = None) -> Snapshot:
    """Read every calendar for a window around today.

    A year ahead and four months back: the view can be paged through without
    running into an empty month, and the feeds are small enough that expanding
    that range costs nothing worth measuring.
    """
    today = dt.datetime.now(zone).date()
    first = today - dt.timedelta(days=days_back)
    last = today + dt.timedelta(days=days_ahead)
    events: list[Event] = []
    errors: list[dict] = []

    for source in sources:
        try:
            text = await read_feed(source)
            events.extend(expand(text, source.name, first, last, zone))
        except Exception as err:                  # noqa: BLE001 - see the docstring
            log.warning("notes: calendar %s could not be read: %s", source.name, err)
            errors.append({"calendar": source.name, "message": str(err)})

    events.sort(key=lambda e: (e.start, e.title))
    return Snapshot(events=events, errors=errors,
                    fetched_at=dt.datetime.now(zone).astimezone(zone).isoformat())


def on_day(snapshot: Snapshot, day: str) -> list[Event]:
    """The appointments of one day, in the order they happen — all day first."""
    return sorted((e for e in snapshot.events if e.date == day),
                  key=lambda e: (not e.allDay, e.time))
