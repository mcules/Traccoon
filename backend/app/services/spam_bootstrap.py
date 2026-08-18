"""Lehrstoff aus den Postfächern selbst — Lernen ohne zu fragen.

Das Gedächtnis der Erkennung wächst bisher nur, wenn jemand eine Rückfrage beantwortet.
Am Anfang steht es damit leer da, obwohl die Antworten längst existieren: **jede Mail im
Spam-Ordner ist bestätigter Spam, jede im Posteingang und im Archiv bestätigte Post.** Ein
Mensch hat das entschieden, nur eben ohne Traccoon.

Zwei Anwendungen, ein Mechanismus:

* **Kaltstart** — einmal über Spam-Ordner und Posteingang/Archiv, damit die Erkennung von
  Anfang an weiß, mit wem dieser Mensch verkehrt.
* **Rückkopplung** — regelmäßig über den Spam-Ordner: was der Mensch selbst dorthin
  verschiebt (am Handy, in der Webmail), ist eine Entscheidung, aus der gelernt gehört.

Beides läuft über denselben Merkstand je Konto und Ordner (höchste verarbeitete UID), damit
kein Durchlauf doppelt zählt — doppelt gezählte Merkmale wären ein verzerrtes Gedächtnis.

**Nur die Absender-Identität.** Gelernt werden `from:` und `dom:` — sonst nichts. Die
technischen Signale (`sig:`) fehlen dem Nachlauf ohnehin (er liest nur Kopfzeilen-Auszüge),
und Betreff-Wörter wie der angeschriebene Alias sind aus dieser Quelle irreführend: ein
Postfach enthält tausende erwünschte Mails und eine Handvoll Müll, also wird jedes
Alltagswort zum Ham-Signal. Aus einer echten Entscheidung dürfen sie weiter gelernt
werden — dort stehen beide Klassen in einem Verhältnis, das etwas bedeutet.
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

# Merkstand je Konto/Ordner: bis zu welcher UID schon gelernt wurde.
STAND_KEY = "spam_lernstand"
# Wie viele Nachrichten ein Durchlauf je Ordner höchstens ansieht (imap-mcp deckelt bei 500).
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


# Nur die Identität des Absenders wird nachgelernt. Betreff-Wörter und der angeschriebene
# Alias sind aus einem Nachlauf heraus GIFT, und das ist teuer gelernt worden (2026-08-18):
# Ein Postfach enthält tausende erwünschte Mails und eine Handvoll Müll. Wer daraus
# Wortstatistik zieht, macht jedes Alltagswort zum Ham-Signal — „rechnung" stand danach
# 55× auf erwünscht, „domain" 12×. Eine Phishing-Mail mit dem Betreff „Ihre Domain-Rechnung
# wartet auf Bearbeitung" fiel dadurch von 0.55 auf 0.14 und wurde nicht mehr gefragt.
# Dasselbe gilt für `to:`: an einen Catch-all-Alias geht ohnehin alles, das Merkmal trennt
# nichts. Aus einer ECHTEN Entscheidung dürfen beide weiter gelernt werden — dort steht
# beides in einem Verhältnis, das etwas bedeutet.
_NACHLAUF_ARTEN = ("from:", "dom:")


def stabile_merkmale(treffer: dict, meine: frozenset[str]) -> list[str]:
    """Merkmale, die auch ohne vollständige Kopfzeilen UND ohne Klassen-Gleichgewicht tragen.

    Das ist die Absender-Identität und sonst nichts: Wer schreibt (Adresse, Domain) ist
    unabhängig davon aussagekräftig, wie viele Mails der Nachlauf gerade liest.
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

    „Neu" heißt: UID größer als der Merkstand. Beim ersten Lauf ist der Merkstand leer,
    dann zählen die jüngsten `limit` Nachrichten — für den Zweck (wer schreibt mir, was
    liegt im Müll) reicht der jüngere Bestand, und die Postfächer bleiben unangetastet.
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
    log.info("Angelernt: %s/%s → %d Nachrichten als %s (Stand jetzt %d)",
             account, folder, gelernt, "Spam" if ist_spam else "erwünscht", hoechste)
    return len(treffer), gelernt


