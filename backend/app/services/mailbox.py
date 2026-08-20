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
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from imapclient import IMAPClient

from ..core.security import decrypt_secret
from ..models.mail import MailAccount, MailIdentity

log = logging.getLogger("mailbox")

# Wieviel Text einer Nachricht in die Oberfläche geht. Eine Mail mit einem eingebetteten
# Bildarchiv im HTML-Teil hat schon Megabyte an Base64 — das braucht niemand zu lesen.
MAX_TEXT = 200_000


def _imap(account: MailAccount) -> IMAPClient:
    client = IMAPClient(account.imap_host, port=account.imap_port, ssl=account.imap_ssl,
                        timeout=30)
    client.login(account.imap_user, decrypt_secret(account.imap_password_enc))
    return client


def _text_aus(msg: email.message.Message) -> tuple[str, str]:
    """(Text, HTML) einer Nachricht — beides, soweit vorhanden."""
    text, html = "", ""
    if msg.is_multipart():
        for teil in msg.walk():
            if teil.get_content_maintype() == "multipart":
                continue
            if teil.get_filename():          # Anhänge gehören nicht in den Fließtext
                continue
            inhalt = teil.get_content_type()
            try:
                roh = teil.get_payload(decode=True) or b""
                stueck = roh.decode(teil.get_content_charset() or "utf-8", errors="replace")
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

    def um(treffer):
        nonlocal fern
        adresse = treffer.group(2)
        if adresse.startswith("data:"):
            return treffer.group(0)
        fern = True
        return f'{treffer.group(1)}data-fern="{adresse}"'

    sauber = re.sub(r'(<img\b[^>]*?)src="([^"]*)"', um, sauber, flags=re.I)
    return sauber, fern


def _anhaenge(msg: email.message.Message) -> list[dict]:
    """Index der Anhänge — Name, Typ, Größe. Der Inhalt wird erst auf Abruf geholt: eine
    Liste von zwanzig Mails soll keine zwanzig PDF durchs Netz ziehen."""
    out = []
    for i, teil in enumerate(msg.walk()):
        name = teil.get_filename()
        if not name:
            continue
        roh = teil.get_payload(decode=True) or b""
        out.append({"index": i, "filename": _kopf(name),
                    "content_type": teil.get_content_type(), "size": len(roh)})
    return out


def _kopf(roh) -> str:
    """Kopfzeile lesbar machen (=?utf-8?B?…?= und Konsorten)."""
    from email.header import decode_header, make_header

    if roh is None:
        return ""
    try:
        return str(make_header(decode_header(str(roh))))
    except Exception:  # noqa: BLE001
        return str(roh)


def _kopfzeile_adressen(roh) -> list[dict]:
    out = []
    for teil in str(roh or "").split(","):
        name, addr = parseaddr(teil.strip())
        if addr:
            out.append({"name": _kopf(name), "addr": addr})
    return out


# ── Lesen ───────────────────────────────────────────────────────────────────

# Reihenfolge der Sonderordner, wie sie jedes Mail-Programm zeigt: was man täglich braucht,
# steht oben, der Rest alphabetisch darunter.
_SONDER_REIHE = ["inbox", "drafts", "sent", "junk", "trash", "archive"]


def baum_sortieren(eintraege: list[dict]) -> list[dict]:
    """Ordner in Anzeigereihenfolge: jedes Kind direkt unter seinem Elternteil.

    Nach dem vollen Namen zu sortieren sieht aus wie ein Baum und ist keiner: `Archives`
    steht alphabetisch vor `INBOX.Aliexpress`, also rutschten die Unterordner des
    Posteingangs unter das Archiv — mit Einrückung, was den Eindruck perfekt macht. Also
    wirklich absteigen: je Ebene sortieren, dann die Kinder dahinter.

    `level` kommt dabei aus der Tiefe im Baum und nicht mehr aus der Zahl der Trennzeichen
    im Namen: ein Ordner, dessen Elternteil der Server gar nicht auflistet, ist eine Wurzel
    und darf nicht eingerückt ins Leere zeigen.
    """
    nach_name = {e["name"]: e for e in eintraege}
    kinder: dict[str, list[dict]] = {}
    wurzeln: list[dict] = []
    for e in eintraege:
        eltern = e.get("parent") or ""
        if eltern and eltern in nach_name:
            kinder.setdefault(eltern, []).append(e)
        else:
            wurzeln.append(e)

    def schluessel(e: dict) -> tuple:
        rang = _SONDER_REIHE.index(e["special"]) if e["special"] in _SONDER_REIHE else 99
        return (rang, (e.get("display") or e["name"]).lower())

    out: list[dict] = []

    def absteigen(knoten: list[dict], ebene: int) -> None:
        for e in sorted(knoten, key=schluessel):
            e["level"] = ebene
            out.append(e)
            absteigen(kinder.get(e["name"], []), ebene + 1)

    absteigen(wurzeln, 0)
    return out


