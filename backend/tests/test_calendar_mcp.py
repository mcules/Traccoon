"""The calendars as tools, and what a tool is not allowed to do with them.

What this replaces reached one vendor's server directly, with its own password,
and could write into every calendar that account could see. The point of these
tests is the other half: that the permission somebody set in their account is
the same permission a tool runs into, and that a refusal says which of the four
conditions failed.
"""
from __future__ import annotations

import datetime as dt

import pytest
from conftest import make_user

from app.api import notes_native as nn
from app.core import scopes as scopes_mod
from app.notes.calendar import store as cal_store
from app.services import calendar_mcp as mcp


async def a_login(db, user):
    return await nn.add_calendar_server(
        nn.ServerIn(label="Wolke", url="https://example.invalid/dav/",
                    username="wer", password="geheim"), user, db)


async def a_calendar(db, user, server_id, name="Privat", **fields):
    return await nn.add_calendar(
        nn.CalendarIn(name=name, url=f"https://example.invalid/dav/{name}/",
                      server_id=server_id, caldav_id=name.lower(), **fields), user, db)


@pytest.fixture(autouse=True)
def nothing_fetched(monkeypatch):
    """No calendar is actually read: `example.invalid` does not resolve, and a
    test that depends on a network is a test that fails for the wrong reason."""
    cal_store.forget()
    yield
    cal_store.forget()


# ------------------------------------------------------------------ the door

def test_the_scope_opens_the_tools_and_nothing_else() -> None:
    allowed = {"calendar"}
    assert scopes_mod.allowed(allowed, "POST", "/mcp/calendar")
    assert not scopes_mod.allowed(allowed, "POST", "/mcp/notes")
    assert not scopes_mod.allowed(allowed, "GET", "/notes-native/calendars")
    assert not scopes_mod.allowed(allowed, "POST", "/notes-native/calendar/event")
    # And the other way round: a note token does not reach the appointments.
    assert not scopes_mod.allowed({"notes"}, "POST", "/mcp/calendar")


def test_every_tool_says_what_it_needs() -> None:
    for tool in mcp.toollist():
        assert tool["description"].strip()
        schema = tool["inputSchema"]
        for name in schema["required"]:
            assert name in schema["properties"], (tool["name"], name)


# ------------------------------------------------------------------ the list

@pytest.mark.asyncio
async def test_the_list_says_per_calendar_whether_it_may_be_written(db) -> None:
    user = await make_user(db, "mcp1")
    server = await a_login(db, user)
    await a_calendar(db, user, server["id"], "Frei", write_access="agent")
    await a_calendar(db, user, server["id"], "Meiner", write_access="manual")
    await nn.add_calendar(nn.CalendarIn(name="Abo", url="https://example.invalid/f.ics"),
                          user, db)
    said = await mcp.execute(db, user, "list_calendars", {})
    by_name = {c["name"]: c for c in said["calendars"]}
    assert by_name["Frei"]["you_may_write"] is True
    assert by_name["Frei"]["why_not"] == ""
    assert by_name["Meiner"]["you_may_write"] is False
    assert "not switched on" in by_name["Meiner"]["why_not"]
    assert by_name["Abo"]["you_may_write"] is False
    assert "subscription" in by_name["Abo"]["why_not"]
    assert by_name["Frei"]["login"] == "Wolke"


@pytest.mark.asyncio
async def test_the_reason_names_the_condition_that_failed(db) -> None:
    user = await make_user(db, "mcp2")
    server = await a_login(db, user)
    half = await nn.add_calendar(
        nn.CalendarIn(name="Halb", url="https://example.invalid/dav/halb/",
                      server_id=server["id"], write_access="agent"), user, db)
    said = await mcp.execute(db, user, "list_calendars", {})
    row = next(c for c in said["calendars"] if c["id"] == half["id"])
    assert "names no collection" in row["why_not"]


# ----------------------------------------------------------------- the write

@pytest.mark.asyncio
async def test_a_tool_cannot_write_where_only_the_person_may(db) -> None:
    """The whole point: a permission set in the account is the permission the
    tool runs into."""
    user = await make_user(db, "mcp3")
    server = await a_login(db, user)
    made = await a_calendar(db, user, server["id"], write_access="manual")
    with pytest.raises(Exception) as err:
        await mcp.execute(db, user, "create_event", {
            "calendar": made["id"], "title": "Termin",
            "start": "2026-09-10T09:00", "end": "2026-09-10T10:00"})
    assert "assistant" in str(getattr(err.value, "detail", err.value))


@pytest.mark.asyncio
async def test_a_tool_cannot_write_into_a_subscription(db) -> None:
    user = await make_user(db, "mcp4")
    made = await nn.add_calendar(
        nn.CalendarIn(name="Abo", url="https://example.invalid/f.ics",
                      write_access="agent"), user, db)
    with pytest.raises(Exception):
        await mcp.execute(db, user, "delete_event", {"calendar": made["id"], "uid": "x"})


