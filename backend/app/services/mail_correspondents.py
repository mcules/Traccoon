"""The address book of the recipient field, harvested from the mailbox itself.

Whoever writes a mail knows the person, not the address, and with a dozen mailboxes and a
few thousand mails behind them the address is nowhere to be looked up by eye. So the
mailbox is walked once, and afterwards only for what is new: every folder but junk, trash
and drafts, the envelope of every message, nothing else. The sent folder gives the people
one has written to, all other folders the people who have written. Both land in
`mail_correspondents` with a count each, because the count is the ranking: forty answers
to one address say more than forty newsletters from another.

Junk stays out on purpose. Its senders are exactly the addresses one never wants offered.

What has been harvested is remembered per folder as `UIDVALIDITY:UID` in the settings. A
changed UIDVALIDITY means the server renumbered, and then the folder is read from the start
again. The counts of it are not taken back for that, they only grow a little large once.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.mail import MailAccount, MailCorrespondent
from . import mailbox
from .appsettings import get_setting, set_setting

log = logging.getLogger("traccoon.mailbox")

# The envelopes of this many messages in one FETCH. Bigger answers only make the parser
# slower without saving a round trip that matters.
CHUNK = 500
# Folders whose messages say nothing about whom one writes with: the sender there is oneself
# (drafts), or somebody nobody wants offered (junk), or it is on the way out (trash).
_OUT = ("junk", "trash", "drafts")


def _state_key(account_id: int, folder: str) -> str:
    return f"mail_correspondents:{account_id}:{folder}"


def _address(part) -> tuple[str, str] | None:
    """An envelope address to (email, name), or None if the server sent none."""
    if part is None or not part.mailbox or not part.host:
        return None
    address = f"{part.mailbox.decode(errors='replace')}@{part.host.decode(errors='replace')}"
    name = mailbox._header(part.name.decode(errors="replace") if part.name else "")
    return address.lower(), name.strip().strip("'\"").strip()


def _harvest_sync(account: MailAccount, state: dict[str, str]
                  ) -> tuple[dict[str, dict], dict[str, str]]:
    """Walk the folders and count. Returns (per address, the new state per folder).

    Per address: name, sent, received, last_seen. The name is the latest one the server had
    for it: people change how they sign, and the newest spelling is the one to offer.
    """
    found: dict[str, dict] = {}
    fresh: dict[str, str] = {}

    def note(pair, kind: str, when) -> None:
        if pair is None:
            return
        address, name = pair
        row = found.setdefault(address, {"name": "", "sent": 0, "received": 0, "last_seen": None})
        row[kind] += 1
        if when is not None and (row["last_seen"] is None or when > row["last_seen"]):
            row["last_seen"] = when
            if name:
                row["name"] = name
        elif name and not row["name"]:
            row["name"] = name

    with mailbox._imap(account) as client:
        for entry in _folder_list(client, account):
            name = entry["name"]
            try:
                status = client.select_folder(name, readonly=True)
            except Exception:  # noqa: BLE001, a folder that refuses is not the whole harvest
                log.debug("no harvest in %s", name)
                continue
            validity = int(status.get(b"UIDVALIDITY", 0))
            known_validity, _, known_uid = (state.get(_state_key(account.id, name)) or "0:0"
                                            ).partition(":")
            last = int(known_uid or 0) if int(known_validity or 0) == validity else 0
            # `UID n:*` also answers with the last message when n is past the end, which is
            # why the answer is filtered and not trusted.
            uids = [u for u in client.search(["UID", f"{last + 1}:*"]) if u > last]
            top = last
            for start in range(0, len(uids), CHUNK):
                chunk = uids[start:start + CHUNK]
                raw = client.fetch(chunk, ["ENVELOPE", "INTERNALDATE"])
                for uid in chunk:
                    got = raw.get(uid) or {}
                    envelope = got.get(b"ENVELOPE")
                    when = got.get(b"INTERNALDATE")
                    if envelope is not None:
                        if entry["sent"]:
                            for part in (envelope.to or ()) + (envelope.cc or ()):
                                note(_address(part), "sent", when)
                        else:
                            for part in (envelope.from_ or ()):
                                note(_address(part), "received", when)
                    top = max(top, uid)
            fresh[_state_key(account.id, name)] = f"{validity}:{top}"
    return found, fresh


def _folder_list(client, account: MailAccount) -> list[dict]:
    """The folders worth harvesting: name and whether it is the sent folder.

    Junk, trash and drafts are left out together with everything underneath them; a folder
    `Trash/2024` is trash as well.
    """
    delimiter_of: dict[str, str] = {}
    kind_of: dict[str, str] = {}
    names: list[str] = []
    mapping = {account.folder_sent: "sent", account.folder_drafts: "drafts",
               account.folder_trash: "trash", account.folder_junk: "junk"}
    for flags, separator, name in client.list_folders():
        marker = {f.decode().lower() for f in flags}
        if "\\noselect" in marker:
            continue
        special = next((k.lstrip("\\") for k in marker
                        if k in ("\\sent", "\\drafts", "\\trash", "\\junk")), "")
        kind_of[name] = special or mapping.get(name, "")
        delimiter_of[name] = separator.decode() if isinstance(separator, bytes) else (separator or "/")
        names.append(name)

    def kind(name: str) -> str:
        """The kind of a folder, inherited from the nearest marked parent."""
        parts = name.split(delimiter_of[name])
        for depth in range(len(parts), 0, -1):
            found = kind_of.get(delimiter_of[name].join(parts[:depth]), "")
            if found:
                return found
        return ""

    out = []
    for name in names:
        k = kind(name)
        if k in _OUT:
            continue
        out.append({"name": name, "sent": k == "sent"})
    return out


async def harvest(db: AsyncSession, account: MailAccount) -> int:
    """What is new in this mailbox, counted in. Returns the number of addresses touched."""
    from ..models.ops import AppSetting

    prefix = f"mail_correspondents:{account.id}:"
    rows = (await db.execute(select(AppSetting).where(
        AppSetting.key.like(prefix.replace("_", r"\_") + "%", escape="\\")))).scalars().all()
    state = {r.key: r.value for r in rows}
    found, fresh = await asyncio.to_thread(_harvest_sync, account, state)

    for address, got in found.items():
        row = (await db.execute(select(MailCorrespondent).where(
            MailCorrespondent.account_id == account.id,
            MailCorrespondent.email == address))).scalar_one_or_none()
        if row is None:
            row = MailCorrespondent(account_id=account.id, email=address[:320], name="",
                                    sent=0, received=0)
            db.add(row)
        row.sent += got["sent"]
        row.received += got["received"]
        when = got["last_seen"]
        if when is not None and (row.last_seen is None or when > row.last_seen):
            row.last_seen = when
            if got["name"]:
                row.name = got["name"][:300]
        elif got["name"] and not row.name:
            row.name = got["name"][:300]
    for key, value in fresh.items():
        if state.get(key) != value:
            await set_setting(db, key, value)
    await db.commit()
    if found:
        log.info("mailbox %s: %d correspondents harvested", account.name, len(found))
    return len(found)


async def harvest_all(db: AsyncSession) -> None:
    """Every enabled mailbox, one after the other. A failing one does not stop the rest."""
    ids = (await db.execute(select(MailAccount.id).where(
        MailAccount.enabled.is_(True)).order_by(MailAccount.id))).scalars().all()
    for account_id in ids:
        # Fetched one at a time: a rollback after a failure expires what the session holds,
        # and the next round would trip over the expired account of the failed one.
        try:
            account = await db.get(MailAccount, account_id)
            if account is not None:
                await harvest(db, account)
        except Exception:  # noqa: BLE001
            log.exception("correspondent harvest of mailbox %s failed", account_id)
            await db.rollback()


def _words(q: str) -> list[str]:
    return [w for w in q.strip().lower().split() if w]


async def suggest(db: AsyncSession, owner_id: int, q: str, limit: int = 12) -> list[dict]:
    """Addresses matching `q`, best first.

    Two sources: the mailboxes of the person (`mail_correspondents`, across all their
    accounts) and the contacts of the vault (`assistant_contacts`). Every word of the query
    has to be found in name or address; "anna darc" finds the one Anna at the DARC.

    The order is a score, not a source: three answers of mine outrank a vault contact I never
    wrote to, and a vault contact outranks a sender of two mails. The vault's name wins over
    the mailbox's when both have one: it is the name the person chose to file them under.
    """
    from ..models.assistant import AssistantContact

    words = _words(q)
    if not words:
        return []

    def matches(email: str, name: str) -> bool:
        text = f"{name} {email}".lower()
        return all(w in text for w in words)

    def pattern(w: str) -> str:
        return "%" + w.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") + "%"

    # One coarse SQL filter on the first word keeps the rest in Python: the tables are a few
    # thousand rows, and case-insensitive matching over two columns is a page of SQL for no
    # gain.
    first = pattern(words[0])
    mine = select(MailAccount.id).where(MailAccount.owner_user_id == owner_id)
    correspondents = (await db.execute(select(MailCorrespondent).where(
        MailCorrespondent.account_id.in_(mine),
        (MailCorrespondent.email.ilike(first, escape="\\"))
        | (MailCorrespondent.name.ilike(first, escape="\\"))
    ))).scalars().all()
    contacts = (await db.execute(select(AssistantContact).where(
        AssistantContact.owner_user_id == owner_id,
        AssistantContact.email != "",
        (AssistantContact.email.ilike(first, escape="\\"))
        | (AssistantContact.name.ilike(first, escape="\\"))
    ))).scalars().all()

    merged: dict[str, dict] = {}
    for c in correspondents:
        if not matches(c.email, c.name):
            continue
        row = merged.setdefault(c.email, {"email": c.email, "name": "", "score": 0.0,
                                          "last_seen": None})
        row["score"] += 10 * c.sent + 2 * c.received
        if c.last_seen and (row["last_seen"] is None or c.last_seen > row["last_seen"]):
            row["last_seen"] = c.last_seen
            if c.name:
                row["name"] = c.name
        elif c.name and not row["name"]:
            row["name"] = c.name
    for k in contacts:
        email = k.email.lower()
        if not matches(email, k.name):
            continue
        row = merged.setdefault(email, {"email": email, "name": "", "score": 0.0,
                                        "last_seen": None})
        row["score"] += {"frontmatter": 8, "sent": 6}.get(k.source_kind, 2)
        if k.name and k.source_kind == "frontmatter":
            row["name"] = k.name
        elif k.name and not row["name"]:
            row["name"] = k.name

    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows = sorted(merged.values(),
                  key=lambda r: (-r["score"], -(r["last_seen"] or floor).timestamp(), r["email"]))
    return [{"email": r["email"], "name": r["name"]} for r in rows[:limit]]
