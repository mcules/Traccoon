"""Writing to a calendar.

Reading happens through the share links the vault already knew, and those are
public and read only — which is why creating an appointment needs a different
door: the account's own CalDAV endpoint. Only the calendars behind that account
can be written; a shared ICS address is all the others are.

The protocol is small enough to speak directly. Two things are done properly
here that the side this replaces did by hand:

  * **Where the calendars are is asked for, not assumed.** That code built the
    path of one particular server into itself, so the account had to be on that
    kind of server or nothing worked. The protocol has two questions for it —
    who am I, and where are my calendars — and asking them costs one round trip
    and works everywhere.
  * **The answer is parsed as XML.** Reading it with regular expressions works
    until a server puts its namespace prefix somewhere else, and then it finds
    nothing and says the account has no calendars.
"""
from __future__ import annotations

import base64
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

log = logging.getLogger("notes.caldav")

TIMEOUT = 20.0
DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
NS = {"d": DAV, "cal": CALDAV}


@dataclass
class Account:
    url: str = ""
    user: str = ""
    password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.user and self.password)


@dataclass
class Calendar:
    id: str                        # the last path segment — stable, and what a request addresses
    name: str
    url: str
    read_only: bool


class CalDavError(RuntimeError):
    """The other side said no. Its own words reach whoever asked."""


def _auth(account: Account) -> dict[str, str]:
    raw = f"{account.user}:{account.password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


async def _dav(account: Account, method: str, url: str, body: str | None = None,
               headers: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        return await client.request(method, url, content=body,
                                    headers={**_auth(account), **(headers or {})})


def _text(node, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "").strip() if found is not None else ""


async def _propfind(account: Account, url: str, props: str, depth: str = "0") -> ET.Element:
    body = ('<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            f"<d:prop>{props}</d:prop></d:propfind>")
    answer = await _dav(account, "PROPFIND", url, body,
                        {"Depth": depth, "Content-Type": "application/xml"})
    if answer.status_code >= 400:
        raise CalDavError(f"CalDAV: HTTP {answer.status_code}")
    try:
        return ET.fromstring(answer.text)
    except ET.ParseError as err:
        raise CalDavError(f"CalDAV: the answer is not XML ({err})") from None


async def _entrance(account: Account) -> str:
    """Where to start asking.

    Somebody types the address of their server, not the address of its calendar
    endpoint — and a server's web root does not speak this protocol, it answers
    "method not allowed". The protocol has a signpost for exactly that case: a
    well-known address that says where the real one is. Trying it costs one
    request and saves building one vendor's folder layout into the code, which
    is what the side this replaces did.
    """
    try:
        await _propfind(account, account.url, "<d:current-user-principal/>")
        return account.url
    except CalDavError:
        pass
    origin = urlsplit(account.url)
    return urlunsplit((origin.scheme, origin.netloc, "/.well-known/caldav", "", ""))


async def calendar_home(account: Account) -> str:
    """Where this account's calendars live, asked for rather than guessed.

    Two questions the protocol has for exactly this: who am I, and where are my
    calendars. An address that already points at the calendar home is left alone
    — the second question then answers with itself.
    """
    start = await _entrance(account)
    tree = await _propfind(account, start, "<d:current-user-principal/>")
    principal = _text(tree, ".//d:current-user-principal/d:href")
    if not principal:
        # A server that will not say who we are: take the address as given and
        # let the next request be the one that fails, with its own message.
        return start
    tree = await _propfind(account, urljoin(start, principal), "<cal:calendar-home-set/>")
    home = _text(tree, ".//cal:calendar-home-set/d:href")
    return urljoin(start, home) if home else start


async def calendars(account: Account) -> list[Calendar]:
    """The calendars this account can see, and which of them it may write to."""
    if not account.configured:
        return []
    home = await calendar_home(account)
    tree = await _propfind(
        account, home,
        "<d:displayname/><d:resourcetype/><d:current-user-privilege-set/>", depth="1")
    out: list[Calendar] = []
    for response in tree.findall("d:response", NS):
        kind = response.find(".//d:resourcetype", NS)
        if kind is None or kind.find("cal:calendar", NS) is None:
            continue                              # the home set itself, or an address book
        href = _text(response, "d:href")
        name = _text(response, ".//d:displayname")
        if not href or not name:
            continue
        privileges = response.find(".//d:current-user-privilege-set", NS)
        may_write = privileges is not None and any(
            p.find("d:write", NS) is not None or p.find("d:write-content", NS) is not None
            for p in privileges.findall("d:privilege", NS))
        out.append(Calendar(id=href.rstrip("/").rsplit("/", 1)[-1], name=name,
                            url=urljoin(home, href), read_only=not may_write))
    return out


# ------------------------------------------------------------ writing an event

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;") \
               .replace("\n", "\\n")


def build_ics(*, uid: str, title: str, start: str, end: str, timezone: str,
              all_day: bool = False, location: str = "", description: str = "") -> str:
    """One appointment, written on the local clock so the times mean what they say."""
    import datetime as dt

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plain = lambda v: v.replace("-", "").replace(":", "")          # noqa: E731
    if all_day:
        first = f"DTSTART;VALUE=DATE:{plain(start)}"
        last = f"DTEND;VALUE=DATE:{plain(end)}"
    else:
        first = f"DTSTART;TZID={timezone}:{plain(start)}00"
        last = f"DTEND;TZID={timezone}:{plain(end)}00"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//notes//calendar//EN",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", first, last,
        f"SUMMARY:{_escape(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


async def _writable(account: Account, calendar_id: str) -> Calendar:
    found = next((c for c in await calendars(account) if c.id == calendar_id), None)
    if found is None:
        raise LookupError("no such calendar")
    if found.read_only:
        raise PermissionError("this calendar can only be read")
    return found


async def save_event(account: Account, calendar_id: str, *, timezone: str,
                     uid: str = "", **fields) -> dict:
    """Create an appointment, or replace the one with this identity."""
    calendar = await _writable(account, calendar_id)
    identity = uid or f"{uuid.uuid4()}@notes"
    url = f"{calendar.url.rstrip('/')}/{quote(identity)}.ics"
    existed = False
    if uid:
        existed = (await _dav(account, "HEAD", url)).status_code < 400
    body = build_ics(uid=identity, timezone=timezone, **fields)
    answer = await _dav(account, "PUT", url, body,
                        {"Content-Type": "text/calendar; charset=utf-8"})
    if answer.status_code >= 400:
        raise CalDavError(f"CalDAV: HTTP {answer.status_code}")
    return {"uid": identity, "url": url, "created": not existed}


async def delete_event(account: Account, calendar_id: str, uid: str) -> None:
    calendar = await _writable(account, calendar_id)
    url = f"{calendar.url.rstrip('/')}/{quote(uid)}.ics"
    answer = await _dav(account, "DELETE", url)
    # Already gone is the state that was asked for, so it is not a failure.
    if answer.status_code >= 400 and answer.status_code != 404:
        raise CalDavError(f"CalDAV: HTTP {answer.status_code}")