def _ordner_sync(account: MailAccount, zaehlen: bool) -> list[dict]:
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
                zuordnung = {account.folder_sent: "sent", account.folder_drafts: "drafts",
                             account.folder_trash: "trash", account.folder_junk: "junk",
                             account.folder_archive: "archive"}
                besonders = zuordnung.get(name, "")
            if name.upper() == "INBOX":
                besonders = "inbox"
            trennzeichen = trenner.decode() if isinstance(trenner, bytes) else (trenner or "/")
            teile = name.split(trennzeichen) if trennzeichen else [name]
            roh.append({
                "name": name,
                "display": teile[-1] if teile else name,
                "level": max(0, len(teile) - 1),
                "parent": trennzeichen.join(teile[:-1]) if len(teile) > 1 else "",
                "delimiter": trennzeichen,
                "special": besonders,
                "unseen": 0, "total": 0,
            })

        if zaehlen:
            for eintrag in roh:
                try:
                    stand = client.folder_status(eintrag["name"], ["UNSEEN", "MESSAGES"])
                    eintrag["unseen"] = int(stand.get(b"UNSEEN", 0))
                    eintrag["total"] = int(stand.get(b"MESSAGES", 0))
                except Exception:  # noqa: BLE001 — ein Ordner ohne Auskunft ist kein Fehler
                    log.debug("Kein Status für %s", eintrag["name"])

        return baum_sortieren(roh)


def _hat_anhang(struktur) -> bool:
    """Hat diese Nachricht einen Anhang? — beantwortet aus der BODYSTRUCTURE.

    Der ganze Sinn der Frage ist, sie zu beantworten, OHNE die Mail zu laden: eine Liste von
    fünfzig Nachrichten soll keine fünfzig Anhänge durchs Netz ziehen. Die Struktur ist
    verschachtelt und je nach Server unterschiedlich tief, deshalb wird sie durchsucht statt
    an festen Stellen abgegriffen.

    Nur `attachment` zählt. Ein eingebettetes Logo im HTML (`inline`) ist kein Anhang, und
    eine Büroklammer an jeder Werbemail wäre keine Auskunft mehr.
    """
    def durchsuchen(teil) -> bool:
        if not isinstance(teil, (list, tuple)):
            return False
        for element in teil:
            if isinstance(element, bytes) and element.lower() == b"attachment":
                return True
            if durchsuchen(element):
                return True
        return False

    return durchsuchen(struktur)


