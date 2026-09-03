"""A login to a calendar server, and the calendars that sit on it.

The shape this replaced was one account on the person: one server, one user.
Neither holds — appointments live on several servers, and one server holds two
accounts when somebody has a private and a work login on the same machine. The
tests here are about the seams that opens up: whose login is it, what happens to
the calendars when it goes, and can a calendar be written at all.
"""
from __future__ import annotations

import pytest
from conftest import make_user

from app.api import notes_native as nn
from app.notes.calendar import access as cal_access
from app.core.error import Error
from app.models.notes import NotesCalendar
from app.models.notes_servers import NotesCalendarServer


async def a_login(db, user, **fields) -> NotesCalendarServer:
    body = nn.ServerIn(label="Wolke", url="https://example.invalid/dav/",
                       username="wer", password="geheim", **fields)
    out = await nn.add_calendar_server(body, user, db)
    return (await db.get(NotesCalendarServer, out["id"]))


# ------------------------------------------------------------------- the login

@pytest.mark.asyncio
async def test_a_login_keeps_its_password_to_itself(db) -> None:
    user = await make_user(db, "cal1")
    out = await nn.calendar_servers(user, db)
    row = out["servers"][0] if out["servers"] else None
    assert row is None
    server = await a_login(db, user)
    said = (await nn.calendar_servers(user, db))["servers"][0]
    assert said["has_password"] is True
    assert "password" not in said and "password_enc" not in said
    assert server.password_enc and server.password_enc != "geheim"


@pytest.mark.asyncio
async def test_a_login_needs_an_address(db) -> None:
    user = await make_user(db, "cal2")
    with pytest.raises(Error) as err:
        await nn.add_calendar_server(nn.ServerIn(label="leer", url="  "), user, db)
    assert err.value.key == "err.notes_server_no_url"


@pytest.mark.asyncio
async def test_a_trailing_slash_does_not_make_a_second_server(db) -> None:
    """Two logins to the same machine are normal; two spellings of it are not."""
    user = await make_user(db, "cal3")
    a = await a_login(db, user)
    b = await nn.add_calendar_server(
        nn.ServerIn(label="Zweiter", url="https://example.invalid/dav"), user, db)
    assert a.url == b["url"] == "https://example.invalid/dav"


@pytest.mark.asyncio
async def test_two_logins_to_the_same_server_stand_side_by_side(db) -> None:
    user = await make_user(db, "cal4")
    await a_login(db, user)
    await nn.add_calendar_server(
        nn.ServerIn(label="Arbeit", url="https://example.invalid/dav/",
                    username="anderswer", password="auch"), user, db)
    said = (await nn.calendar_servers(user, db))["servers"]
    assert [s["label"] for s in said] == ["Wolke", "Arbeit"]
    assert {s["username"] for s in said} == {"wer", "anderswer"}


@pytest.mark.asyncio
async def test_renaming_a_login_leaves_its_password_alone(db) -> None:
    """A field left empty means "keep what is stored", not "take it away"."""
    user = await make_user(db, "cal5")
    server = await a_login(db, user)
    before = server.password_enc
    await nn.change_calendar_server(server.id, nn.ServerIn(label="Neu"), user, db)
    await db.refresh(server)
    assert server.label == "Neu" and server.password_enc == before


@pytest.mark.asyncio
async def test_somebody_elses_login_is_not_found(db) -> None:
    mine = await make_user(db, "cal6")
    theirs = await make_user(db, "cal7")
    server = await a_login(db, theirs)
    with pytest.raises(Error) as err:
        await nn.change_calendar_server(server.id, nn.ServerIn(label="meins"), mine, db)
    assert err.value.status_code == 404


# --------------------------------------------------------- the calendars on it

@pytest.mark.asyncio
async def test_a_calendar_cannot_be_hung_on_a_login_that_is_not_yours(db) -> None:
    mine = await make_user(db, "cal8")
    theirs = await make_user(db, "cal9")
    server = await a_login(db, theirs)
    with pytest.raises(Error) as err:
        await nn.add_calendar(
            nn.CalendarIn(name="fremd", url="https://example.invalid/x/",
                          server_id=server.id), mine, db)
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_losing_the_login_leaves_the_calendars_as_subscriptions(db) -> None:
    """Deleting somebody's calendars because a password changed would be the
    wrong kind of tidy: they stay, they are still read, they stop being
    written to."""
    user = await make_user(db, "cal10")
    server = await a_login(db, user)
    made = await nn.add_calendar(
        nn.CalendarIn(name="Privat", url="https://example.invalid/dav/privat/",
                      server_id=server.id, caldav_id="privat", write_access="manual"), user, db)
    assert made["writable"] is True
    await nn.drop_calendar_server(server.id, user, db)
    left = (await nn.calendars(user, db))["calendars"]
    assert len(left) == 1
    assert left[0]["url"] == "https://example.invalid/dav/privat/"
    # That the calendar then reads as a subscription is the database's doing,
    # and SQLite keeps foreign keys switched off unless asked — so what is
    # checked here is the rule Postgres acts on.
    key, = NotesCalendar.__table__.c.server_id.foreign_keys
    assert key.ondelete == "SET NULL"


