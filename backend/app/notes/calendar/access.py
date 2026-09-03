"""Whose calendar it is, and who may write into it.

This sits below both the routes and the tools rather than inside either. The
question "may this write happen" has exactly one answer per calendar, and a
second copy of it — one for the interface, one for whatever writes on somebody's
behalf — is how the two come to disagree about a permission somebody set once.

The distinction the permission rests on is the one the whole API already makes:
a session reaches as far as its person does, and anything acting on that
person's behalf carries a token.
"""
from __future__ import annotations

import logging

from fastapi import Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.error import Error
from ...core.security import decrypt_secret
from ...models.notes import (WRITE_ACCESS, WRITE_AGENT, WRITE_MANUAL, WRITE_NONE,
                             NotesCalendar)
from ...models.notes_servers import NotesCalendarServer
from ...models.user import User
from . import caldav, fetch

log = logging.getLogger("notes")


async def servers_of(db: AsyncSession, user: User) -> list[NotesCalendarServer]:
    """Every login this person has to a calendar server, in their own order."""
    return list((await db.execute(
        select(NotesCalendarServer)
        .where(NotesCalendarServer.owner_user_id == user.id)
        .order_by(NotesCalendarServer.position, NotesCalendarServer.id))).scalars().all())


async def calendars_of(db: AsyncSession, user: User) -> list[NotesCalendar]:
    """This person's calendars, in their own order."""
    return list((await db.execute(
        select(NotesCalendar).where(NotesCalendar.owner_user_id == user.id)
        .order_by(NotesCalendar.position, NotesCalendar.id))).scalars().all())


def write_access_of(given: str) -> str:
    """The permission as it was asked for, or nothing at all.

    An unknown word becomes `none` rather than an error: the safe reading of "I
    do not understand this permission" is not to grant it.
    """
    return given if given in WRITE_ACCESS else WRITE_NONE


def may_write(row: NotesCalendar, by_agent: bool) -> bool:
    """Whether an appointment may be written here, by this kind of caller.

    Four things have to hold at once, and they fail for four different reasons:
    the calendar sits on a login, it names a collection, the server grants
    writing, and this person allowed it — the assistant additionally needs to
    have been named, because "I may write here" and "something may write here
    for me" are not the same permission.
    """
    if not (row.server_id and row.caldav_id) or row.server_read_only:
        return False
    if by_agent:
        return row.write_access == WRITE_AGENT
    return row.write_access in (WRITE_MANUAL, WRITE_AGENT)


async def own_calendar(db: AsyncSession, user: User, cid: int) -> NotesCalendar:
    row = (await db.execute(select(NotesCalendar).where(
        NotesCalendar.id == cid,
        NotesCalendar.owner_user_id == user.id))).scalar_one_or_none()
    if row is None:
        # The same answer for somebody else's calendar as for one that is not
        # there: telling the two apart would say that it exists.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_calendar_not_found",
                    "No such calendar")
    return row


async def own_server(db: AsyncSession, user: User, sid: int) -> NotesCalendarServer:
    row = await db.get(NotesCalendarServer, sid)
    if row is None or row.owner_user_id != user.id:
        # The same answer for somebody else's login as for one that is not
        # there: telling them apart says whether it exists.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_server_not_found",
                    "No such calendar login")
    return row


def account_of(server: NotesCalendarServer) -> caldav.Account:
    password = ""
    if server.password_enc:
        try:
            password = decrypt_secret(server.password_enc)
        except Exception:                         # noqa: BLE001
            log.warning("notes: the password of server %s cannot be read", server.id)
    return caldav.Account(url=server.url, user=server.username, password=password)


async def ask_the_server(row: NotesCalendar, server: NotesCalendarServer | None) -> None:
    """Whether the server grants writing to this collection, asked rather than
    assumed.

    A failure here leaves the flag where it was. Being unable to reach a server
    while saving a calendar says nothing about that server's permissions, and
    turning "I could not ask" into "you may not write" would take a permission
    away every time the network hiccups.
    """
    if server is None or not row.caldav_id:
        row.server_read_only = False
        return
    account = account_of(server)
    if not account.configured:
        return
    try:
        found = await caldav.calendars(account)
    except caldav.CalDavError:
        log.info("notes: server %s could not be asked about %s", server.id, row.caldav_id)
        return
    for c in found:
        if c.id == row.caldav_id:
            row.server_read_only = c.read_only
            return


async def sources_of(db: AsyncSession, user: User) -> list[fetch.Source]:
    """This person's calendars, as the fetcher wants them.

    The password is decrypted here and nowhere else — it exists as plain text
    for the length of one fetch and never leaves this process.
    """
    rows = (await db.execute(
        select(NotesCalendar).where(NotesCalendar.owner_user_id == user.id,
                                    NotesCalendar.enabled.is_(True))
        .order_by(NotesCalendar.position, NotesCalendar.id))).scalars().all()
    servers = {s.id: s for s in await servers_of(db, user)}
    out = []
    for row in rows:
        # A calendar that belongs to a login is read with that login. Its own
        # user and password are for a subscription that asks for one itself,
        # which is a different thing and rarer.
        server = servers.get(row.server_id) if row.server_id else None
        if server is not None:
            user_name, secret = server.username, server.password_enc
            what = f"server {server.id}"
        else:
            user_name, secret = row.auth_user, row.auth_password_enc
            what = f"calendar {row.id}"
        password = ""
        if secret:
            try:
                password = decrypt_secret(secret)
            except Exception:                     # noqa: BLE001
                log.warning("notes: the password of %s cannot be read", what)
        out.append(fetch.Source(name=row.name or f"Kalender {row.id}", url=row.url,
                                    link_target=row.link_target,
                                    auth_user=user_name, auth_password=password))
    return out


def is_agent(request: Request | None) -> bool:
    """Whether this call comes from a token rather than from somebody's window.

    The same distinction the whole API already makes: a session reaches as far
    as its person does (`scopes is None`), a personal access token is measured
    against what it was given. Everything that writes on somebody's behalf —
    the assistant, an agent, a flow — carries a token; a person clicking in the
    interface does not.
    """
    return bool(request is not None and getattr(request.state, "scopes", None) is not None)


async def writing_to(db: AsyncSession, user: User, cid: int,
                      by_agent: bool = False) -> tuple[caldav.Account, NotesCalendar]:
    """The login an appointment in this calendar is written through."""
    row = await own_calendar(db, user, cid)
    if row.server_id is None or not row.caldav_id:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_calendar_read_only",
                    "That calendar can only be read")
    if row.server_read_only:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_calendar_server_read_only",
                    "The server does not allow writing to {name}", name=row.name or row.url)
    if row.write_access == WRITE_NONE:
        raise Error(status.HTTP_403_FORBIDDEN, "err.notes_calendar_write_not_allowed",
                    "Writing to {name} is switched off", name=row.name or row.url)
    if by_agent and row.write_access != WRITE_AGENT:
        raise Error(status.HTTP_403_FORBIDDEN, "err.notes_calendar_not_for_agents",
                    "Only you may write to {name}, not the assistant",
                    name=row.name or row.url)
    server = await own_server(db, user, row.server_id)
    account = account_of(server)
    if not account.configured:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_no_calendar_account",
                    "No account to write appointments through")
    return account, row