def _liste_sync(account: MailAccount, ordner: str, suche: str, offset: int,
                limit: int) -> dict:
    with _imap(account) as client:
        stand = client.select_folder(ordner, readonly=True)
        gesamt = stand.get(b"EXISTS", 0)
        kriterium = ["TEXT", suche] if suche else ["ALL"]
        uids = client.search(kriterium)
        uids = list(reversed(uids))          # neueste zuerst, wie in jedem Postfach
        ausschnitt = uids[offset:offset + limit]
        if not ausschnitt:
            return {"total": len(uids), "exists": gesamt, "messages": []}
        roh = client.fetch(ausschnitt, ["ENVELOPE", "FLAGS", "RFC822.SIZE", "INTERNALDATE",
                                        "BODYSTRUCTURE"])
        nachrichten = []
        for uid in ausschnitt:
            eintrag = roh.get(uid) or {}
            umschlag = eintrag.get(b"ENVELOPE")
            flags = {f.decode().lower() for f in eintrag.get(b"FLAGS", ())}
            absender = ""
            if umschlag is not None and umschlag.from_:
                erster = umschlag.from_[0]
                name = _kopf(erster.name.decode() if erster.name else "")
                adresse = f"{(erster.mailbox or b'').decode()}@{(erster.host or b'').decode()}"
                absender = formataddr((name, adresse))
            nachrichten.append({
                "uid": uid,
                "subject": _kopf(umschlag.subject.decode("utf-8", "replace")
                                 if umschlag is not None and umschlag.subject else ""),
                "from": absender,
                "date": (eintrag.get(b"INTERNALDATE") or dt.datetime.now(dt.timezone.utc)).isoformat(),
                "size": eintrag.get(b"RFC822.SIZE", 0),
                "seen": "\\seen" in flags,
                "flagged": "\\flagged" in flags,
                "answered": "\\answered" in flags,
                "has_attachment": _hat_anhang(eintrag.get(b"BODYSTRUCTURE")),
            })
        return {"total": len(uids), "exists": gesamt, "messages": nachrichten}


def _nachricht_sync(account: MailAccount, ordner: str, uid: int) -> dict:
    with _imap(account) as client:
        client.select_folder(ordner)
        roh = client.fetch([uid], ["RFC822", "FLAGS"])
        eintrag = roh.get(uid)
        if not eintrag:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(eintrag[b"RFC822"], policy=email.policy.default)
        text, html = _text_aus(msg)
        html_sauber, fernbilder = saeubern(html) if html else ("", False)
        flags = {f.decode().lower() for f in eintrag.get(b"FLAGS", ())}
        return {
            "uid": uid, "folder": ordner,
            "subject": _kopf(msg.get("Subject")),
            "from": _kopfzeile_adressen(msg.get("From")),
            "to": _kopfzeile_adressen(msg.get("To")),
            "cc": _kopfzeile_adressen(msg.get("Cc")),
            "reply_to": _kopfzeile_adressen(msg.get("Reply-To")),
            "date": _kopf(msg.get("Date")),
            "message_id": str(msg.get("Message-ID") or ""),
            "text": text, "html": html_sauber, "remote_images": fernbilder,
            "attachments": _anhaenge(msg),
            "seen": "\\seen" in flags, "flagged": "\\flagged" in flags,
        }


def _anhang_sync(account: MailAccount, ordner: str, uid: int, index: int) -> tuple[str, str, bytes]:
    with _imap(account) as client:
        client.select_folder(ordner, readonly=True)
        roh = client.fetch([uid], ["RFC822"])
        eintrag = roh.get(uid)
        if not eintrag:
            raise LookupError("Nachricht nicht gefunden")
        msg = email.message_from_bytes(eintrag[b"RFC822"], policy=email.policy.default)
        for i, teil in enumerate(msg.walk()):
            if i != index:
                continue
            name = teil.get_filename()
            if not name:
                break
            return (_kopf(name), teil.get_content_type(), teil.get_payload(decode=True) or b"")
    raise LookupError("Anhang nicht gefunden")


# ── Ändern ──────────────────────────────────────────────────────────────────

def _flag_sync(account: MailAccount, ordner: str, uid: int, flag: str, an: bool) -> None:
    with _imap(account) as client:
        client.select_folder(ordner)
        if an:
            client.add_flags([uid], [flag])
        else:
            client.remove_flags([uid], [flag])


def _verschieben_sync(account: MailAccount, ordner: str, uid: int, ziel: str) -> None:
    with _imap(account) as client:
        client.select_folder(ordner)
        # MOVE, wo der Server es kann; sonst der alte Weg (kopieren, löschen, aufräumen).
        if client.has_capability("MOVE"):
            client.move([uid], ziel)
        else:
            client.copy([uid], ziel)
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


