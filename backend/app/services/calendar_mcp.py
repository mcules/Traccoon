"""The calendars as tools, for whoever works with them but is not a person.

What this replaces reached one vendor's server directly, with a password of its
own in a container's configuration, and it knew nothing about which calendar
anybody meant to be written to — every calendar the account could see was one it
could write. So a tool that was given the ability to add an appointment had the
ability to add it anywhere.

Here the same call goes through what somebody set up in their account: their
logins, their calendars, and per calendar who may write into it. The token is a
normal Traccoon token with the calendar scope, so it is revoked the way every
other one is, and it opens nothing else in the house.

Reading covers every calendar, including the subscriptions that can never be
written. Writing is refused with a sentence saying which of the four conditions
failed — that it sits on a login, that it names a collection, that the server
allows it, and that this person allowed the assistant specifically.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.timezones import zone_of
from ..models.user import User
from ..notes.calendar import access as cal_access
from ..notes.calendar import caldav as cal_dav
from ..notes.calendar import fetch as cal_fetch
from ..notes.calendar import store as cal_store

log = logging.getLogger("calendar.mcp")

# The window the fetched set covers, and the reason a date outside it comes back
# as a refusal rather than as an empty list: nothing found and not looked for
# read the same way to a caller, and only one of them is true.
DAYS_BACK = 120
DAYS_AHEAD = 400

MAX_EVENTS = 500

INSTRUCTIONS = """\
These are the calendars of one person, as they set them up.

Reading covers all of them. Writing works only where they allowed it for the
assistant, per calendar — `list_calendars` says so for each one, and a write to
any other calendar is refused. Use the numeric `calendar` id from that list, not
a name: two servers can hold a calendar of the same name.

