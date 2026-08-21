"""Learning material from the mailboxes themselves: learning without asking.

The memory of the detection has so far only grown when somebody answered a question. At the
beginning it therefore stands empty although the answers have long existed: **every mail in
the spam folder is confirmed spam, every one in the inbox and in the archive confirmed
post.** A human decided that, only without Traccoon.

Two applications, one mechanism:

* **Cold start**: once over the spam folder and the inbox or archive, so that the detection
  knows from the beginning who this person deals with.
* **Feedback**: regularly over the spam folder, because what the human moves there
  themselves (on the phone, in the webmail) is a decision worth learning from.

Both run over the same mark per account and folder (the highest processed UID) so that no
pass counts twice: doubly counted features would be a distorted memory.

**Only the sender identity.** What is learned are `from:` and `dom:`, nothing else. The
technical signals (`sig:`) are missing from the follow-up anyway (it only reads header
excerpts), and subject words like the addressed alias are misleading from this source: a
mailbox contains thousands of wanted mails and a handful of rubbish, so every everyday word
becomes a ham signal. From a real decision they may still be learned, because there both
classes stand in a ratio that means something.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession

from . import spam_learn
from .appsettings import get_setting, set_setting
from .mcp_client import McpError, call_tool, result_json
from .spam_rules import evaluate, features, is_my
from .spam_review import my_addresses

log = logging.getLogger("traccoon.spam")

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")

# Mark per account and folder: up to which UID learning has already happened.
STATE_KEY = "spam_lernstand"
# How many messages one pass looks at per folder at most (imap-mcp caps at 500).
BATCH = 500


def _state_key(account: str, folder: str) -> str:
    return f"{STATE_KEY}:{account}:{folder}"


def _payload(hits: dict) -> dict:
    """A search hit reduced to the little that is enough for stable features."""
    return {
        "from": hits.get("from") or [],
        "to": hits.get("to") or [],
        "cc": hits.get("cc") or [],
        "subject": hits.get("subject") or "",
        "message_id": hits.get("message_id") or "",
        "date": hits.get("date") or "",
        "headers": {},
        "links": [],
        "attachments": [],
        "body_text": "",
    }


# Only the identity of the sender is learned afterwards. Subject words and the addressed
# alias are POISON out of a follow-up, and that was learned expensively (2026-08-18): a
# mailbox contains thousands of wanted mails and a handful of rubbish. Whoever draws word
# statistics from that makes every everyday word a ham signal: "rechnung" afterwards stood
# 55 times on wanted, "domain" 12 times. A phishing mail whose subject combined those two
# words thereby fell from 0.55 to 0.14 and was no longer asked about. The same applies to `to:`: everything goes to a catch-all alias anyway, so the
# feature separates nothing. From a REAL decision both may still be learned, because there
# both stand in a ratio that means something.
_NACHLAUF_KINDS = ("from:", "dom:")


def stable_features(hits: dict, my: frozenset[str]) -> list[str]:
    """Features that carry even without complete headers AND without class balance.

    That is the sender identity and nothing else: who writes (address, domain) is meaningful
    independently of how many mails the follow-up is reading right now.
    """
    payload = _payload(hits)
    res = evaluate(payload, my_addresses=my, body="")
    return [m for m in features(res, payload["subject"])
            if m.startswith(_NACHLAUF_KINDS)]


async def _search(account: str, folder: str, limit: int) -> list[dict]:
    answer = await call_tool(IMAP_MCP_URL, "search_emails", {
        "account": account, "folder": folder, "limit": limit})
    data = result_json(answer) or {}
    return list(data.get("results") or [])


async def relearn(db: AsyncSession, owner_id: int | None, account: str, folder: str, *,
                     is_spam: bool, limit: int = BATCH) -> tuple[int, int]:
    """Neue Nachrichten eines Ordners lernen. → (gelesen, gelernt).

    "New" means: UID greater than the mark. On the first pass the mark is empty, and then the
    most recent `limit` messages count; for the purpose (who writes to me, what lies in the
    rubbish) the more recent stock is enough, and the mailboxes stay untouched.
    """
    key = _state_key(account, folder)
    try:
        state = int(await get_setting(db, key, "0") or 0)
    except ValueError:
        state = 0

    try:
        hits = await _search(account, folder, limit)
    except McpError as exc:
        log.warning("Learning %s/%s failed: %s", account, folder, exc)
        return 0, 0

    new = [t for t in hits if int(t.get("uid") or 0) > state]
    if not new:
        return len(hits), 0

    my = await my_addresses(db)
    learned = 0
    for t in new:
        features = stable_features(t, my)
        if features:
            await spam_learn.features_count(db, owner_id, features, is_spam)
            learned += 1
    highest = max(int(t.get("uid") or 0) for t in new)
    await db.commit()
    await set_setting(db, key, str(highest))
    log.info("Learned: %s/%s -> %d messages as %s (now at %d)",
             account, folder, learned, "spam" if is_spam else "wanted", highest)
    return len(hits), learned


async def accounts(db: AsyncSession) -> list[dict]:
    """Accounts of imap-mcp with their folder roles (inbox, spam)."""
    try:
        answer = await call_tool(IMAP_MCP_URL, "list_accounts", {})
    except McpError as exc:
        log.warning("Account list not fetchable: %s", exc)
        return []
    return list((result_json(answer) or {}).get("accounts") or [])


async def folder(db: AsyncSession, account: str) -> list[str]:
    try:
        answer = await call_tool(IMAP_MCP_URL, "list_folders", {"account": account})
    except McpError as exc:
        log.warning("Folder list %s not fetchable: %s", account, exc)
        return []
    data = result_json(answer) or {}
    return [f["name"] for f in (data.get("folders") or []) if not f.get("ignored")]


_SENT_NAMES = ("sent", "gesendet", "sent items", "gesendete elemente", "sent messages",
               "gesendete objekte")


def sent_folder(names: list[str]) -> str | None:
    """Find the sent folder in a folder list (the name differs per server)."""
    for name in names:
        last = name.split("/")[-1].strip().lower()
        if last in _SENT_NAMES:
            return name
    return None


def recipient(hits: dict, my: frozenset[str]) -> list[tuple[str, str]]:
    """(Address, display name) of all recipients of a sent mail, without one's own addresses."""
    out: list[tuple[str, str]] = []
    for field in ("to", "cc"):
        for entry in hits.get(field) or []:
            address = str((entry or {}).get("addr") or "").strip().lower()
            if not address or "@" not in address or is_my(address, my):
                continue
            out.append((address, str((entry or {}).get("name") or "").strip()))
    return out