def archiv_ziel(account: MailAccount, nachricht_datum, absender: str = "",
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

    wann = nachricht_datum or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(wann, str):
        try:
            from email.utils import parsedate_to_datetime
            wann = parsedate_to_datetime(wann)
        except Exception:  # noqa: BLE001 — ein unlesbares Datum ist kein Grund, nicht zu archivieren
            wann = _dt.datetime.now(_dt.timezone.utc)

    absender = (absender or "").strip()
    werte = {name: wann.strftime(muster) for name, muster in PLATZHALTER.items()}
    werte["quartal"] = f"Q{(wann.month - 1) // 3 + 1}"
    werte["absender"] = absender
    werte["absender_domain"] = absender.rpartition("@")[2] if "@" in absender else absender

    ziel = account.archive_pattern
    for name, wert in werte.items():
        ziel = ziel.replace("{" + name + "}", str(wert))
    # Was übrig bleibt, ist ein Tippfehler im Muster. Er soll auffallen, aber keinen Ordner
    # mit geschweiften Klammern im Namen anlegen.
    ziel = re.sub(r"\{[^}]*\}", "", ziel).strip("/ ")
    if trenner and trenner != "/":
        ziel = ziel.replace("/", trenner)
    return ziel or account.folder_archive


def _archivieren_sync(account: MailAccount, ordner: str, uid: int) -> str:
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
        client.select_folder(ordner)
        eintrag = (client.fetch([uid], ["ENVELOPE", "INTERNALDATE"]) or {}).get(uid) or {}
        umschlag = eintrag.get(b"ENVELOPE")
        absender = ""
        if umschlag is not None and umschlag.from_:
            erster = umschlag.from_[0]
            absender = f"{(erster.mailbox or b'').decode()}@{(erster.host or b'').decode()}"
        wann = (umschlag.date if umschlag is not None and umschlag.date
                else eintrag.get(b"INTERNALDATE"))

        ziel = archiv_ziel(account, wann, absender, trenner)
        if not client.folder_exists(ziel):
            client.create_folder(ziel)
            try:
                client.subscribe_folder(ziel)
            except Exception:  # noqa: BLE001 — nicht jeder Server kennt Abonnements
                log.debug("Kein Abonnement für %s", ziel)
        if client.has_capability("MOVE"):
            client.move([uid], ziel)
        else:
            client.copy([uid], ziel)
            client.add_flags([uid], [b"\\Deleted"])
            client.expunge()
        return ziel


def _ungelesen_sync(account: MailAccount, ordner: str = "INBOX") -> int:
    """Wieviel Ungelesenes liegt im Posteingang? — eine Frage, eine Antwort.

    Bewusst nur der Posteingang und nicht die Summe über alle Ordner: „neue Post" meint das,
    was hereingekommen ist, nicht die zweihundert ungelesenen Newsletter im Archiv. Und es
    ist ein Aufruf statt vierzig.
    """
    with _imap(account) as client:
        stand = client.folder_status(ordner, ["UNSEEN"])
        return int(stand.get(b"UNSEEN", 0))


def _alle_gelesen_sync(account: MailAccount, ordner: str) -> int:
    """Setzt \\Seen auf alles Ungelesene und sagt, wie viele es waren.

    Nur die Ungelesenen anzufassen ist kein Geiz, sondern Rücksicht: Ein Server, der jedes
    Flag einzeln bestätigt, hat bei zehntausend Nachrichten sonst viel zu tun — und die
    Zahl in der Rückmeldung ist ohnehin die, die den Menschen interessiert.
    """
    with _imap(account) as client:
        client.select_folder(ordner)
        offen = client.search(["UNSEEN"])
        if offen:
            client.add_flags(offen, [b"\\Seen"])
        return len(offen)


def _ordner_loeschen_sync(account: MailAccount, ordner: str) -> None:
    with _imap(account) as client:
        # Erst wegschalten: manche Server verweigern das Löschen des gewählten Ordners.
        client.select_folder("INBOX", readonly=True)
        client.delete_folder(ordner)


def _endgueltig_sync(account: MailAccount, ordner: str, uid: int) -> None:
    """Wirklich weg. Nur für den Papierkorb gedacht — überall sonst wird verschoben, damit
    ein Fehlgriff eine Bewegung bleibt und kein Verlust."""
    with _imap(account) as client:
        client.select_folder(ordner)
        client.add_flags([uid], [b"\\Deleted"])
        client.expunge()


def _entwurf_sync(account: MailAccount, roh: bytes) -> None:
    with _imap(account) as client:
        client.append(account.folder_drafts, roh, flags=[b"\\Draft"])


def _ablegen_sync(account: MailAccount, ordner: str, roh: bytes) -> None:
    with _imap(account) as client:
        client.append(ordner, roh, flags=[b"\\Seen"])


# ── Bauen und Senden ────────────────────────────────────────────────────────

def baue_nachricht(identitaet: MailIdentity, felder: dict) -> EmailMessage:
    """Aus den Feldern des Formulars eine Nachricht — eine Stelle für Entwurf und Versand."""
    msg = EmailMessage()
    msg["From"] = formataddr((identitaet.display_name or "", identitaet.email))
    msg["To"] = ", ".join(felder.get("to") or [])
    if felder.get("cc"):
        msg["Cc"] = ", ".join(felder["cc"])
    if felder.get("bcc"):
        msg["Bcc"] = ", ".join(felder["bcc"])
    if identitaet.reply_to:
        msg["Reply-To"] = identitaet.reply_to
    msg["Subject"] = felder.get("subject") or ""
    if felder.get("in_reply_to"):
        msg["In-Reply-To"] = felder["in_reply_to"]
        msg["References"] = felder["in_reply_to"]
    koerper = felder.get("text") or ""
    if identitaet.signature:
        koerper = f"{koerper}\n\n-- \n{identitaet.signature}"
    msg.set_content(koerper)
    for anhang in felder.get("attachments") or []:
        haupt, _, unter = (anhang.get("content_type") or "application/octet-stream").partition("/")
        msg.add_attachment(anhang["data"], maintype=haupt, subtype=unter or "octet-stream",
                           filename=anhang.get("filename") or "anhang")
    return msg


def _senden_sync(account: MailAccount, msg: EmailMessage) -> None:
    passwort = decrypt_secret(account.smtp_password_enc)
    kontext = ssl.create_default_context()
    if account.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=30,
                                  context=kontext)
    else:
        server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
    with server:
        if account.smtp_security == "starttls":
            server.starttls(context=kontext)
        if account.smtp_user:
            server.login(account.smtp_user, passwort)
        server.send_message(msg)


