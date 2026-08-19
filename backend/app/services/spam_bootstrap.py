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
from .mcp_client import McpError, call_tool, ergebnis_json
from .spam_rules import evaluate, features, ist_meine
from .spam_review import meine_adressen

log = logging.getLogger("traccoon.spam")

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")

# Mark per account and folder: up to which UID learning has already happened.
STAND_KEY = "spam_lernstand"
# How many messages one pass looks at per folder at most (imap-mcp caps at 500).
STAPEL = 500


def _stand_key(account: str, folder: str) -> str:
    return f"{STAND_KEY}:{account}:{folder}"


def _payload(treffer: dict) -> dict:
    """Suchtreffer → das Wenige, was für stabile Merkmale reicht."""
    return {
        "from": treffer.get("from") or [],
        "to": treffer.get("to") or [],
        "cc": treffer.get("cc") or [],
        "subject": treffer.get("subject") or "",
        "message_id": treffer.get("message_id") or "",
        "date": treffer.get("date") or "",
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
_NACHLAUF_ARTEN = ("from:", "dom:")


def stabile_merkmale(treffer: dict, meine: frozenset[str]) -> list[str]:
    """Features that carry even without complete headers AND without class balance.

    That is the sender identity and nothing else: who writes (address, domain) is meaningful
    independently of how many mails the follow-up is reading right now.
    """
    payload = _payload(treffer)
    res = evaluate(payload, meine_adressen=meine, body="")
    return [m for m in features(res, payload["subject"])
            if m.startswith(_NACHLAUF_ARTEN)]


async def _suchen(account: str, folder: str, limit: int) -> list[dict]:
    antwort = await call_tool(IMAP_MCP_URL, "search_emails", {
        "account": account, "folder": folder, "limit": limit})
    daten = ergebnis_json(antwort) or {}
    return list(daten.get("results") or [])


async def nachlernen(db: AsyncSession, owner_id: int | None, account: str, folder: str, *,
                     ist_spam: bool, limit: int = STAPEL) -> tuple[int, int]:
    """Neue Nachrichten eines Ordners lernen. → (gelesen, gelernt).

    "New" means: UID greater than the mark. On the first pass the mark is empty, and then the
    most recent `limit` messages count; for the purpose (who writes to me, what lies in the
    rubbish) the more recent stock is enough, and the mailboxes stay untouched.
    """
    key = _stand_key(account, folder)
    try:
        stand = int(await get_setting(db, key, "0") or 0)
    except ValueError:
        stand = 0

    try:
        treffer = await _suchen(account, folder, limit)
    except McpError as exc:
        log.warning("Anlernen %s/%s fehlgeschlagen: %s", account, folder, exc)
        return 0, 0

    neu = [t for t in treffer if int(t.get("uid") or 0) > stand]
    if not neu:
        return len(treffer), 0

    meine = await meine_adressen(db)
    gelernt = 0
    for t in neu:
        merkmale = stabile_merkmale(t, meine)
        if merkmale:
            await spam_learn.merkmale_zaehlen(db, owner_id, merkmale, ist_spam)
            gelernt += 1
    hoechste = max(int(t.get("uid") or 0) for t in neu)
    await db.commit()
    await set_setting(db, key, str(hoechste))
    log.info("Learned: %s/%s -> %d messages as %s (now at %d)",
             account, folder, gelernt, "Spam" if ist_spam else "erwünscht", hoechste)
    return len(treffer), gelernt


async def konten(db: AsyncSession) -> list[dict]:
    """Accounts of imap-mcp with their folder roles (inbox, spam)."""
    try:
        antwort = await call_tool(IMAP_MCP_URL, "list_accounts", {})
    except McpError as exc:
        log.warning("Account list not fetchable: %s", exc)
        return []
    return list((ergebnis_json(antwort) or {}).get("accounts") or [])


async def ordner(db: AsyncSession, account: str) -> list[str]:
    try:
        antwort = await call_tool(IMAP_MCP_URL, "list_folders", {"account": account})
    except McpError as exc:
        log.warning("Folder list %s not fetchable: %s", account, exc)
        return []
    daten = ergebnis_json(antwort) or {}
    return [f["name"] for f in (daten.get("folders") or []) if not f.get("ignored")]


_SENT_NAMEN = ("sent", "gesendet", "sent items", "gesendete elemente", "sent messages",
               "gesendete objekte")


def sent_ordner(namen: list[str]) -> str | None:
    """Find the sent folder in a folder list (the name differs per server)."""
    for name in namen:
        letztes = name.split("/")[-1].strip().lower()
        if letztes in _SENT_NAMEN:
            return name
    return None


def empfaenger(treffer: dict, meine: frozenset[str]) -> list[tuple[str, str]]:
    """(Address, display name) of all recipients of a sent mail, without one's own addresses."""
    out: list[tuple[str, str]] = []
    for feld in ("to", "cc"):
        for eintrag in treffer.get(feld) or []:
            adresse = str((eintrag or {}).get("addr") or "").strip().lower()
            if not adresse or "@" not in adresse or ist_meine(adresse, meine):
                continue
            out.append((adresse, str((eintrag or {}).get("name") or "").strip()))
    return out


async def antwort_kontakte(db: AsyncSession, owner_id: int | None,
                           limit: int = STAPEL) -> int:
    """Whoever I have written to is wanted. Returns the number of new addresses.

    The strongest ham signal a mailbox can produce, and it costs no question: whoever got an
    answer from me is not a stranger. The addresses land as
    `AssistantContact(source_kind='sent')` in the same acquittal list as the vault contacts,
    and the vault reconciliation leaves them alone (it only mirrors its own).
    """
    from ..models.assistant import AssistantContact
    from sqlalchemy import select as _select

    meine = await meine_adressen(db)
    neu = 0
    for konto in await konten(db):
        alias = konto["alias"]
        ordnername = sent_ordner(await ordner(db, alias))
        if not ordnername:
            log.info("Account %s: no sent folder found", alias)
            continue
        key = _stand_key(alias, ordnername)
        try:
            stand = int(await get_setting(db, key, "0") or 0)
        except ValueError:
            stand = 0
        try:
            treffer = await _suchen(alias, ordnername, limit)
        except McpError as exc:
            log.warning("Gesendet-Abgleich %s fehlgeschlagen: %s", alias, exc)
            continue
        frisch = [t for t in treffer if int(t.get("uid") or 0) > stand]
        if not frisch:
            continue
        for t in frisch:
            for adresse, name in empfaenger(t, meine):
                vorhanden = (await db.execute(_select(AssistantContact).where(
                    AssistantContact.owner_user_id == owner_id,
                    AssistantContact.email == adresse))).scalar_one_or_none()
                if vorhanden is not None:
                    continue   # from the vault or already noted: do not overwrite
                db.add(AssistantContact(
                    owner_user_id=owner_id, email=adresse,
                    domain=adresse.split("@", 1)[1], name=name[:300],
                    source_path=f"{alias}/{ordnername}"[:500], source_kind="sent"))
                neu += 1
        await db.commit()
        await set_setting(db, key, str(max(int(t.get("uid") or 0) for t in frisch)))
    if neu:
        log.info("Sent folder reconciliation: %d new wanted addresses", neu)
    return neu


# Folders that are no learning material for "wanted": one's own (I am the sender there),
# drafts and notes. The spam folder runs separately as the counterpart.
_KEIN_HAM = ("sent", "gesendet", "drafts", "entwürfe", "entwuerfe", "notes", "notizen",
             "templates", "vorlagen", "outbox", "postausgang")
# How far back archives still say something about today's post. Older years carry addresses
# that have long been dead; they would inflate the memory without ever turning up again.
JAHRE_ZURUECK = 3


def ist_ham_ordner(name: str, *, spam_folder: str | None, jetzt_jahr: int) -> bool:
    """Taugt dieser Ordner als Beleg für „erwünscht"?"""
    if spam_folder and name == spam_folder:
        return False
    erstes = name.split("/")[0].lower()
    if erstes in _KEIN_HAM:
        return False
    jahr = "".join(ch for ch in name.split("/")[-1] if ch.isdigit())
    if len(jahr) == 4 and int(jahr) < jetzt_jahr - JAHRE_ZURUECK:
        return False
    return True


async def spam_rueckkopplung(db: AsyncSession, owner_id: int | None) -> int:
    """Learn what the human moved into the spam folder themselves as spam. Returns the count."""
    gesamt = 0
    for konto in await konten(db):
        if not konto.get("spam_folder"):
            continue
        _, gelernt = await nachlernen(db, owner_id, konto["alias"], konto["spam_folder"],
                                      ist_spam=True)
        gesamt += gelernt
    return gesamt


async def kaltstart(db: AsyncSession, owner_id: int | None, *,
                    limit: int = STAPEL) -> dict[str, int]:
    """Once over everything that is already decided: spam folder and inbox or archive.

    Archives count as well: the post somebody kept stands there, and that is exactly the
    strongest "wanted" statement a mailbox has to offer.
    """
    import datetime as dt

    jetzt_jahr = dt.datetime.now(tz=dt.timezone.utc).year
    bilanz = {"spam": 0, "ham": 0}
    for konto in await konten(db):
        alias = konto["alias"]
        if konto.get("spam_folder"):
            _, n = await nachlernen(db, owner_id, alias, konto["spam_folder"],
                                    ist_spam=True, limit=limit)
            bilanz["spam"] += n
        ham_ordner = [konto.get("inbox_folder") or "INBOX"]
        ham_ordner += [f for f in await ordner(db, alias)
                       if ist_ham_ordner(f, spam_folder=konto.get("spam_folder"),
                                         jetzt_jahr=jetzt_jahr)]
        for f in dict.fromkeys(ham_ordner):
            _, n = await nachlernen(db, owner_id, alias, f, ist_spam=False, limit=limit)
            bilanz["ham"] += n
    log.info("Cold start: %d messages learned as spam, %d as wanted",
             bilanz["spam"], bilanz["ham"])
    return bilanz