@pytest.mark.asyncio
async def test_somebody_elses_calendar_is_simply_not_there(db) -> None:
    mine = await make_user(db, "mcp5")
    theirs = await make_user(db, "mcp6")
    server = await a_login(db, theirs)
    made = await a_calendar(db, theirs, server["id"], write_access="agent")
    with pytest.raises(Exception) as err:
        await mcp.execute(db, mine, "create_event", {
            "calendar": made["id"], "title": "Termin",
            "start": "2026-09-10T09:00", "end": "2026-09-10T10:00"})
    assert getattr(err.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_an_appointment_without_a_title_is_refused_before_the_server(db) -> None:
    user = await make_user(db, "mcp7")
    server = await a_login(db, user)
    made = await a_calendar(db, user, server["id"], write_access="agent")
    with pytest.raises(ValueError):
        await mcp.execute(db, user, "create_event", {
            "calendar": made["id"], "title": "   ",
            "start": "2026-09-10T09:00", "end": "2026-09-10T10:00"})


# ------------------------------------------------------------------ the read

@pytest.mark.asyncio
async def test_a_day_outside_the_window_is_said_rather_than_answered_empty(db) -> None:
    """Nothing found and not looked for read the same way to a caller, and only
    one of them is true."""
    user = await make_user(db, "mcp8")
    far = (dt.date.today() + dt.timedelta(days=mcp.DAYS_AHEAD + 30)).isoformat()
    with pytest.raises(ValueError) as err:
        await mcp.execute(db, user, "list_events", {"from": far, "to": far})
    assert "fetched" in str(err.value)


@pytest.mark.asyncio
async def test_a_date_that_is_not_one_says_so(db) -> None:
    user = await make_user(db, "mcp9")
    with pytest.raises(ValueError) as err:
        await mcp.execute(db, user, "list_events", {"from": "10.09.2026", "to": "2026-09-11"})
    assert "YYYY-MM-DD" in str(err.value)


@pytest.mark.asyncio
async def test_a_backwards_span_is_refused(db) -> None:
    user = await make_user(db, "mcp10")
    with pytest.raises(ValueError):
        await mcp.execute(db, user, "list_events",
                          {"from": "2026-09-11", "to": "2026-09-10"})


@pytest.mark.asyncio
async def test_a_calendar_that_could_not_be_read_is_named(db) -> None:
    """Returning what the other calendars held would be an answer that looks
    complete."""
    user = await make_user(db, "mcp11")
    await nn.add_calendar(nn.CalendarIn(name="Kaputt", url="https://example.invalid/f.ics"),
                          user, db)
    today = dt.date.today().isoformat()
    said = await mcp.execute(db, user, "list_events", {"from": today, "to": today})
    assert said["events"] == []
    assert said["unread"] and said["unread"][0]["calendar"] == "Kaputt"


@pytest.mark.asyncio
async def test_an_unknown_tool_is_a_sentence_not_a_crash(db) -> None:
    user = await make_user(db, "mcp12")
    with pytest.raises(LookupError):
        await mcp.execute(db, user, "calendar_drop_everything", {})


@pytest.mark.asyncio
async def test_deleting_something_that_was_not_there_is_not_a_success(db, monkeypatch) -> None:
    """The route a person clicks stays idempotent — deleting twice means the
    same thing both times. A tool handed a wrong identity must not be told it
    removed an appointment, because it reports that on."""
    user = await make_user(db, "mcp13")
    server = await a_login(db, user)
    made = await a_calendar(db, user, server["id"], write_access="agent")

    async def nothing_there(account, calendar_id, uid):
        return False
    monkeypatch.setattr(mcp.cal_dav, "delete_event", nothing_there)
    monkeypatch.setattr(mcp.cal_access, "writing_to", _pretend(made["id"]))
    with pytest.raises(LookupError) as err:
        await mcp.execute(db, user, "delete_event", {"calendar": made["id"], "uid": "erfunden"})
    assert "erfunden" in str(err.value)


@pytest.mark.asyncio
async def test_a_deletion_that_happened_is_confirmed(db, monkeypatch) -> None:
    user = await make_user(db, "mcp14")
    server = await a_login(db, user)
    made = await a_calendar(db, user, server["id"], write_access="agent")

    async def it_was_there(account, calendar_id, uid):
        return True
    monkeypatch.setattr(mcp.cal_dav, "delete_event", it_was_there)
    monkeypatch.setattr(mcp.cal_access, "writing_to", _pretend(made["id"]))
    said = await mcp.execute(db, user, "delete_event", {"calendar": made["id"], "uid": "echt"})
    assert said == {"ok": True, "uid": "echt", "calendar": made["id"]}


def _pretend(cid):
    """The permission check is measured on its own above; here the account has
    to come back without a server being reachable."""
    from app.notes.calendar import caldav as dav

    async def writing_to(db, user, wanted, by_agent=False):
        from app.notes.calendar import access
        row = await access.own_calendar(db, user, wanted)
        assert access.may_write(row, by_agent)
        return dav.Account(url="https://example.invalid/dav/", user="wer", password="x"), row
    return writing_to
