"""Which newsletters arrive in a mailbox, and how one gets out of them again.

A newsletter says of itself that it is one. Since RFC 2369 it carries `List-Unsubscribe`, and
since RFC 8058 often a `List-Unsubscribe-Post` beside it, which means: one HTTP POST and you
are out, without a landing page, without a login, without "please tell us why".

So this is not a guess about what a subscription might be. It is the list of senders who
declared themselves, grouped and counted, with the way out that they named themselves.

What it is NOT: a spam filter. Whoever sends without an unsubscribe header does not turn up
here, and that is honest: for those the way out is not a button but the junk folder.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import re

from imapclient import IMAPClient

from ..models.mail import MailAccount
from .mailbox import _header, _imap

log = logging.getLogger("traccoon.newsletters")

# How far back to look. A newsletter that has not arrived in a year is not a subscription one
# is troubled by, and every mail costs a line in the FETCH.
LOOK_AT = 800

FIELDS = "BODY.PEEK[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST LIST-ID SUBJECT)]"


def _addresses(raw: str) -> tuple[str, str]:
    """The two ways out of a `List-Unsubscribe`, as (http, mailto).

    The header holds one or both, each in angle brackets. The HTTP one is preferred where the
    sender allows one click (RFC 8058); the mail is the fallback and works everywhere, only
    it takes a moment longer.
    """
    http, mail = "", ""
    for hit in re.findall(r"<([^>]+)>", raw or ""):
        target = hit.strip()
        if target.lower().startswith("http") and not http:
            http = target
        elif target.lower().startswith("mailto:") and not mail:
            mail = target
    return http, mail


def _list(raw: str) -> tuple[str, str]:
    """(id, name) of a `List-Id` header.

    It is built as `"Name of the list" <id.of.the.list>`, and both halves are worth having:
    the id groups, the name is what a person recognises.
    """
    if not raw:
        return "", ""
    inner = re.search(r"<([^>]+)>", raw)
    name = raw[:inner.start()] if inner else ""
    return ((inner.group(1) if inner else raw).strip().lower(),
            _header(name).strip().strip('"').strip())


def _key(sender: str, list_id: str) -> str:
    """What makes two mails the same subscription.

    The `List-ID` where there is one, because a house can run several lists from one address.
    Otherwise the sender, lower case: `News@Shop.de` and `news@shop.de` are one subscription
    and would otherwise stand twice.
    """
    return list_id or sender.strip().lower()


def _scan_sync(account: MailAccount, folders: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    with _imap(account) as client:
        for folder in folders:
            try:
                client.select_folder(folder, readonly=True)
                uids = client.search(["ALL"])
            except Exception:  # noqa: BLE001 — a folder without permission is not the end
                log.debug("no access to %s", folder)
                continue
            excerpt = uids[-LOOK_AT:]
            if not excerpt:
                continue
            raw = client.fetch(excerpt, [FIELDS, "INTERNALDATE"])
            for uid in excerpt:
                entry = raw.get(uid) or {}
                head = b""
                for name, value in entry.items():
                    if isinstance(name, bytes) and name.startswith(b"BODY[HEADER.FIELDS"):
                        head = value or b""
                        break
                if not head:
                    continue
                text = head.decode("utf-8", "replace")
                out = re.search(r"^List-Unsubscribe:\s*(.+?)(?=^\S|\Z)", text,
                                 re.I | re.M | re.S)
                if not out:
                    continue
                sender_line = re.search(r"^From:\s*(.+?)(?=^\S|\Z)", text, re.I | re.M | re.S)
                list_line = re.search(r"^List-Id:\s*(.+?)(?=^\S|\Z)", text, re.I | re.M | re.S)
                post_line = re.search(r"^List-Unsubscribe-Post:\s*(.+?)(?=^\S|\Z)", text,
                                       re.I | re.M | re.S)
                name, address = email.utils.parseaddr(
                    _header(sender_line.group(1).strip()) if sender_line else "")
                list_id, list_name = _list(list_line.group(1).strip() if list_line else "")
                key = _key(address, list_id)
                if not key:
                    continue
                # On a mailing list the sender is whoever wrote today. One does not unsubscribe
                # from them, one unsubscribes from the list, so that is the name that stands
                # here: `list_id` where the header carries no readable one.
                shown = list_name or (list_id if list_id else (name or address))
                http, mail = _addresses(out.group(1))
                when = entry.get(b"INTERNALDATE")
                sitting = found.setdefault(key, {
                    "key": key, "name": shown, "sender": address, "list_id": list_id,
                    "folder": folder, "uid": uid, "count": 0, "last": None,
                    "http": http, "mailto": mail, "one_click": False,
                })
                sitting["count"] += 1
                if when and (sitting["last"] is None or when > sitting["last"]):
                    # The newest mail of a subscription carries the way out that counts: an
                    # address from three years ago may be dead.
                    sitting.update({"last": when, "folder": folder, "uid": uid,
                                     "http": http or sitting["http"],
                                     "mailto": mail or sitting["mailto"],
                                     "name": shown or sitting["name"]})
                if post_line and "one-click" in post_line.group(1).lower():
                    sitting["one_click"] = True

    listing = sorted(found.values(), key=lambda e: e["count"], reverse=True)
    for entry in listing:
        entry["last"] = entry["last"].isoformat() if entry["last"] else ""
    return listing


async def scan(account: MailAccount, folders: list[str]) -> list[dict]:
    return await asyncio.to_thread(_scan_sync, account, folders)


def _one_click_sync(url: str) -> tuple[bool, str]:
    """The unsubscribe of RFC 8058: one POST, no page, no login.

    Deliberately without following redirects into a browser world: what answers with 2xx has
    understood it. Everything else is reported as it is, because "probably went through" is
    the one thing a person cannot use here.
    """
    import httpx

    try:
        answer = httpx.post(url, data={"List-Unsubscribe": "One-Click"}, timeout=20,
                             follow_redirects=True,
                             headers={"User-Agent": "Traccoon"})
        return 200 <= answer.status_code < 300, f"HTTP {answer.status_code}"
    except Exception as error:  # noqa: BLE001
        return False, str(error)[:200]


async def one_click(url: str) -> tuple[bool, str]:
    return await asyncio.to_thread(_one_click_sync, url)