def _deutlicher(fehler: Exception, account: MailAccount, smtp: bool) -> str:
    """Die Meldung der Bibliothek um den Satz ergänzen, der weiterhilft.

    `WRONG_VERSION_NUMBER` heißt fast immer dasselbe: verschlüsselt angeklopft, wo der Server
    erst im Klartext grüßt und dann aufrüstet (STARTTLS) — oder umgekehrt. Wer das nicht
    schon weiß, liest sonst eine Zeile aus `_ssl.c` und ist keinen Schritt weiter.
    """
    text = str(fehler)
    if "WRONG_VERSION_NUMBER" in text or "record layer failure" in text:
        port = account.smtp_port if smtp else account.imap_port
        art = account.smtp_security if smtp else ("ssl" if account.imap_ssl else "none")
        rat = ("Port 587 spricht STARTTLS, Port 465 ist von Anfang an verschlüsselt"
               if smtp else
               "Port 993 ist von Anfang an verschlüsselt, Port 143 rüstet erst auf")
        return (f"{text} — Port {port} und Verschlüsselung „{art}\" passen nicht zusammen. "
                f"{rat}.")
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return f"{text} — das Zertifikat des Servers ist nicht überprüfbar."
    if "AUTHENTICATIONFAILED" in text.upper() or "authentication failed" in text.lower():
        return f"{text} — Benutzername oder Kennwort stimmen nicht."
    return text


