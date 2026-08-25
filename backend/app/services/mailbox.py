"""Access to a mailbox: folders, messages, attachments, drafts, sending.

Why synchronous and in a thread pool: `imapclient` is a proven, blocking library — the same
one that has been running in `imap-mcp` for months. An asynchronous IMAP rebuild would be a
second building site without a gain; the waiting time sits in the network anyway, not in the
CPU. Every call opens its connection and closes it again: mailboxes are used in bursts here
(one list, one message), and an open connection per person would be a state nobody
tidies up.

What does NOT happen here: deciding. This layer reads and writes, it judges nothing. What is
to happen to a mail is written in a flow (`mail_action`) or lies in the hands of the person
in front of the screen.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import email
import email.policy
import logging
import os
import smtplib
import ssl
import threading
import time
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from imapclient import IMAPClient

from ..core.security import decrypt_secret
from ..models.mail import MailAccount, MailIdentity

log = logging.getLogger("mailbox")

# How much text of a message goes into the UI. A mail with an embedded image archive in its
# HTML part already has megabytes of base64 — nobody needs to read that.
MAX_TEXT = 200_000


def _join(account: MailAccount) -> IMAPClient:
    client = IMAPClient(account.imap_host, port=account.imap_port, ssl=account.imap_ssl,
                        timeout=30)
    client.login(account.imap_user, decrypt_secret(account.imap_password_enc))
    return client


# ── Offene Verbindungen ──────────────────────────────────────────────────────
# Logging in costs a TLS handshake and a LOGIN: measured at 266 ms, and that on EVERY call.
# While a page is being built that is three or four logins in a row for questions that are
# not 300 ms of work together.
#
# So the connection stays around and gets reused. IMAP is stateful (a SELECT applies to the
# connection, not to the call), which is why every call borrows exactly one — using the same
# connection in parallel would mix the folders up.
POOL_MAX = int(os.getenv("MAIL_POOL", "3"))       # per account
LEERLAUF_S = 240.0                                 # after that it is probably dead

_pool: dict[int, list[tuple[IMAPClient, float]]] = {}
_pool_lock = threading.Lock()


def _from_pool(account: MailAccount) -> IMAPClient | None:
    """A parked connection, provided it is still alive."""
    with _pool_lock:
        pool = _pool.get(account.id) or []
        while pool:
            client, latest = pool.pop()
            if time.monotonic() - latest > LEERLAUF_S:
                _close(client)
                continue
            _pool[account.id] = pool
            return client
        _pool[account.id] = []
    return None


def _back(account_id: int, client: IMAPClient) -> None:
    with _pool_lock:
        pool = _pool.setdefault(account_id, [])
        if len(pool) >= POOL_MAX:
            _close(client)
            return
        pool.append((client, time.monotonic()))


def _close(client: IMAPClient) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — closing a dead connection must cost nothing
        pass


def pool_empty(account_id: int | None = None) -> None:
    """Throw all parked connections away — after changed credentials."""
    with _pool_lock:
        accounts = [account_id] if account_id is not None else list(_pool)
        for k in accounts:
            for client, _ in _pool.pop(k, []):
                _close(client)


@contextmanager
def _imap(account: MailAccount):
    """Borrow a connection. It goes back into the pool if nothing went wrong.

    A used connection may have died quietly (the server disconnects after a few minutes of
    silence). That is why it is tapped with a NOOP before anyone gets it — a
    Roundtrip statt eines Fehlers mitten in einer Antwort.
    """
    client = _from_pool(account)
    if client is not None:
        try:
            client.noop()
        except Exception:  # noqa: BLE001 — then a fresh one it is
            _close(client)
            client = None
    if client is None:
        client = _join(account)
    try:
        yield client
    except Exception:
        # After an error the state of the connection is unclear (a half-read answer, an
        # aborted FETCH). Putting one like that back would mean passing the error to the next
        # Aufruf weiterzureichen.
        _close(client)
        raise
    else:
        _back(account.id, client)


def _text_from(msg: email.message.Message) -> tuple[str, str]:
    """(Text, HTML) einer Nachricht — beides, soweit vorhanden."""
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():          # attachments do not belong in the body text
                continue
            content = part.get_content_type()
            try:
                raw = part.get_payload(decode=True) or b""
                piece = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — a broken encoding must not swallow the mail
                continue
            if content == "text/plain" and not text:
                text = piece
            elif content == "text/html" and not html:
                html = piece
    else:
        raw = msg.get_payload(decode=True) or b""
        piece = raw.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html = piece
        else:
            text = piece
    if html and not text:
        import html2text

        converter = html2text.HTML2Text()
        converter.ignore_images = True
        converter.body_width = 0
        text = converter.handle(html)
    return text[:MAX_TEXT], html[:MAX_TEXT]


# What may survive from a foreign mail. Everything else goes: scripts of course, but forms
# too (a login mask inside the mailbox is exactly the trick phishing is about),
# eingebettete Rahmen und Objekte.
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "div",
    "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "li", "ol", "p", "pre", "s", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
_ALLOWED_ATTRIBUTE = {
    "*": {"style", "title", "align", "width", "height", "colspan", "rowspan", "dir"},
    # `rel` is deliberately missing here: nh3 sets it itself (`link_rel`) and refuses it as an
    # allowed attribute so that nobody can water the link protection down again.
    "a": {"href", "target", "title"},
    "img": {"src", "alt", "title", "width", "height"},
}


def has_content(html: str) -> bool:
    """Is there anything left to see in this HTML?

    An HTML part is not the same as an HTML mail. Plenty of senders hang a second part on
    their message that consists of nothing but a wrapper, a tracking pixel and a `<style>`
    block, and the block is the first thing the cleaning throws away. What is left is an empty
    white box beside a tab called "formatted", while the readable text sits in the plain part
    the reader never gets offered.

    So: text counts, and a picture counts (a mail that is one single graphic is a mail).
    Empty tags and non-breaking spaces do not.
    """
    import re

    if re.search(r"<img\b", html or "", re.I):
        return True
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.replace("&nbsp;", " ").replace("&zwnj;", " ").replace("&shy;", "")
    return bool(text.strip())


def clean(html: str) -> tuple[str, bool]:
    """Returns (cleaned HTML, whether there are remote images).

    Remote images are not removed but **rehung**: the address moves to `data-fern`, `src`
    disappears. That way the message stays complete but loads nothing afterwards — a loaded
    image is a signal back to the sender that it was read, and that decision belongs to the
    person and not to the mailbox.
    """
    import re

    import nh3

    clean = nh3.clean(html or "", tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTE,
                       url_schemes={"http", "https", "mailto", "cid", "data"},
                       link_rel="noopener noreferrer nofollow")
    fern = False

    def um(hits):
        nonlocal fern
        address = hits.group(2)
        if address.startswith("data:"):
            return hits.group(0)
        fern = True
        return f'{hits.group(1)}data-fern="{address}"'

    clean = re.sub(r'(<img\b[^>]*?)src="([^"]*)"', um, clean, flags=re.I)
    return clean, fern


def _kind(part, name: str) -> str:
    """The file type of an attachment, and the file name has the last word.

    Plenty of senders declare every attachment as `application/octet-stream`, a PDF invoice
    included: it is the "I do not know" of the mail world. Traccoon then knew nothing either
    and offered no preview for a file whose name ends in `.pdf`. So: a declared type counts,
    but a generic one is only a placeholder, and the extension is the better guess.
    """
    import mimetypes

    declared = (part.get_content_type() or "").lower()
    if declared and declared not in ("application/octet-stream", "application/x-download",
                                      "binary/octet-stream", "content/unknown"):
        return declared
    guessed, _ = mimetypes.guess_type(name or "")
    return guessed or declared or "application/octet-stream"


def _attachments(msg: email.message.Message) -> list[dict]:
    """Index of the attachments: name, type, size. The content is fetched only on demand, a
    list of twenty mails must not drag twenty PDFs across the network."""
    out = []
    for i, part in enumerate(msg.walk()):
        name = part.get_filename()
        if not name:
            continue
        raw = part.get_payload(decode=True) or b""
        readable = _header(name)
        out.append({"index": i, "filename": readable,
                    "content_type": _kind(part, readable), "size": len(raw)})
    return out


def _header(raw) -> str:
    """Kopfzeile lesbar machen (=?utf-8?B?…?= und Konsorten)."""
    from email.header import decode_header, make_header

    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:  # noqa: BLE001
        return str(raw)


def _header_line_addresses(raw) -> list[dict]:
    out = []
    for part in str(raw or "").split(","):
        name, addr = parseaddr(part.strip())
        if addr:
            out.append({"name": _header(name), "addr": addr})
    return out


# ── Lesen ───────────────────────────────────────────────────────────────────

# Order of the special folders the way every mail program shows them: what one needs daily
# stands at the top, the rest alphabetically below.
_SPECIAL_SERIES = ["inbox", "drafts", "sent", "junk", "trash", "archive"]


def tree_sort(entries: list[dict]) -> list[dict]:
    """Ordner in Anzeigereihenfolge: jedes Kind direkt unter seinem Elternteil.

    Sorting by the full name looks like a tree and is none: `Archives` comes alphabetically
    before `INBOX.Aliexpress`, so the subfolders of the inbox slid below the archive — with
    indentation, which makes the impression perfect. So descend for real: sort per level, then
    put the children behind it.

    `level` then comes from the depth in the tree and no longer from the number of separators
    in the name: a folder whose parent the server does not list at all is a root and must not
    point indented into the void.
    """
    by_name = {e["name"]: e for e in entries}
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for e in entries:
        parent = e.get("parent") or ""
        if parent and parent in by_name:
            children.setdefault(parent, []).append(e)
        else:
            roots.append(e)

    def key(e: dict) -> tuple:
        rank = _SPECIAL_SERIES.index(e["special"]) if e["special"] in _SPECIAL_SERIES else 99
        return (rank, (e.get("display") or e["name"]).lower())

    out: list[dict] = []

    def descend(node: list[dict], level: int) -> None:
        for e in sorted(node, key=key):
            e["level"] = level
            out.append(e)
            descend(children.get(e["name"], []), level + 1)

    descend(roots, 0)
    return out


def _folder_sync(account: MailAccount, count: bool) -> list[dict]:
    """The folders as a tree: name, display name, level, parent folder — and on request the
    der ungelesenen Nachrichten.

    The separator comes from the server (a dot with Courier, a slash with Dovecot); guessing
    it would mean showing a flat list with dots in the name at every second provider instead
    einer Struktur.

    Counting happens only on demand: one STATUS per folder is forty questions to the mailbox
    when there are forty folders, and nobody needs that while paging through a list.
    """
    with _imap(account) as client:
        raw = []
        for flags, separator, name in client.list_folders():
            marker = {f.decode().lower() for f in flags}
            if "\\noselect" in marker:
                continue
            special = next((k.lstrip("\\") for k in marker
                              if k in ("\\sent", "\\drafts", "\\trash", "\\junk",
                                       "\\archive")), "")
            if not special:
                # Not every server marks its special folders (RFC 6154). Then what is entered
                # on the account decides — the person knows better than a list of names in the
                # code.
                mapping = {account.folder_sent: "sent", account.folder_drafts: "drafts",
                             account.folder_trash: "trash", account.folder_junk: "junk",
                             account.folder_archive: "archive"}
                special = mapping.get(name, "")
            if name.upper() == "INBOX":
                special = "inbox"
            separator = separator.decode() if isinstance(separator, bytes) else (separator or "/")
            parts = name.split(separator) if separator else [name]
            raw.append({
                "name": name,
                "display": parts[-1] if parts else name,
                "level": max(0, len(parts) - 1),
                "parent": separator.join(parts[:-1]) if len(parts) > 1 else "",
                "delimiter": separator,
                "special": special,
                "unseen": 0, "total": 0,
            })

        if count:
            for entry in raw:
                try:
                    state = client.folder_status(entry["name"], ["UNSEEN", "MESSAGES"])
                    entry["unseen"] = int(state.get(b"UNSEEN", 0))
                    entry["total"] = int(state.get(b"MESSAGES", 0))
                except Exception:  # noqa: BLE001 — a folder without an answer is no error
                    log.debug("no status for %s", entry["name"])

        return tree_sort(raw)


def _has_attachment(structure) -> bool:
    """Does this message have an attachment? — answered from the BODYSTRUCTURE.

    The whole point of the question is to answer it WITHOUT loading the mail: a list of fifty
    messages must not drag fifty attachments across the network. The structure is nested and
    differently deep depending on the server, which is why it is searched instead of
    an festen Stellen abgegriffen.

    Only `attachment` counts. An embedded logo in the HTML (`inline`) is no attachment, and a
    paperclip on every piece of advertising would no longer be information.
    """
    def search(part) -> bool:
        if not isinstance(part, (list, tuple)):
            return False
        for element in part:
            if isinstance(element, bytes) and element.lower() == b"attachment":
                return True
            if search(element):
                return True
        return False

    return search(structure)


def _listing_sync(account: MailAccount, folder: str, search: str, offset: int,
                limit: int) -> dict:
    with _imap(account) as client:
        state = client.select_folder(folder, readonly=True)
        total = state.get(b"EXISTS", 0)
        criterion = ["TEXT", search] if search else ["ALL"]
        uids = client.search(criterion)
        uids = list(reversed(uids))          # neueste zuerst, wie in jedem Postfach
        excerpt = uids[offset:offset + limit]
        if not excerpt:
            return {"total": len(uids), "exists": total, "messages": []}
        raw = client.fetch(excerpt, ["ENVELOPE", "FLAGS", "RFC822.SIZE", "INTERNALDATE",
                                        "BODYSTRUCTURE"])
        messages = []
        for uid in excerpt:
            entry = raw.get(uid) or {}
            envelope = entry.get(b"ENVELOPE")
            flags = {f.decode().lower() for f in entry.get(b"FLAGS", ())}
            sender = ""
            if envelope is not None and envelope.from_:
                first = envelope.from_[0]
                name = _header(first.name.decode() if first.name else "")
                address = f"{(first.mailbox or b'').decode()}@{(first.host or b'').decode()}"
                sender = formataddr((name, address))
            messages.append({
                "uid": uid,
                "subject": _header(envelope.subject.decode("utf-8", "replace")
                                 if envelope is not None and envelope.subject else ""),
                "from": sender,
                "date": (entry.get(b"INTERNALDATE") or dt.datetime.now(dt.timezone.utc)).isoformat(),
                "size": entry.get(b"RFC822.SIZE", 0),
                "seen": "\\seen" in flags,
                "flagged": "\\flagged" in flags,
                "answered": "\\answered" in flags,
                "has_attachment": _has_attachment(entry.get(b"BODYSTRUCTURE")),
            })
        return {"total": len(uids), "exists": total, "messages": messages}


def _message_sync(account: MailAccount, folder: str, uid: int) -> dict:
    """Read a message, and read it only.

    Read-only on purpose: an IMAP server sets `\\Seen` by itself when a mailbox is open for
    writing and somebody fetches the body. Opening would then already be reading, and
    clicking past something in a list would silently take its mark away. When a mail counts as
    read is decided one level up (three seconds open), and it is said there with a flag of its
    own.
    """
    with _imap(account) as client:
        client.select_folder(folder, readonly=True)
        raw = client.fetch([uid], ["RFC822", "FLAGS"])
        entry = raw.get(uid)
        if not entry:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(entry[b"RFC822"], policy=email.policy.default)
        text, html = _text_from(msg)
        html_clean, remoteimages = clean(html) if html else ("", False)
        # A part that carries nothing is no part: without this the reader gets a choice
        # between an empty page and the text, with the empty one preselected.
        if html_clean and not has_content(html_clean):
            html_clean, remoteimages = "", False
        flags = {f.decode().lower() for f in entry.get(b"FLAGS", ())}
        return {
            "uid": uid, "folder": folder,
            "subject": _header(msg.get("Subject")),
            "from": _header_line_addresses(msg.get("From")),
            "to": _header_line_addresses(msg.get("To")),
            "cc": _header_line_addresses(msg.get("Cc")),
            "reply_to": _header_line_addresses(msg.get("Reply-To")),
            "date": _header(msg.get("Date")),
            "message_id": str(msg.get("Message-ID") or ""),
            "text": text, "html": html_clean, "remote_images": remoteimages,
            "attachments": _attachments(msg),
            "seen": "\\seen" in flags, "flagged": "\\flagged" in flags,
        }


def _attachment_sync(account: MailAccount, folder: str, uid: int, index: int) -> tuple[str, str, bytes]:
    with _imap(account) as client:
        client.select_folder(folder, readonly=True)
        raw = client.fetch([uid], ["RFC822"])
        entry = raw.get(uid)
        if not entry:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(entry[b"RFC822"], policy=email.policy.default)
        for i, part in enumerate(msg.walk()):
            if i != index:
                continue
            name = part.get_filename()
            if not name:
                break
            readable = _header(name)
            return (readable, _kind(part, readable), part.get_payload(decode=True) or b"")
    raise LookupError("Anhang nicht gefunden")


# ── Changing ────────────────────────────────────────────────────────────────

def _flag_sync(account: MailAccount, folder: str, uid: int, flag: str, an: bool) -> None:
    with _imap(account) as client:
        client.select_folder(folder)
        if an:
            client.add_flags([uid], [flag])
        else:
            client.remove_flags([uid], [flag])


def _move_sync(account: MailAccount, folder: str, uid: int, target: str) -> None:
    with _imap(account) as client:
        client.select_folder(folder)
        # MOVE where the server can do it; otherwise the old way (copy, delete, clean up).
        if client.has_capability("MOVE"):
            client.move([uid], target)
        else:
            client.copy([uid], target)
            client.add_flags([uid], [b"\\Deleted"])
            client.expunge()


# What may appear in an archive pattern. Deliberately spelled out: everyone reads `{year}`,
# `%Y` has to be looked up. The short forms next to it because they suggest themselves while
# typing once one knows them. The German names stay in the map: patterns saved before the
# rename still stand in accounts, and a pattern that suddenly no longer resolves would put
# mail into a folder literally called "Archive/{jahr}".
PLACEHOLDER = {
    "year": "%Y", "YYYY": "%Y", "jahr": "%Y",
    "year_short": "%y", "YY": "%y", "jahr_kurz": "%y",
    "month": "%m", "MM": "%m", "monat": "%m",
    "month_name": "%B", "monatsname": "%B",
    "day": "%d", "DD": "%d", "tag": "%d",
    "week": "%V", "kw": "%V",
}


def archive_target(account: MailAccount, message_date, sender: str = "",
                separator: str = "") -> str:
    """The folder this message belongs archived in.

    With `folder` that is always the same one. With `pattern` it grows out of the pattern —
    filled with the date **of the message**, so that an invoice from 2023 still lands in the
    year 2023 in 2026, and with the sender in case somebody wants to sort by that.

    The separator in the pattern is always `/`; it is replaced by the one the server uses (a
    dot with Courier, a slash with Dovecot). That way a pattern stays valid across a move, and
    nobody has to know how their IMAP server nests folders.
    """
    import datetime as _dt
    import re

    if account.archive_mode != "pattern" or not account.archive_pattern:
        return account.folder_archive

    when = message_date or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(when, str):
        try:
            from email.utils import parsedate_to_datetime
            when = parsedate_to_datetime(when)
        except Exception:  # noqa: BLE001 — an unreadable date is no reason not to archive
            when = _dt.datetime.now(_dt.timezone.utc)

    sender = (sender or "").strip()
    values = {name: when.strftime(pattern) for name, pattern in PLACEHOLDER.items()}
    quarter = f"Q{(when.month - 1) // 3 + 1}"
    domain = sender.rpartition("@")[2] if "@" in sender else sender
    values["quarter"] = values["quartal"] = quarter
    values["sender"] = values["absender"] = sender
    values["sender_domain"] = values["absender_domain"] = domain

    target = account.archive_pattern
    for name, value in values.items():
        target = target.replace("{" + name + "}", str(value))
    # What is left over is a typo in the pattern. It should stand out but not create a folder
    # with curly braces in its name.
    target = re.sub(r"\{[^}]*\}", "", target).strip("/ ")
    if separator and separator != "/":
        target = target.replace("/", separator)
    return target or account.folder_archive


def _archive_sync(account: MailAccount, folder: str, uid: int) -> str:
    """Archives and creates the target folder if it does not exist yet.

    Without the creation a yearly archive would be broken exactly once a year — on the first
    im Januar.
    """
    with _imap(account) as client:
        separator = "/"
        for _flags, raw_separator, _name in client.list_folders():
            if raw_separator:
                separator = raw_separator.decode() if isinstance(raw_separator, bytes) else raw_separator
                break
        client.select_folder(folder)
        entry = (client.fetch([uid], ["ENVELOPE", "INTERNALDATE"]) or {}).get(uid) or {}
        envelope = entry.get(b"ENVELOPE")
        sender = ""
        if envelope is not None and envelope.from_:
            first = envelope.from_[0]
            sender = f"{(first.mailbox or b'').decode()}@{(first.host or b'').decode()}"
        when = (envelope.date if envelope is not None and envelope.date
                else entry.get(b"INTERNALDATE"))

        target = archive_target(account, when, sender, separator)
        if not client.folder_exists(target):
            client.create_folder(target)
            try:
                client.subscribe_folder(target)
            except Exception:  # noqa: BLE001 (not every server knows subscriptions)
                log.debug("no subscription for %s", target)
        if client.has_capability("MOVE"):
            client.move([uid], target)
        else:
            client.copy([uid], target)
            client.add_flags([uid], [b"\\Deleted"])
            client.expunge()
        return target


def _unread_sync(account: MailAccount, folder: str = "INBOX") -> int:
    """How much unread mail lies in the inbox? — one question, one answer.

    Deliberately only the inbox and not the sum over all folders: "new mail" means what has
    come in, not the two hundred unread newsletters in the archive. And it
    is one call instead of forty.
    """
    with _imap(account) as client:
        state = client.folder_status(folder, ["UNSEEN"])
        return int(state.get(b"UNSEEN", 0))


def _all_read_sync(account: MailAccount, folder: str) -> int:
    """Sets \\Seen on everything unread and says how many there were.

    Touching only the unread ones is not stinginess but consideration: a server that confirms
    every flag individually otherwise has a lot to do with ten thousand messages — and the
    number in the answer is the one that interests the person anyway.
    """
    with _imap(account) as client:
        client.select_folder(folder)
        open_ones = client.search(["UNSEEN"])
        if open_ones:
            client.add_flags(open_ones, [b"\\Seen"])
        return len(open_ones)


def _folder_delete_sync(account: MailAccount, folder: str) -> None:
    with _imap(account) as client:
        # Switch away first: some servers refuse to delete the selected folder.
        client.select_folder("INBOX", readonly=True)
        client.delete_folder(folder)


# How many messages go into one command. A folder with ten thousand mails would otherwise
# become a single line the server has to read in one piece, and some servers cut it off.
BLOCK = 500


def _folder_empty_sync(account: MailAccount, folder: str, trash: str) -> dict:
    """Empty the folder: everything in it goes, the folder itself stays.

    Emptying means the same as deleting a single message: into the trash, so that a misgrab
    stays a movement. Only in the trash itself (or without one) is it final, otherwise
    "empty the trash" would be a move onto itself.
    """
    with _imap(account) as client:
        client.select_folder(folder)
        uids = client.search(["ALL"])
        if not uids:
            return {"deleted": 0, "target": ""}
        final = not trash or folder == trash
        move = client.has_capability("MOVE")
        for start in range(0, len(uids), BLOCK):
            block = uids[start:start + BLOCK]
            if final:
                client.add_flags(block, [b"\\Deleted"])
            elif move:
                client.move(block, trash)
            else:
                client.copy(block, trash)
                client.add_flags(block, [b"\\Deleted"])
        if final or not move:
            client.expunge()
        return {"deleted": len(uids), "target": "" if final else trash}


def _folder_create_sync(account: MailAccount, name: str) -> None:
    with _imap(account) as client:
        client.create_folder(name)
        # Unsubscribed the folder would exist but stay invisible in other mail programs:
        # they list what one is subscribed to, not what is there.
        try:
            client.subscribe_folder(name)
        except Exception:  # noqa: BLE001 — not every server knows subscriptions
            log.debug("no subscription for %s", name)


def _folder_rename_sync(account: MailAccount, folder: str, target: str) -> None:
    """Rename, which on IMAP is also the move: the name IS the path.

    Subfolders come along by themselves (the server renames the whole branch); the
    subscription does not, which is why it is set again on the new name.
    """
    with _imap(account) as client:
        client.select_folder("INBOX", readonly=True)
        client.rename_folder(folder, target)
        try:
            client.subscribe_folder(target)
        except Exception:  # noqa: BLE001
            log.debug("no subscription for %s", target)


def _final_sync(account: MailAccount, folder: str, uid: int) -> None:
    """Really gone. Meant for the trash only — everywhere else things are moved so that a
    misgrab stays a movement and not a loss."""
    with _imap(account) as client:
        client.select_folder(folder)
        client.add_flags([uid], [b"\\Deleted"])
        client.expunge()


def _draft_sync(account: MailAccount, raw: bytes) -> None:
    with _imap(account) as client:
        client.append(account.folder_drafts, raw, flags=[b"\\Draft"])


def _store_sync(account: MailAccount, folder: str, raw: bytes) -> None:
    with _imap(account) as client:
        client.append(folder, raw, flags=[b"\\Seen"])


# ── Bauen und Senden ────────────────────────────────────────────────────────

def build_message(identity: MailIdentity, fields: dict) -> EmailMessage:
    """Turn the form fields into a message — one place for draft and sending."""
    msg = EmailMessage()
    msg["From"] = formataddr((identity.display_name or "", identity.email))
    msg["To"] = ", ".join(fields.get("to") or [])
    if fields.get("cc"):
        msg["Cc"] = ", ".join(fields["cc"])
    if fields.get("bcc"):
        msg["Bcc"] = ", ".join(fields["bcc"])
    if identity.reply_to:
        msg["Reply-To"] = identity.reply_to
    msg["Subject"] = fields.get("subject") or ""
    if fields.get("in_reply_to"):
        msg["In-Reply-To"] = fields["in_reply_to"]
        msg["References"] = fields["in_reply_to"]
    body = fields.get("text") or ""
    if identity.signature:
        body = f"{body}\n\n-- \n{identity.signature}"
    msg.set_content(body)
    for attachment in fields.get("attachments") or []:
        primary, _, sub = (attachment.get("content_type") or "application/octet-stream").partition("/")
        msg.add_attachment(attachment["data"], maintype=primary, subtype=sub or "octet-stream",
                           filename=attachment.get("filename") or "anhang")
    return msg


def _send_sync(account: MailAccount, msg: EmailMessage) -> None:
    password = decrypt_secret(account.smtp_password_enc)
    context = ssl.create_default_context()
    if account.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=30,
                                  context=context)
    else:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
    with server:
        if account.smtp_security == "starttls":
            server.starttls(context=context)
        if account.smtp_user:
            server.login(account.smtp_user, password)
        server.send_message(msg)


def _clearer(error: Exception, account: MailAccount, smtp: bool) -> str:
    """Extend the library's message by the sentence that actually helps.

    `WRONG_VERSION_NUMBER` almost always means the same thing: knocked encrypted where the
    server greets in the clear first and upgrades afterwards (STARTTLS) — or the other way
    round. Whoever does not know that already otherwise reads a line from `_ssl.c` and is no
    step further.
    """
    text = str(error)
    if "WRONG_VERSION_NUMBER" in text or "record layer failure" in text:
        port = account.smtp_port if smtp else account.imap_port
        kind = account.smtp_security if smtp else ("ssl" if account.imap_ssl else "none")
        advice = ("port 587 speaks STARTTLS, port 465 is encrypted from the start"
               if smtp else
               "port 993 is encrypted from the start, port 143 upgrades on the way")
        return (f"{text} — port {port} and the encryption \"{kind}\" do not match. "
                f"{advice}.")
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return f"{text} — the certificate of the server cannot be checked."
    if "AUTHENTICATIONFAILED" in text.upper() or "authentication failed" in text.lower():
        return f"{text} — the user name or the password is wrong."
    return text


def _check_sync(account: MailAccount) -> dict:
    """A connection test that touches both paths — otherwise the typo in the SMTP password is
    noticed only when an answer does not go out."""
    result: dict = {"imap": "", "smtp": ""}
    try:
        with _imap(account) as client:
            client.select_folder("INBOX", readonly=True)
        result["imap"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["imap"] = _clearer(exc, account, smtp=False)[:300]
    if account.smtp_host:
        try:
            context = ssl.create_default_context()
            if account.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=20,
                                          context=context)
            else:
                server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=20)
            with server:
                if account.smtp_security == "starttls":
                    server.starttls(context=context)
                if account.smtp_user:
                    server.login(account.smtp_user, decrypt_secret(account.smtp_password_enc))
            result["smtp"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["smtp"] = _clearer(exc, account, smtp=True)[:300]
    return result


# ── Async wrappers (the API calls only these) ────────────────────────────────

async def folder(account: MailAccount, count: bool = False) -> list[dict]:
    return await asyncio.to_thread(_folder_sync, account, count)


async def listing(account: MailAccount, folder_name: str, search: str = "", offset: int = 0,
                limit: int = 50) -> dict:
    return await asyncio.to_thread(_listing_sync, account, folder_name, search, offset, limit)


async def message(account: MailAccount, folder_name: str, uid: int) -> dict:
    return await asyncio.to_thread(_message_sync, account, folder_name, uid)


async def attachment(account: MailAccount, folder_name: str, uid: int,
                 index: int) -> tuple[str, str, bytes]:
    return await asyncio.to_thread(_attachment_sync, account, folder_name, uid, index)


async def flag(account: MailAccount, folder_name: str, uid: int, name: str, an: bool) -> None:
    await asyncio.to_thread(_flag_sync, account, folder_name, uid, name, an)


async def move(account: MailAccount, folder_name: str, uid: int, target: str) -> None:
    await asyncio.to_thread(_move_sync, account, folder_name, uid, target)


async def unread(account: MailAccount, folder_name: str = "INBOX") -> int:
    return await asyncio.to_thread(_unread_sync, account, folder_name)


async def all_read(account: MailAccount, folder_name: str) -> int:
    return await asyncio.to_thread(_all_read_sync, account, folder_name)


async def folder_delete(account: MailAccount, folder_name: str) -> None:
    await asyncio.to_thread(_folder_delete_sync, account, folder_name)


async def folder_empty(account: MailAccount, folder_name: str, trash: str = "") -> dict:
    return await asyncio.to_thread(_folder_empty_sync, account, folder_name, trash)


async def folder_create(account: MailAccount, name: str) -> None:
    await asyncio.to_thread(_folder_create_sync, account, name)


async def folder_rename(account: MailAccount, folder_name: str, target: str) -> None:
    await asyncio.to_thread(_folder_rename_sync, account, folder_name, target)


async def archive(account: MailAccount, folder_name: str, uid: int) -> str:
    """Archives the message and returns where it ended up."""
    return await asyncio.to_thread(_archive_sync, account, folder_name, uid)


async def final_delete(account: MailAccount, folder_name: str, uid: int) -> None:
    await asyncio.to_thread(_final_sync, account, folder_name, uid)


async def draft_save(account: MailAccount, identity: MailIdentity,
                            fields: dict) -> None:
    msg = build_message(identity, fields)
    await asyncio.to_thread(_draft_sync, account, msg.as_bytes())


async def send(account: MailAccount, identity: MailIdentity, fields: dict) -> None:
    msg = build_message(identity, fields)
    await asyncio.to_thread(_send_sync, account, msg)
    # A sent mail missing from one's own mailbox is a lost one: afterwards the conversation
    # exists only on the other side.
    try:
        await asyncio.to_thread(_store_sync, account, account.folder_sent, msg.as_bytes())
    except Exception:  # noqa: BLE001
        log.exception("the copy in the folder %s failed (the mail went out)",
                      account.folder_sent)


async def check(account: MailAccount) -> dict:
    return await asyncio.to_thread(_check_sync, account)