Times are that person's local time. A whole-day appointment has a date and no
time; anything else has both. When changing an appointment, pass its `uid`
unchanged and send the whole appointment, not only the field that differs.
"""

STRING = {"type": "string"}
DATE = {"type": "string", "description": "YYYY-MM-DD"}


def _tool(name: str, description: str, properties: dict,
          required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": required or []}}


TOOLS: list[dict] = [
    _tool("list_calendars",
          "The calendars of this person: what each is called, which login it "
          "sits on, and whether you may write into it.",
          {}),
    _tool("list_events",
          "The appointments between two dates, across every calendar unless one "
          f"is named. At most {DAYS_BACK} days back and {DAYS_AHEAD} days ahead. "
          "Descriptions come shortened; `get_event` has the whole text of one "
          "appointment, and `description_chars` says when there is more of it.",
          {"from": DATE, "to": DATE,
           "calendar": {"type": "integer", "description": "id from list_calendars"},
           "limit": {"type": "integer", "description": f"at most {MAX_EVENTS}"}},
          ["from", "to"]),
    _tool("search_events",
          "Appointments whose title, place or description contain the text. "
          "Searches the fetched window, not the whole history of a calendar.",
          {"query": STRING, "from": DATE, "to": DATE,
           "limit": {"type": "integer"}},
          ["query"]),
    _tool("get_event",
          "One appointment by its `uid`, with every occurrence of it that falls "
          "in the window.",
          {"uid": STRING}, ["uid"]),
    _tool("create_event",
          "Put a new appointment into a calendar you may write to. Answers with "
          "the `uid` it got, which is what you need to change or remove it.",
          {"calendar": {"type": "integer", "description": "id from list_calendars"},
           "title": STRING,
           "start": {"type": "string",
                     "description": "YYYY-MM-DD for a whole day, else YYYY-MM-DDTHH:MM"},
           "end": {"type": "string", "description": "same shape as start"},
           "all_day": {"type": "boolean"},
           "location": STRING, "description": STRING},
          ["calendar", "title", "start", "end"]),
    _tool("update_event",
          "Change an appointment. Send it whole — what you leave out is left "
          "out of the appointment, not kept from before.",
          {"calendar": {"type": "integer"}, "uid": STRING, "title": STRING,
           "start": STRING, "end": STRING, "all_day": {"type": "boolean"},
           "location": STRING, "description": STRING},
          ["calendar", "uid", "title", "start", "end"]),
    _tool("delete_event",
          "Take an appointment out of a calendar you may write to.",
          {"calendar": {"type": "integer"}, "uid": STRING},
          ["calendar", "uid"]),
    _tool("sync_day",
          "Write one day's appointments into that day's daily note, and make the "
          "note first if it is not there yet. Appointments already in it are "
          "recognised and left alone, one that was called off is struck through "
          "and says so, and one that has moved away has its old line marked with "
          "where it went. Running it twice changes nothing the second time.",
          {"date": {"type": "string", "description": "YYYY-MM-DD"},
           "dry_run": {"type": "boolean",
                       "description": "work out what would change and write nothing"}},
          ["date"]),
    _tool("sync_window",
          "The same for a run of days, starting at `date` (today when left out). "
          "Also marks the notes an appointment has moved OUT of, including days "
          "far outside the window that nobody is otherwise reading — a single day "
          "cannot see that. This is the one a nightly job wants.",
          {"date": {"type": "string", "description": "YYYY-MM-DD, default today"},
           "days": {"type": "integer", "description": "1 to 120, default 7"},
           "dry_run": {"type": "boolean"}},
          []),
    _tool("daily_note",
          "The daily note of a day, made from the vault's template if it is not "
          "there yet. Answers with its path and whether it had to be made.",
          {"date": {"type": "string", "description": "YYYY-MM-DD"}},
          ["date"]),
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def toollist() -> list[dict]:
    return TOOLS


def _day(value: str, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{what} is not a date in the form YYYY-MM-DD: {value!r}") from None


def _in_reach(first: dt.date, last: dt.date, today: dt.date) -> None:
    """Say when a question is outside what was fetched.

    An empty answer would be indistinguishable from "there is nothing then", and
    a caller acting on that would say the day is free when nobody looked.
    """
    if first < today - dt.timedelta(days=DAYS_BACK) or last > today + dt.timedelta(days=DAYS_AHEAD):
        raise ValueError(
            f"only {DAYS_BACK} days back and {DAYS_AHEAD} days ahead are fetched "
            f"({today - dt.timedelta(days=DAYS_BACK)} to {today + dt.timedelta(days=DAYS_AHEAD)})")


async def _snapshot(db: AsyncSession, user: User) -> tuple[list[cal_fetch.Event], list[dict]]:
    sources = await cal_access.sources_of(db, user)
    snap = await cal_store.ensure(user.id, sources, zone=zone_of(user))
    return snap.events, snap.errors


async def execute(db: AsyncSession, user: User, name: str, args: dict) -> Any:
    """One tool call. Everything it raises reaches the caller as a sentence."""
    if name not in TOOL_NAMES:
        raise LookupError(f"no tool called {name!r}")
    zone = zone_of(user)
    today = dt.datetime.now(zone).date()

    if name == "sync_day":
        # The route holds the whole reconciliation — creating the note, folding
        # the old form, recognising what moved, keeping the index. Repeating any
        # of that here would be a second version of it that drifts.
        from ..api.notes_native import SyncDayIn, calendar_sync_day
        return await calendar_sync_day(
            SyncDayIn(date=str(args.get("date") or ""),
                      dryRun=bool(args.get("dry_run"))), user=user, db=db)

    if name == "sync_window":
        from ..api.notes_native import SyncWindowIn, calendar_sync_window
        return await calendar_sync_window(
            SyncWindowIn(date=str(args.get("date") or ""),
                         days=int(args.get("days") or 7),
                         dryRun=bool(args.get("dry_run"))), user=user, db=db)

    if name == "daily_note":
        from ..api.notes_native import DailyIn, daily_note
        return await daily_note(DailyIn(date=str(args.get("date") or "")), user=user)

    if name == "list_calendars":
        servers = {s.id: s for s in await cal_access.servers_of(db, user)}
        rows = await cal_access.calendars_of(db, user)
        out = []
        for row in rows:
            server = servers.get(row.server_id) if row.server_id else None
            out.append({
                "id": row.id,
                "name": row.name,
                "login": (server.label or server.url) if server else None,
                "enabled": row.enabled,
                # The one thing a caller has to know before it tries.
                "you_may_write": cal_access.may_write(row, by_agent=True),
                "why_not": _why_not(row, server),
            })
        return {"calendars": out, "today": today.isoformat(),
                "timezone": str(zone)}

    if name in ("list_events", "search_events"):
        first = _day(str(args.get("from") or ""), "from") if args.get("from") else today
        last = (_day(str(args.get("to") or ""), "to") if args.get("to")
                else today + dt.timedelta(days=30))
        if last < first:
            raise ValueError("`to` is before `from`")
        _in_reach(first, last, today)
        events, errors = await _snapshot(db, user)
        want = str(args.get("calendar") or "")
        named = ""
        if name == "list_events" and args.get("calendar"):
            row = await cal_access.own_calendar(db, user, int(args["calendar"]))
            named = row.name
        hits = [e for e in events
                if first.isoformat() <= e.date <= last.isoformat()
                and (not named or e.calendar == named)]
        if name == "search_events":
            needle = str(args.get("query") or "").casefold()
            if not needle:
                raise ValueError("search_events needs a query")
            hits = [e for e in hits if needle in " ".join(
                filter(None, (e.title, e.location, e.description))).casefold()]
        limit = min(int(args.get("limit") or MAX_EVENTS), MAX_EVENTS)
        # `brief`: a listing carries the beginning of a description, not all of it. Whoever
        # needs the whole text of ONE appointment asks `get_event` for it, which is what
        # that tool is for.
        answer: dict[str, Any] = {"events": [e.as_json(brief=True) for e in hits[:limit]],
                                  "total": len(hits)}
        # A calendar that could not be read is said out loud. Silently returning
        # what the others held would be an answer that looks complete.
        if errors:
            answer["unread"] = errors
        return answer

    if name == "get_event":
        uid = str(args.get("uid") or "")
        if not uid:
            raise ValueError("get_event needs a uid")
        events, _ = await _snapshot(db, user)
        found = [e.as_json() for e in events if e.uid == uid]
        if not found:
            raise LookupError(f"no appointment with the uid {uid!r} in the fetched window")
        return {"uid": uid, "occurrences": found}

    if name in ("create_event", "update_event"):
        account, row = await cal_access.writing_to(
            db, user, int(args.get("calendar") or 0), by_agent=True)
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("an appointment needs a title")
        try:
            made = await cal_dav.save_event(
                account, row.caldav_id, timezone=user.timezone or "Europe/Berlin",
                uid=str(args.get("uid") or ""), title=title,
                start=str(args.get("start") or ""), end=str(args.get("end") or ""),
                all_day=bool(args.get("all_day")),
                location=str(args.get("location") or ""),
                description=str(args.get("description") or ""))
        except cal_dav.CalDavError as err:
            raise ValueError(f"the calendar refused it: {err}") from None
        # What was just written is not in the fetched set yet, and a caller that
        # reads straight back would be told its own appointment does not exist.
        cal_store.forget(user.id)
        return {**made, "calendar": row.id, "name": row.name}

    if name == "delete_event":
        account, row = await cal_access.writing_to(
            db, user, int(args.get("calendar") or 0), by_agent=True)
        uid = str(args.get("uid") or "")
        if not uid:
            raise ValueError("delete_event needs a uid")
        try:
            gone = await cal_dav.delete_event(account, row.caldav_id, uid)
        except LookupError:
            raise LookupError(f"no calendar {row.caldav_id!r} on that login") from None
        except cal_dav.CalDavError as err:
            raise ValueError(f"the calendar refused it: {err}") from None
        if not gone:
            # Saying "done" here would have the caller report that it removed an
            # appointment it never touched. Nothing was deleted, so nothing is
            # confirmed.
            raise LookupError(f"there was no appointment with the uid {uid!r} in {row.name}")
        cal_store.forget(user.id)
        return {"ok": True, "uid": uid, "calendar": row.id}

    raise LookupError(f"no tool called {name!r}")   # pragma: no cover


def _why_not(row, server) -> str:
    """Why writing is not on offer, in the order the conditions are checked.

    Named rather than implied: "you may not write here" is a dead end, and each
    of these four has a different thing somebody would do about it.
    """
    if server is None:
        return "this calendar is a subscription — it sits on no login"
    if not row.caldav_id:
        return "this calendar names no collection on its login yet"
    if row.server_read_only:
        return "the server allows only reading here"
    if not cal_access.may_write(row, by_agent=True):
        return "writing by the assistant is not switched on for this calendar"
    return ""