def _pruefen_sync(account: MailAccount) -> dict:
    """Ein Verbindungstest, der beide Wege anfasst — sonst merkt man den Tippfehler im
    SMTP-Kennwort erst, wenn eine Antwort nicht rausgeht."""
    ergebnis: dict = {"imap": "", "smtp": ""}
    try:
        with _imap(account) as client:
            client.select_folder("INBOX", readonly=True)
        ergebnis["imap"] = "ok"
    except Exception as exc:  # noqa: BLE001
        ergebnis["imap"] = _deutlicher(exc, account, smtp=False)[:300]
    if account.smtp_host:
        try:
            kontext = ssl.create_default_context()
            if account.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=20,
                                          context=kontext)
            else:
                server = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=20)
            with server:
                if account.smtp_security == "starttls":
                    server.starttls(context=kontext)
                if account.smtp_user:
                    server.login(account.smtp_user, decrypt_secret(account.smtp_password_enc))
            ergebnis["smtp"] = "ok"
        except Exception as exc:  # noqa: BLE001
            ergebnis["smtp"] = _deutlicher(exc, account, smtp=True)[:300]
    return ergebnis


# ── Async-Hüllen (die API ruft nur diese) ────────────────────────────────────

async def ordner(account: MailAccount, zaehlen: bool = False) -> list[dict]:
    return await asyncio.to_thread(_ordner_sync, account, zaehlen)


async def liste(account: MailAccount, ordner_name: str, suche: str = "", offset: int = 0,
                limit: int = 50) -> dict:
    return await asyncio.to_thread(_liste_sync, account, ordner_name, suche, offset, limit)


async def nachricht(account: MailAccount, ordner_name: str, uid: int) -> dict:
    return await asyncio.to_thread(_nachricht_sync, account, ordner_name, uid)


async def anhang(account: MailAccount, ordner_name: str, uid: int,
                 index: int) -> tuple[str, str, bytes]:
    return await asyncio.to_thread(_anhang_sync, account, ordner_name, uid, index)


async def flag(account: MailAccount, ordner_name: str, uid: int, name: str, an: bool) -> None:
    await asyncio.to_thread(_flag_sync, account, ordner_name, uid, name, an)


async def verschieben(account: MailAccount, ordner_name: str, uid: int, ziel: str) -> None:
    await asyncio.to_thread(_verschieben_sync, account, ordner_name, uid, ziel)


async def ungelesen(account: MailAccount, ordner_name: str = "INBOX") -> int:
    return await asyncio.to_thread(_ungelesen_sync, account, ordner_name)


async def alle_gelesen(account: MailAccount, ordner_name: str) -> int:
    return await asyncio.to_thread(_alle_gelesen_sync, account, ordner_name)


async def ordner_loeschen(account: MailAccount, ordner_name: str) -> None:
    await asyncio.to_thread(_ordner_loeschen_sync, account, ordner_name)


async def archivieren(account: MailAccount, ordner_name: str, uid: int) -> str:
    """Archiviert die Nachricht und gibt zurück, wo sie gelandet ist."""
    return await asyncio.to_thread(_archivieren_sync, account, ordner_name, uid)


async def endgueltig_loeschen(account: MailAccount, ordner_name: str, uid: int) -> None:
    await asyncio.to_thread(_endgueltig_sync, account, ordner_name, uid)


async def entwurf_speichern(account: MailAccount, identitaet: MailIdentity,
                            felder: dict) -> None:
    msg = baue_nachricht(identitaet, felder)
    await asyncio.to_thread(_entwurf_sync, account, msg.as_bytes())


async def senden(account: MailAccount, identitaet: MailIdentity, felder: dict) -> None:
    msg = baue_nachricht(identitaet, felder)
    await asyncio.to_thread(_senden_sync, account, msg)
    # Eine gesendete Mail, die im eigenen Postfach fehlt, ist eine verlorene: das Gespräch
    # steht danach nur noch auf der Gegenseite.
    try:
        await asyncio.to_thread(_ablegen_sync, account, account.folder_sent, msg.as_bytes())
    except Exception:  # noqa: BLE001
        log.exception("Kopie im Ordner %s fehlgeschlagen (die Mail ist raus)",
                      account.folder_sent)


async def pruefen(account: MailAccount) -> dict:
    return await asyncio.to_thread(_pruefen_sync, account)