@pytest.mark.asyncio
async def test_only_a_calendar_on_a_login_can_be_written(db) -> None:
    """A subscription is a public address: offering it for writing would be an
    offer that fails on save."""
    user = await make_user(db, "cal11")
    server = await a_login(db, user)
    await nn.add_calendar(nn.CalendarIn(name="Abo", url="https://example.invalid/feed.ics"),
                          user, db)
    await nn.add_calendar(
        nn.CalendarIn(name="Auf dem Server", url="https://example.invalid/dav/privat/",
                      server_id=server.id, caldav_id="privat", write_access="manual"), user, db)
    # …and one that sits on the login but names no collection: there is nothing
    # to write into yet.
    await nn.add_calendar(
        nn.CalendarIn(name="Halb", url="https://example.invalid/dav/halb/",
                      server_id=server.id), user, db)
    said = await nn.writable_calendars(None, user, db)
    assert said["configured"] is True
    assert [c["name"] for c in said["calendars"]] == ["Auf dem Server"]
    assert said["calendars"][0]["server"] == "Wolke"


@pytest.mark.asyncio
async def test_a_switched_off_login_offers_nothing_to_write(db) -> None:
    user = await make_user(db, "cal12")
    server = await a_login(db, user)
    await nn.add_calendar(
        nn.CalendarIn(name="Privat", url="https://example.invalid/dav/privat/",
                      server_id=server.id, caldav_id="privat", write_access="manual"), user, db)
    await nn.change_calendar_server(server.id, nn.ServerIn(enabled=False), user, db)
    said = await nn.writable_calendars(None, user, db)
    assert said["configured"] is False and said["calendars"] == []


@pytest.mark.asyncio
async def test_writing_to_somebody_elses_calendar_is_not_found(db) -> None:
    mine = await make_user(db, "cal13")
    theirs = await make_user(db, "cal14")
    server = await a_login(db, theirs)
    made = await nn.add_calendar(
        nn.CalendarIn(name="Privat", url="https://example.invalid/dav/privat/",
                      server_id=server.id, caldav_id="privat", write_access="manual"), theirs, db)
    with pytest.raises(Error) as err:
        await cal_access.writing_to(db, mine, made["id"])
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_a_calendar_without_a_collection_cannot_be_written(db) -> None:
    user = await make_user(db, "cal15")
    server = await a_login(db, user)
    made = await nn.add_calendar(
        nn.CalendarIn(name="Halb", url="https://example.invalid/dav/halb/",
                      server_id=server.id), user, db)
    with pytest.raises(Error):
        await cal_access.writing_to(db, user, made["id"])


@pytest.mark.asyncio
async def test_an_incomplete_login_is_said_rather_than_raised(db) -> None:
    """Somebody typing is in a state, not in a failure."""
    user = await make_user(db, "cal16")
    made = await nn.add_calendar_server(
        nn.ServerIn(label="Halb", url="https://example.invalid/dav/"), user, db)
    said = await nn.server_collections(made["id"], user, db)
    assert said == {"ok": False, "reason": "incomplete", "collections": []}


@pytest.mark.asyncio
async def test_a_calendar_can_be_moved_between_logins(db) -> None:
    user = await make_user(db, "cal17")
    a = await a_login(db, user)
    b = await nn.add_calendar_server(
        nn.ServerIn(label="Arbeit", url="https://example.invalid/dav/",
                    username="anderswer", password="auch"), user, db)
    made = await nn.add_calendar(
        nn.CalendarIn(name="Privat", url="https://example.invalid/dav/privat/",
                      server_id=a.id, caldav_id="privat", write_access="manual"), user, db)
    moved = await nn.change_calendar(made["id"], nn.CalendarIn(server_id=b["id"]), user, db)
    assert moved["server_id"] == b["id"]
    assert moved["caldav_id"] == "privat"     # the collection is not lost on the way
    row = await db.get(NotesCalendar, made["id"])
    assert row.server_id == b["id"]