async def answer_contacts(db: AsyncSession, owner_id: int | None,
                           limit: int = BATCH) -> int:
    """Whoever I have written to is wanted. Returns the number of new addresses.

    The strongest ham signal a mailbox can produce, and it costs no question: whoever got an
    answer from me is not a stranger. The addresses land as
    `AssistantContact(source_kind='sent')` in the same acquittal list as the vault contacts,
    and the vault reconciliation leaves them alone (it only mirrors its own).
    """
    from ..models.assistant import AssistantContact
    from sqlalchemy import select as _select

    my = await my_addresses(db)
    new = 0
    for account in await accounts(db):
        alias = account["alias"]
        foldername = sent_folder(await folder(db, alias))
        if not foldername:
            log.info("Account %s: no sent folder found", alias)
            continue
        key = _state_key(alias, foldername)
        try:
            state = int(await get_setting(db, key, "0") or 0)
        except ValueError:
            state = 0
        try:
            hits = await _search(alias, foldername, limit)
        except McpError as exc:
            log.warning("The sent folder reconciliation %s failed: %s", alias, exc)
            continue
        fresh = [t for t in hits if int(t.get("uid") or 0) > state]
        if not fresh:
            continue
        for t in fresh:
            for address, name in recipient(t, my):
                existing = (await db.execute(_select(AssistantContact).where(
                    AssistantContact.owner_user_id == owner_id,
                    AssistantContact.email == address))).scalar_one_or_none()
                if existing is not None:
                    continue   # from the vault or already noted: do not overwrite
                db.add(AssistantContact(
                    owner_user_id=owner_id, email=address,
                    domain=address.split("@", 1)[1], name=name[:300],
                    source_path=f"{alias}/{foldername}"[:500], source_kind="sent"))
                new += 1
        await db.commit()
        await set_setting(db, key, str(max(int(t.get("uid") or 0) for t in fresh)))
    if new:
        log.info("Sent folder reconciliation: %d new wanted addresses", new)
    return new


# Folders that are no learning material for "wanted": one's own (I am the sender there),
# drafts and notes. The spam folder runs separately as the counterpart. The German names stand
# beside the English ones because the folder names come from the mail server, not from us.
_NO_HAM = ("sent", "gesendet", "drafts", "entwürfe", "entwuerfe", "notes", "notizen",
             "templates", "vorlagen", "outbox", "postausgang")
# How far back archives still say something about today's post. Older years carry addresses
# that have long been dead; they would inflate the memory without ever turning up again.
YEARS_BACK = 3


def is_ham_folder(name: str, *, spam_folder: str | None, now_year: int) -> bool:
    """Does this folder work as evidence for something wanted?"""
    if spam_folder and name == spam_folder:
        return False
    first = name.split("/")[0].lower()
    if first in _NO_HAM:
        return False
    year = "".join(ch for ch in name.split("/")[-1] if ch.isdigit())
    if len(year) == 4 and int(year) < now_year - YEARS_BACK:
        return False
    return True


async def spam_feedback(db: AsyncSession, owner_id: int | None) -> int:
    """Learn what the human moved into the spam folder themselves as spam. Returns the count."""
    total = 0
    for account in await accounts(db):
        if not account.get("spam_folder"):
            continue
        _, learned = await relearn(db, owner_id, account["alias"], account["spam_folder"],
                                      is_spam=True)
        total += learned
    return total


async def coldstart(db: AsyncSession, owner_id: int | None, *,
                    limit: int = BATCH) -> dict[str, int]:
    """Once over everything that is already decided: spam folder and inbox or archive.

    Archives count as well: the post somebody kept stands there, and that is exactly the
    strongest "wanted" statement a mailbox has to offer.
    """
    import datetime as dt

    now_year = dt.datetime.now(tz=dt.timezone.utc).year
    balance = {"spam": 0, "ham": 0}
    for account in await accounts(db):
        alias = account["alias"]
        if account.get("spam_folder"):
            _, n = await relearn(db, owner_id, alias, account["spam_folder"],
                                    is_spam=True, limit=limit)
            balance["spam"] += n
        ham_folder = [account.get("inbox_folder") or "INBOX"]
        ham_folder += [f for f in await folder(db, alias)
                       if is_ham_folder(f, spam_folder=account.get("spam_folder"),
                                         now_year=now_year)]
        for f in dict.fromkeys(ham_folder):
            _, n = await relearn(db, owner_id, alias, f, is_spam=False, limit=limit)
            balance["ham"] += n
    log.info("Cold start: %d messages learned as spam, %d as wanted",
             balance["spam"], balance["ham"])
    return balance