async def konten(db: AsyncSession) -> list[dict]:
    """Konten des imap-mcp mit ihren Ordner-Rollen (Posteingang/Spam)."""
    try:
        antwort = await call_tool(IMAP_MCP_URL, "list_accounts", {})
    except McpError as exc:
        log.warning("Kontenliste nicht abrufbar: %s", exc)
        return []
    return list((ergebnis_json(antwort) or {}).get("accounts") or [])


async def ordner(db: AsyncSession, account: str) -> list[str]:
    try:
        antwort = await call_tool(IMAP_MCP_URL, "list_folders", {"account": account})
    except McpError as exc:
        log.warning("Ordnerliste %s nicht abrufbar: %s", account, exc)
        return []
    daten = ergebnis_json(antwort) or {}
    return [f["name"] for f in (daten.get("folders") or []) if not f.get("ignored")]


_SENT_NAMEN = ("sent", "gesendet", "sent items", "gesendete elemente", "sent messages",
               "gesendete objekte")


def sent_ordner(namen: list[str]) -> str | None:
    """Den Gesendet-Ordner aus einer Ordnerliste heraussuchen (Name je Server anders)."""
    for name in namen:
        letztes = name.split("/")[-1].strip().lower()
        if letztes in _SENT_NAMEN:
            return name
    return None


def empfaenger(treffer: dict, meine: frozenset[str]) -> list[tuple[str, str]]:
    """(Adresse, Anzeigename) aller Empfänger einer gesendeten Mail — ohne eigene Adressen."""
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
    """Wem ich geschrieben habe, der ist erwünscht. → Anzahl neuer Adressen.

    Das stärkste Ham-Signal, das ein Postfach hergibt, und es kostet keine Rückfrage: wer
    eine Antwort von mir bekommen hat, ist kein Fremder. Die Adressen landen als
    `AssistantContact(source_kind='sent')` in derselben Freispruch-Liste wie die
    Vault-Kontakte — der Vault-Abgleich lässt sie in Ruhe (er spiegelt nur seine eigenen).
    """
    from ..models.assistant import AssistantContact
    from sqlalchemy import select as _select

    meine = await meine_adressen(db)
    neu = 0
    for konto in await konten(db):
        alias = konto["alias"]
        ordnername = sent_ordner(await ordner(db, alias))
        if not ordnername:
            log.info("Konto %s: kein Gesendet-Ordner gefunden", alias)
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
                    continue   # aus dem Vault oder schon gemerkt — nicht überschreiben
                db.add(AssistantContact(
                    owner_user_id=owner_id, email=adresse,
                    domain=adresse.split("@", 1)[1], name=name[:300],
                    source_path=f"{alias}/{ordnername}"[:500], source_kind="sent"))
                neu += 1
        await db.commit()
        await set_setting(db, key, str(max(int(t.get("uid") or 0) for t in frisch)))
    if neu:
        log.info("Gesendet-Abgleich: %d neue erwünschte Adressen", neu)
    return neu


# Ordner, die kein Lehrstoff für „erwünscht" sind: Eigenes (dort bin ICH der Absender),
# Entwürfe und Notizen. Der Spam-Ordner läuft getrennt als Gegenstück.
_KEIN_HAM = ("sent", "gesendet", "drafts", "entwürfe", "entwuerfe", "notes", "notizen",
             "templates", "vorlagen", "outbox", "postausgang")
# Wie weit zurück Archive noch etwas über die heutige Post sagen. Ältere Jahrgänge tragen
# Adressen, die längst tot sind — sie blähten das Gedächtnis, ohne je wieder aufzutauchen.
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
    """Was der Mensch selbst in den Spam-Ordner geschoben hat, als Spam lernen. → gelernt."""
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
    """Einmal über alles, was schon entschieden ist: Spam-Ordner und Posteingang/Archiv.

    Archive zählen mit — dort steht die Post, die jemand aufgehoben hat, und genau das ist
    die stärkste Aussage „erwünscht", die ein Postfach zu bieten hat.
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
    log.info("Kaltstart: %d Nachrichten als Spam, %d als erwünscht gelernt",
             bilanz["spam"], bilanz["ham"])
    return bilanz
