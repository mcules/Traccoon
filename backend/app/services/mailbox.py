"""Zugriff auf ein Postfach: Ordner, Nachrichten, Anhänge, Entwürfe, Senden.

Warum synchron und im Threadpool: `imapclient` ist eine bewährte, blockierende Bibliothek —
dieselbe, die im `imap-mcp` seit Monaten läuft. Ein asynchroner IMAP-Nachbau wäre eine zweite
Baustelle ohne Gewinn; die Wartezeit steckt ohnehin im Netz, nicht in der CPU. Jeder Aufruf
öffnet seine Verbindung und schließt sie wieder: Postfächer werden hier stoßweise benutzt
(eine Liste, eine Nachricht), und eine offene Verbindung je Person wäre ein Zustand, den
niemand aufräumt.

Was hier NICHT passiert: entscheiden. Diese Schicht liest und schreibt, sie beurteilt nichts.
Was mit einer Mail geschehen soll, steht in einem Ablauf (`mail_action`) oder in der Hand des
Menschen vor dem Bildschirm.
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

# Wieviel Text einer Nachricht in die Oberfläche geht. Eine Mail mit einem eingebetteten
# Bildarchiv im HTML-Teil hat schon Megabyte an Base64 — das braucht niemand zu lesen.
MAX_TEXT = 200_000


def _verbinden(account: MailAccount) -> IMAPClient:
    client = IMAPClient(account.imap_host, port=account.imap_port, ssl=account.imap_ssl,
                        timeout=30)
    client.login(account.imap_user, decrypt_secret(account.imap_password_enc))
    return client


# ── Offene Verbindungen ──────────────────────────────────────────────────────
# Anmelden kostet einen TLS-Handschlag und ein LOGIN: gemessen 266 ms, und zwar bei JEDEM
# Aufruf. Beim Aufbau der Seite sind das drei, vier Anmeldungen hintereinander für Fragen,
# die zusammen keine 300 ms Arbeit sind.
#
# Also bleibt die Verbindung liegen und wird wiederverwendet. IMAP ist zustandsbehaftet (ein
# SELECT gilt für die Verbindung, nicht für den Aufruf), deshalb leiht sich jeder Aufruf
# genau eine — parallele Nutzung derselben Verbindung würde die Ordner durcheinanderbringen.
POOL_MAX = int(os.getenv("MAIL_POOL", "3"))       # je Konto
LEERLAUF_S = 240.0                                 # danach ist sie vermutlich tot

_pool: dict[int, list[tuple[IMAPClient, float]]] = {}
_pool_lock = threading.Lock()


def _aus_pool(account: MailAccount) -> IMAPClient | None:
    """Eine liegende Verbindung, sofern sie noch lebt."""
    with _pool_lock:
        vorrat = _pool.get(account.id) or []
        while vorrat:
            client, latest = vorrat.pop()
            if time.monotonic() - latest > LEERLAUF_S:
                _schliessen(client)
                continue
            _pool[account.id] = vorrat
            return client
        _pool[account.id] = []
    return None


def _zurueck(account_id: int, client: IMAPClient) -> None:
    with _pool_lock:
        vorrat = _pool.setdefault(account_id, [])
        if len(vorrat) >= POOL_MAX:
            _schliessen(client)
            return
        vorrat.append((client, time.monotonic()))


def _schliessen(client: IMAPClient) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — eine tote Verbindung zu schließen darf nichts kosten
        pass


def pool_leeren(account_id: int | None = None) -> None:
    """Alle liegenden Verbindungen wegwerfen — nach geänderten Zugangsdaten."""
    with _pool_lock:
        konten = [account_id] if account_id is not None else list(_pool)
        for k in konten:
            for client, _ in _pool.pop(k, []):
                _schliessen(client)


@contextmanager
def _imap(account: MailAccount):
    """Eine Verbindung ausleihen. Sie geht zurück in den Vorrat, wenn nichts schiefging.

    Eine gebrauchte Verbindung kann still gestorben sein (der Server trennt nach ein paar
    Minuten Ruhe). Deshalb wird sie mit NOOP angetippt, bevor sie jemand bekommt — ein
    Roundtrip statt eines Fehlers mitten in einer Antwort.
    """
    client = _aus_pool(account)
    if client is not None:
        try:
            client.noop()
        except Exception:  # noqa: BLE001 — dann eben eine frische
            _schliessen(client)
            client = None
    if client is None:
        client = _verbinden(account)
    try:
        yield client
    except Exception:
        # Nach einem Fehler ist der Zustand der Verbindung unklar (halb gelesene Antwort,
        # abgebrochener FETCH). Eine solche zurückzulegen hieße, den Fehler an den nächsten
        # Aufruf weiterzureichen.
        _schliessen(client)
        raise
    else:
        _zurueck(account.id, client)


def _text_aus(msg: email.message.Message) -> tuple[str, str]:
    """(Text, HTML) einer Nachricht — beides, soweit vorhanden."""
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():          # Anhänge gehören nicht in den Fließtext
                continue
            inhalt = part.get_content_type()
            try:
                roh = part.get_payload(decode=True) or b""
                stueck = roh.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — eine kaputte Kodierung darf die Mail nicht verschlucken
                continue
            if inhalt == "text/plain" and not text:
                text = stueck
            elif inhalt == "text/html" and not html:
                html = stueck
    else:
        roh = msg.get_payload(decode=True) or b""
        stueck = roh.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html = stueck
        else:
            text = stueck
    if html and not text:
        import html2text

        wandler = html2text.HTML2Text()
        wandler.ignore_images = True
        wandler.body_width = 0
        text = wandler.handle(html)
    return text[:MAX_TEXT], html[:MAX_TEXT]


# Was aus einer fremden Mail überleben darf. Alles andere fliegt: Skripte sowieso, aber auch
# Formulare (eine Anmeldemaske im Postfach ist genau der Trick, um den es bei Phishing geht),
# eingebettete Rahmen und Objekte.
_ERLAUBTE_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "div",
    "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "li", "ol", "p", "pre", "s", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
_ERLAUBTE_ATTRIBUTE = {
    "*": {"style", "title", "align", "width", "height", "colspan", "rowspan", "dir"},
    # `rel` fehlt hier absichtlich: nh3 setzt es selbst (`link_rel`) und lehnt es als
    # erlaubtes Attribut ab, damit niemand die Absicherung von Links wieder aufweicht.
    "a": {"href", "target", "title"},
    "img": {"src", "alt", "title", "width", "height"},
}


def saeubern(html: str) -> tuple[str, bool]:
    """Gibt (gesäubertes HTML, ob es Fernbilder gibt) zurück.

    Fernbilder werden nicht entfernt, sondern **umgehängt**: die Adresse wandert nach
    `data-fern`, `src` verschwindet. So bleibt die Nachricht vollständig, lädt aber nichts
    nach — ein geladenes Bild ist eine Rückmeldung an den Absender, dass gelesen wurde, und
    diese Entscheidung gehört dem Menschen und nicht dem Postfach.
    """
    import re

    import nh3

    sauber = nh3.clean(html or "", tags=_ERLAUBTE_TAGS, attributes=_ERLAUBTE_ATTRIBUTE,
                       url_schemes={"http", "https", "mailto", "cid", "data"},
                       link_rel="noopener noreferrer nofollow")
    fern = False

    def um(hits):
        nonlocal fern
        adresse = hits.group(2)
        if adresse.startswith("data:"):
            return hits.group(0)
        fern = True
        return f'{hits.group(1)}data-fern="{adresse}"'

    sauber = re.sub(r'(<img\b[^>]*?)src="([^"]*)"', um, sauber, flags=re.I)
    return sauber, fern


def _attachments(msg: email.message.Message) -> list[dict]:
    """Index der Anhänge — Name, Typ, Größe. Der Inhalt wird erst auf Abruf geholt: eine
    Liste von zwanzig Mails soll keine zwanzig PDF durchs Netz ziehen."""
    out = []
    for i, part in enumerate(msg.walk()):
        name = part.get_filename()
        if not name:
            continue
        roh = part.get_payload(decode=True) or b""
        out.append({"index": i, "filename": _header(name),
                    "content_type": part.get_content_type(), "size": len(roh)})
    return out


def _header(roh) -> str:
    """Kopfzeile lesbar machen (=?utf-8?B?…?= und Konsorten)."""
    from email.header import decode_header, make_header

    if roh is None:
        return ""
    try:
        return str(make_header(decode_header(str(roh))))
    except Exception:  # noqa: BLE001
        return str(roh)


def _header_line_adressen(roh) -> list[dict]:
    out = []
    for part in str(roh or "").split(","):
        name, addr = parseaddr(part.strip())
        if addr:
            out.append({"name": _header(name), "addr": addr})
    return out


# ── Lesen ───────────────────────────────────────────────────────────────────

# Reihenfolge der Sonderordner, wie sie jedes Mail-Programm zeigt: was man täglich braucht,
# steht oben, der Rest alphabetisch darunter.
_SONDER_SERIES = ["inbox", "drafts", "sent", "junk", "trash", "archive"]


def baum_sortieren(entries: list[dict]) -> list[dict]:
    """Ordner in Anzeigereihenfolge: jedes Kind direkt unter seinem Elternteil.

    Nach dem vollen Namen zu sortieren sieht aus wie ein Baum und ist keiner: `Archives`
    steht alphabetisch vor `INBOX.Aliexpress`, also rutschten die Unterordner des
    Posteingangs unter das Archiv — mit Einrückung, was den Eindruck perfekt macht. Also
    wirklich absteigen: je Ebene sortieren, dann die Kinder dahinter.

    `level` kommt dabei aus der Tiefe im Baum und nicht mehr aus der Zahl der Trennzeichen
    im Namen: ein Ordner, dessen Elternteil der Server gar nicht auflistet, ist eine Wurzel
    und darf nicht eingerückt ins Leere zeigen.
    """
    nach_name = {e["name"]: e for e in entries}
    kinder: dict[str, list[dict]] = {}
    wurzeln: list[dict] = []
    for e in entries:
        eltern = e.get("parent") or ""
        if eltern and eltern in nach_name:
            kinder.setdefault(eltern, []).append(e)
        else:
            wurzeln.append(e)

    def key(e: dict) -> tuple:
        rang = _SONDER_SERIES.index(e["special"]) if e["special"] in _SONDER_SERIES else 99
        return (rang, (e.get("display") or e["name"]).lower())

    out: list[dict] = []

    def absteigen(node: list[dict], ebene: int) -> None:
        for e in sorted(node, key=key):
            e["level"] = ebene
            out.append(e)
            absteigen(kinder.get(e["name"], []), ebene + 1)

    absteigen(wurzeln, 0)
    return out


def _folder_sync(account: MailAccount, count: bool) -> list[dict]:
    """Die Ordner als Baum: Name, Anzeigename, Ebene, Elternordner — und auf Wunsch die Zahl
    der ungelesenen Nachrichten.

    Der Trenner kommt vom Server (Punkt bei Courier, Schrägstrich bei Dovecot); ihn zu raten
    hieße, bei jedem zweiten Anbieter eine flache Liste mit Punkten im Namen zu zeigen statt
    einer Struktur.

    Gezählt wird nur auf Abruf: ein STATUS je Ordner ist bei vierzig Ordnern vierzig Fragen
    ans Postfach, und beim Blättern durch eine Liste braucht das niemand.
    """
    with _imap(account) as client:
        roh = []
        for flags, trenner, name in client.list_folders():
            kennzeichen = {f.decode().lower() for f in flags}
            if "\\noselect" in kennzeichen:
                continue
            besonders = next((k.lstrip("\\") for k in kennzeichen
                              if k in ("\\sent", "\\drafts", "\\trash", "\\junk",
                                       "\\archive")), "")
            if not besonders:
                # Nicht jeder Server kennzeichnet seine Sonderordner (RFC 6154). Dann
                # entscheidet, was am Konto eingetragen ist — die Person weiß es besser als
                # eine Namensliste im Code.
                mapping = {account.folder_sent: "sent", account.folder_drafts: "drafts",
                             account.folder_trash: "trash", account.folder_junk: "junk",
                             account.folder_archive: "archive"}
                besonders = mapping.get(name, "")
            if name.upper() == "INBOX":
                besonders = "inbox"
            trennzeichen = trenner.decode() if isinstance(trenner, bytes) else (trenner or "/")
            parts = name.split(trennzeichen) if trennzeichen else [name]
            roh.append({
                "name": name,
                "display": parts[-1] if parts else name,
                "level": max(0, len(parts) - 1),
                "parent": trennzeichen.join(parts[:-1]) if len(parts) > 1 else "",
                "delimiter": trennzeichen,
                "special": besonders,
                "unseen": 0, "total": 0,
            })

        if count:
            for entry in roh:
                try:
                    state = client.folder_status(entry["name"], ["UNSEEN", "MESSAGES"])
                    entry["unseen"] = int(state.get(b"UNSEEN", 0))
                    entry["total"] = int(state.get(b"MESSAGES", 0))
                except Exception:  # noqa: BLE001 — ein Ordner ohne Auskunft ist kein Fehler
                    log.debug("Kein Status für %s", entry["name"])

        return baum_sortieren(roh)


def _hat_attachment(struktur) -> bool:
    """Hat diese Nachricht einen Anhang? — beantwortet aus der BODYSTRUCTURE.

    Der ganze Sinn der Frage ist, sie zu beantworten, OHNE die Mail zu laden: eine Liste von
    fünfzig Nachrichten soll keine fünfzig Anhänge durchs Netz ziehen. Die Struktur ist
    verschachtelt und je nach Server unterschiedlich tief, deshalb wird sie durchsucht statt
    an festen Stellen abgegriffen.

    Nur `attachment` zählt. Ein eingebettetes Logo im HTML (`inline`) ist kein Anhang, und
    eine Büroklammer an jeder Werbemail wäre keine Auskunft mehr.
    """
    def durchsuchen(part) -> bool:
        if not isinstance(part, (list, tuple)):
            return False
        for element in part:
            if isinstance(element, bytes) and element.lower() == b"attachment":
                return True
            if durchsuchen(element):
                return True
        return False

    return durchsuchen(struktur)


def _listing_sync(account: MailAccount, folder: str, suche: str, offset: int,
                limit: int) -> dict:
    with _imap(account) as client:
        state = client.select_folder(folder, readonly=True)
        gesamt = state.get(b"EXISTS", 0)
        kriterium = ["TEXT", suche] if suche else ["ALL"]
        uids = client.search(kriterium)
        uids = list(reversed(uids))          # neueste zuerst, wie in jedem Postfach
        ausschnitt = uids[offset:offset + limit]
        if not ausschnitt:
            return {"total": len(uids), "exists": gesamt, "messages": []}
        roh = client.fetch(ausschnitt, ["ENVELOPE", "FLAGS", "RFC822.SIZE", "INTERNALDATE",
                                        "BODYSTRUCTURE"])
        messages = []
        for uid in ausschnitt:
            entry = roh.get(uid) or {}
            envelope = entry.get(b"ENVELOPE")
            flags = {f.decode().lower() for f in entry.get(b"FLAGS", ())}
            absender = ""
            if envelope is not None and envelope.from_:
                erster = envelope.from_[0]
                name = _header(erster.name.decode() if erster.name else "")
                adresse = f"{(erster.mailbox or b'').decode()}@{(erster.host or b'').decode()}"
                absender = formataddr((name, adresse))
            messages.append({
                "uid": uid,
                "subject": _header(envelope.subject.decode("utf-8", "replace")
                                 if envelope is not None and envelope.subject else ""),
                "from": absender,
                "date": (entry.get(b"INTERNALDATE") or dt.datetime.now(dt.timezone.utc)).isoformat(),
                "size": entry.get(b"RFC822.SIZE", 0),
                "seen": "\\seen" in flags,
                "flagged": "\\flagged" in flags,
                "answered": "\\answered" in flags,
                "has_attachment": _hat_attachment(entry.get(b"BODYSTRUCTURE")),
            })
        return {"total": len(uids), "exists": gesamt, "messages": messages}


def _message_sync(account: MailAccount, folder: str, uid: int) -> dict:
    with _imap(account) as client:
        client.select_folder(folder)
        roh = client.fetch([uid], ["RFC822", "FLAGS"])
        entry = roh.get(uid)
        if not entry:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(entry[b"RFC822"], policy=email.policy.default)
        text, html = _text_aus(msg)
        html_sauber, fernbilder = saeubern(html) if html else ("", False)
        flags = {f.decode().lower() for f in entry.get(b"FLAGS", ())}
        return {
            "uid": uid, "folder": folder,
            "subject": _header(msg.get("Subject")),
            "from": _header_line_adressen(msg.get("From")),
            "to": _header_line_adressen(msg.get("To")),
            "cc": _header_line_adressen(msg.get("Cc")),
            "reply_to": _header_line_adressen(msg.get("Reply-To")),
            "date": _header(msg.get("Date")),
            "message_id": str(msg.get("Message-ID") or ""),
            "text": text, "html": html_sauber, "remote_images": fernbilder,
            "attachments": _attachments(msg),
            "seen": "\\seen" in flags, "flagged": "\\flagged" in flags,
        }


def _attachment_sync(account: MailAccount, folder: str, uid: int, index: int) -> tuple[str, str, bytes]:
    with _imap(account) as client:
        client.select_folder(folder, readonly=True)
        roh = client.fetch([uid], ["RFC822"])
        entry = roh.get(uid)
        if not entry:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(entry[b"RFC822"], policy=email.policy.default)
        for i, part in enumerate(msg.walk()):
            if i != index:
                continue
            name = part.get_filename()
            if not name:
                break
            return (_header(name), part.get_content_type(), part.get_payload(decode=True) or b"")
    raise LookupError("Anhang nicht gefunden")


# ── Ändern ──────────────────────────────────────────────────────────────────

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
        # MOVE, wo der Server es kann; sonst der alte Weg (kopieren, löschen, aufräumen).
        if client.has_capability("MOVE"):
            client.move([uid], target)
        else:
            client.copy([uid], target)
            client.add_flags([uid], [b"\\Deleted"])
            client.expunge()


# Was in einem Archiv-Muster stehen darf. Bewusst deutsch und ausgeschrieben: `{jahr}` liest
# jeder, `%Y` muss man nachschlagen. Die kurzen Formen daneben, weil sie sich beim Tippen
# aufdrängen, wenn man sie einmal kennt.
PLATZHALTER = {
    "jahr": "%Y", "YYYY": "%Y",
    "jahr_kurz": "%y", "YY": "%y",
    "monat": "%m", "MM": "%m",
    "monatsname": "%B",
    "tag": "%d", "DD": "%d",
    "kw": "%V",
}


def archiv_target(account: MailAccount, message_datum, absender: str = "",
                trenner: str = "") -> str:
    """Der Ordner, in den diese Nachricht archiviert gehört.

    Bei `folder` ist das immer derselbe. Bei `pattern` entsteht er aus dem Muster — gefüllt
    mit dem Datum **der Nachricht**, damit eine Rechnung von 2023 auch 2026 noch im Jahr 2023
    landet, und mit dem Absender, falls jemand danach sortieren will.

    Der Trenner im Muster ist immer `/`; ersetzt wird er durch den, den der Server benutzt
    (Punkt bei Courier, Schrägstrich bei Dovecot). So bleibt ein Muster über einen Umzug
    hinweg gültig, und niemand muss wissen, wie sein IMAP-Server Ordner schachtelt.
    """
    import datetime as _dt
    import re

    if account.archive_mode != "pattern" or not account.archive_pattern:
        return account.folder_archive

    wann = message_datum or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(wann, str):
        try:
            from email.utils import parsedate_to_datetime
            wann = parsedate_to_datetime(wann)
        except Exception:  # noqa: BLE001 — ein unlesbares Datum ist kein Grund, nicht zu archivieren
            wann = _dt.datetime.now(_dt.timezone.utc)

    absender = (absender or "").strip()
    values = {name: wann.strftime(pattern) for name, pattern in PLATZHALTER.items()}
    values["quartal"] = f"Q{(wann.month - 1) // 3 + 1}"
    values["absender"] = absender
    values["absender_domain"] = absender.rpartition("@")[2] if "@" in absender else absender

    target = account.archive_pattern
    for name, value in values.items():
        target = target.replace("{" + name + "}", str(value))
    # Was übrig bleibt, ist ein Tippfehler im Muster. Er soll auffallen, aber keinen Ordner
    # mit geschweiften Klammern im Namen anlegen.
    target = re.sub(r"\{[^}]*\}", "", target).strip("/ ")
    if trenner and trenner != "/":
        target = target.replace("/", trenner)
    return target or account.folder_archive


def _archivieren_sync(account: MailAccount, folder: str, uid: int) -> str:
    """Archiviert und legt den Zielordner an, falls es ihn noch nicht gibt.

    Ohne das Anlegen wäre ein Jahresarchiv genau einmal im Jahr kaputt — beim ersten Klick
    im Januar.
    """
    with _imap(account) as client:
        trenner = "/"
        for _flags, roh_trenner, _name in client.list_folders():
            if roh_trenner:
                trenner = roh_trenner.decode() if isinstance(roh_trenner, bytes) else roh_trenner
                break
        client.select_folder(folder)
        entry = (client.fetch([uid], ["ENVELOPE", "INTERNALDATE"]) or {}).get(uid) or {}
        envelope = entry.get(b"ENVELOPE")
        absender = ""
        if envelope is not None and envelope.from_:
            erster = envelope.from_[0]
            absender = f"{(erster.mailbox or b'').decode()}@{(erster.host or b'').decode()}"
        wann = (envelope.date if envelope is not None and envelope.date
                else entry.get(b"INTERNALDATE"))

        target = archiv_target(account, wann, absender, trenner)
        if not client.folder_exists(target):
            client.create_folder(target)
            try:
                client.subscribe_folder(target)
            except Exception:  # noqa: BLE001 — nicht jeder Server kennt Abonnements
                log.debug("Kein Abonnement für %s", target)
        if client.has_capability("MOVE"):
            client.move([uid], target)
        else:
            client.copy([uid], target)
            client.add_flags([uid], [b"\\Deleted"])
            client.expunge()
        return target


def _ungelesen_sync(account: MailAccount, folder: str = "INBOX") -> int:
    """Wieviel Ungelesenes liegt im Posteingang? — eine Frage, eine Antwort.

    Bewusst nur der Posteingang und nicht die Summe über alle Ordner: „neue Post" meint das,
    was hereingekommen ist, nicht die zweihundert ungelesenen Newsletter im Archiv. Und es
    ist ein Aufruf statt vierzig.
    """
    with _imap(account) as client:
        state = client.folder_status(folder, ["UNSEEN"])
        return int(state.get(b"UNSEEN", 0))


def _all_gelesen_sync(account: MailAccount, folder: str) -> int:
    """Setzt \\Seen auf alles Ungelesene und sagt, wie viele es waren.

    Nur die Ungelesenen anzufassen ist kein Geiz, sondern Rücksicht: Ein Server, der jedes
    Flag einzeln bestätigt, hat bei zehntausend Nachrichten sonst viel zu tun — und die
    Zahl in der Rückmeldung ist ohnehin die, die den Menschen interessiert.
    """
    with _imap(account) as client:
        client.select_folder(folder)
        offen = client.search(["UNSEEN"])
        if offen:
            client.add_flags(offen, [b"\\Seen"])
        return len(offen)


def _folder_delete_sync(account: MailAccount, folder: str) -> None:
    with _imap(account) as client:
        # Erst wegschalten: manche Server verweigern das Löschen des gewählten Ordners.
        client.select_folder("INBOX", readonly=True)
        client.delete_folder(folder)


def _endgueltig_sync(account: MailAccount, folder: str, uid: int) -> None:
    """Wirklich weg. Nur für den Papierkorb gedacht — überall sonst wird verschoben, damit
    ein Fehlgriff eine Bewegung bleibt und kein Verlust."""
    with _imap(account) as client:
        client.select_folder(folder)
        client.add_flags([uid], [b"\\Deleted"])
        client.expunge()


def _entwurf_sync(account: MailAccount, roh: bytes) -> None:
    with _imap(account) as client:
        client.append(account.folder_drafts, roh, flags=[b"\\Draft"])


def _ablegen_sync(account: MailAccount, folder: str, roh: bytes) -> None:
    with _imap(account) as client:
        client.append(folder, roh, flags=[b"\\Seen"])


# ── Bauen und Senden ────────────────────────────────────────────────────────

def baue_message(identity: MailIdentity, fields: dict) -> EmailMessage:
    """Aus den Feldern des Formulars eine Nachricht — eine Stelle für Entwurf und Versand."""
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
    koerper = fields.get("text") or ""
    if identity.signature:
        koerper = f"{koerper}\n\n-- \n{identity.signature}"
    msg.set_content(koerper)
    for attachment in fields.get("attachments") or []:
        haupt, _, unter = (attachment.get("content_type") or "application/octet-stream").partition("/")
        msg.add_attachment(attachment["data"], maintype=haupt, subtype=unter or "octet-stream",
                           filename=attachment.get("filename") or "anhang")
    return msg


def _senden_sync(account: MailAccount, msg: EmailMessage) -> None:
    passwort = decrypt_secret(account.smtp_password_enc)
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
            server.login(account.smtp_user, passwort)
        server.send_message(msg)


def _deutlicher(error: Exception, account: MailAccount, smtp: bool) -> str:
    """Die Meldung der Bibliothek um den Satz ergänzen, der weiterhilft.

    `WRONG_VERSION_NUMBER` heißt fast immer dasselbe: verschlüsselt angeklopft, wo der Server
    erst im Klartext grüßt und dann aufrüstet (STARTTLS) — oder umgekehrt. Wer das nicht
    schon weiß, liest sonst eine Zeile aus `_ssl.c` und ist keinen Schritt weiter.
    """
    text = str(error)
    if "WRONG_VERSION_NUMBER" in text or "record layer failure" in text:
        port = account.smtp_port if smtp else account.imap_port
        kind = account.smtp_security if smtp else ("ssl" if account.imap_ssl else "none")
        rat = ("Port 587 spricht STARTTLS, Port 465 ist von Anfang an verschlüsselt"
               if smtp else
               "Port 993 ist von Anfang an verschlüsselt, Port 143 rüstet erst auf")
        return (f"{text} — Port {port} und Verschlüsselung „{kind}\" passen nicht zusammen. "
                f"{rat}.")
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return f"{text} — das Zertifikat des Servers ist nicht überprüfbar."
    if "AUTHENTICATIONFAILED" in text.upper() or "authentication failed" in text.lower():
        return f"{text} — Benutzername oder Kennwort stimmen nicht."
    return text


def _check_sync(account: MailAccount) -> dict:
    """Ein Verbindungstest, der beide Wege anfasst — sonst merkt man den Tippfehler im
    SMTP-Kennwort erst, wenn eine Antwort nicht rausgeht."""
    result: dict = {"imap": "", "smtp": ""}
    try:
        with _imap(account) as client:
            client.select_folder("INBOX", readonly=True)
        result["imap"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["imap"] = _deutlicher(exc, account, smtp=False)[:300]
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
            result["smtp"] = _deutlicher(exc, account, smtp=True)[:300]
    return result


# ── Async-Hüllen (die API ruft nur diese) ────────────────────────────────────

async def folder(account: MailAccount, count: bool = False) -> list[dict]:
    return await asyncio.to_thread(_folder_sync, account, count)


async def listing(account: MailAccount, folder_name: str, suche: str = "", offset: int = 0,
                limit: int = 50) -> dict:
    return await asyncio.to_thread(_listing_sync, account, folder_name, suche, offset, limit)


async def message(account: MailAccount, folder_name: str, uid: int) -> dict:
    return await asyncio.to_thread(_message_sync, account, folder_name, uid)


async def attachment(account: MailAccount, folder_name: str, uid: int,
                 index: int) -> tuple[str, str, bytes]:
    return await asyncio.to_thread(_attachment_sync, account, folder_name, uid, index)


async def flag(account: MailAccount, folder_name: str, uid: int, name: str, an: bool) -> None:
    await asyncio.to_thread(_flag_sync, account, folder_name, uid, name, an)


async def move(account: MailAccount, folder_name: str, uid: int, target: str) -> None:
    await asyncio.to_thread(_move_sync, account, folder_name, uid, target)


async def ungelesen(account: MailAccount, folder_name: str = "INBOX") -> int:
    return await asyncio.to_thread(_ungelesen_sync, account, folder_name)


async def all_gelesen(account: MailAccount, folder_name: str) -> int:
    return await asyncio.to_thread(_all_gelesen_sync, account, folder_name)


async def folder_delete(account: MailAccount, folder_name: str) -> None:
    await asyncio.to_thread(_folder_delete_sync, account, folder_name)


async def archivieren(account: MailAccount, folder_name: str, uid: int) -> str:
    """Archiviert die Nachricht und gibt zurück, wo sie gelandet ist."""
    return await asyncio.to_thread(_archivieren_sync, account, folder_name, uid)


async def endgueltig_delete(account: MailAccount, folder_name: str, uid: int) -> None:
    await asyncio.to_thread(_endgueltig_sync, account, folder_name, uid)


async def entwurf_speichern(account: MailAccount, identity: MailIdentity,
                            fields: dict) -> None:
    msg = baue_message(identity, fields)
    await asyncio.to_thread(_entwurf_sync, account, msg.as_bytes())


async def senden(account: MailAccount, identity: MailIdentity, fields: dict) -> None:
    msg = baue_message(identity, fields)
    await asyncio.to_thread(_senden_sync, account, msg)
    # Eine gesendete Mail, die im eigenen Postfach fehlt, ist eine verlorene: das Gespräch
    # steht danach nur noch auf der Gegenseite.
    try:
        await asyncio.to_thread(_ablegen_sync, account, account.folder_sent, msg.as_bytes())
    except Exception:  # noqa: BLE001
        log.exception("Kopie im Ordner %s fehlgeschlagen (die Mail ist raus)",
                      account.folder_sent)


async def check(account: MailAccount) -> dict:
    return await asyncio.to_thread(_check_sync, account)