# ------------------------------------------------------------ who may write


class _AsAgent:
    """A call carrying a personal access token: the assistant, an agent, a flow.

    The whole API tells the two apart this way — a session reaches as far as its
    person does and has no scopes, a token is measured against what it was
    given.
    """
    class state:
        scopes = ["notes"]


async def on_a_login(db, user, **fields):
    server = await a_login(db, user)
    made = await nn.add_calendar(
        nn.CalendarIn(name="Privat", url="https://example.invalid/dav/privat/",
                      server_id=server.id, caldav_id="privat", **fields), user, db)
    return server, made


@pytest.mark.asyncio
async def test_writing_is_off_until_somebody_says_otherwise(db) -> None:
    """A calendar that turns out to be writable does not thereby become one that
    is written to."""
    user = await make_user(db, "cal18")
    _, made = await on_a_login(db, user)
    assert made["write_access"] == "none"
    assert made["on_a_login"] is True and made["writable"] is False
    with pytest.raises(Error) as err:
        await cal_access.writing_to(db, user, made["id"])
    assert err.value.key == "err.notes_calendar_write_not_allowed"


@pytest.mark.asyncio
async def test_the_assistant_is_a_permission_of_its_own(db) -> None:
    user = await make_user(db, "cal19")
    _, made = await on_a_login(db, user, write_access="manual")
    assert made["writable"] is True and made["agent_may_write"] is False
    account, _ = await cal_access.writing_to(db, user, made["id"])   # the person: allowed
    assert account.configured
    with pytest.raises(Error) as err:
        await cal_access.writing_to(db, user, made["id"], by_agent=True)
    assert err.value.key == "err.notes_calendar_not_for_agents"
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_naming_the_assistant_lets_it_write(db) -> None:
    user = await make_user(db, "cal20")
    _, made = await on_a_login(db, user, write_access="agent")
    assert made["writable"] is True and made["agent_may_write"] is True
    await cal_access.writing_to(db, user, made["id"], by_agent=True)


@pytest.mark.asyncio
async def test_a_permission_nobody_wrote_down_is_not_granted(db) -> None:
    """An unknown word is not an error but a `none`: the safe reading of "I do
    not understand this permission" is not to grant it."""
    user = await make_user(db, "cal21")
    _, made = await on_a_login(db, user, write_access="everybody")
    assert made["write_access"] == "none"


@pytest.mark.asyncio
async def test_what_the_server_forbids_no_setting_allows(db) -> None:
    user = await make_user(db, "cal22")
    _, made = await on_a_login(db, user, write_access="agent")
    row = await db.get(NotesCalendar, made["id"])
    row.server_read_only = True
    await db.commit()
    said = (await nn.calendars(user, db))["calendars"][0]
    assert said["writable"] is False and said["agent_may_write"] is False
    assert said["write_access"] == "agent"    # the wish is kept, not the ability
    with pytest.raises(Error) as err:
        await cal_access.writing_to(db, user, made["id"])
    assert err.value.key == "err.notes_calendar_server_read_only"


@pytest.mark.asyncio
async def test_the_list_of_writable_calendars_follows_who_is_asking(db) -> None:
    """A list that offers what the caller may not write to is a dialog that
    fails after everything has been typed in."""
    user = await make_user(db, "cal23")
    server = await a_login(db, user)
    for name, access in (("Meiner", "manual"), ("Geteilter", "agent"), ("Fremder", "none")):
        await nn.add_calendar(
            nn.CalendarIn(name=name, url=f"https://example.invalid/dav/{name}/",
                          server_id=server.id, caldav_id=name.lower(),
                          write_access=access), user, db)
    person = await nn.writable_calendars(None, user, db)
    agent = await nn.writable_calendars(_AsAgent, user, db)
    assert [c["name"] for c in person["calendars"]] == ["Meiner", "Geteilter"]
    assert [c["name"] for c in agent["calendars"]] == ["Geteilter"]
    assert agent["configured"] is True


@pytest.mark.asyncio
async def test_an_unreachable_server_does_not_take_a_permission_away(db) -> None:
    """Being unable to ask says nothing about what the server allows. Turning
    "I could not reach it" into "you may not write" would take the permission
    away on every hiccup — and `example.invalid` never resolves, so this is
    exactly that case."""
    user = await make_user(db, "cal24")
    _, made = await on_a_login(db, user, write_access="agent")
    assert made["server_read_only"] is False
    assert made["agent_may_write"] is True
