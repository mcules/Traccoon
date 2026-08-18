"""Regelbasierte Spam-Signale aus Adressen, Kopfzeilen, Links und Anhängen — ohne Modell.

Warum überhaupt Regeln, wo doch ein Modell da ist: die verlässlichsten Spam-Anzeiger sind
technischer, nicht sprachlicher Natur. Ob DKIM von einer fremden Domain unterschrieben wurde
oder ein Link woandershin führt, als sein Text behauptet, ist eine Tatsache — ein
Sprachmodell kann sie nur nacherzählen und dabei irren. Der Text entscheidet erst dort, wo
die Technik sauber ist (und genau das ist der Grund, warum guter Betrug heute technisch
sauber ankommt).

Die Signale folgen dem, was Mailfilter und Phishing-Forschung als tragfähig ausweisen:

* **Echtheit** — SPF/DKIM/DMARC *und deren Ausrichtung* auf die Absenderdomain. Eine gültige
  Signatur einer fremden Domain ist der häufigste Weg, mit „DKIM pass" zu fälschen.
* **Täuschung am Namen** — Punycode/IDN, Schriftmischung (kyrillisches „о" in einem sonst
  lateinischen Wort), unsichtbare Zeichen, bekannte Marke als Subdomain einer fremden.
* **Kopfzeilen-Hygiene** — fehlende/kaputte Message-ID, Datumsversatz, Zufallsadressen,
  vorgetäuschte Antworten (`Re:` ohne Bezug), aufgesetzte Dringlichkeit (SpamAssassin-Erbe).
* **Links** — Text gegen Ziel, IP-Adressen statt Namen, `@`-Trick, Kürzungsdienste.
* **Anhänge** — ausführbare und skriptfähige Formate, Doppelendungen, passwortgeschützte
  Archive (die kein Scanner öffnen kann).

Zweite Aufgabe des Moduls: die **Merkmale** zerlegen, über die gelernt wird (`spam_learn`).
Urteil und Lehrstoff kommen damit aus derselben Quelle und können nicht auseinanderlaufen.

Reine Funktionen, keine DB, kein I/O — dadurch ohne Aufbau testbar. Alles, was den
Kontaktbestand braucht (bekannte Domains, Namensgleichheit), reicht der Aufrufer herein.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Adressen bei diesen Anbietern sagen über die Domain nichts aus: an gmx.de hängen ein
# ehrlicher Nachbar und ein Betrüger gleichermaßen. Domain-Signale (Whitelist wie Verdacht)
# müssen hier aussetzen, sonst spricht man mit einer Regel halbe Landstriche frei.
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch",
    "web.de", "t-online.de", "freenet.de", "yahoo.com", "yahoo.de", "outlook.com",
    "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "aol.com",
    "icloud.com", "me.com", "mail.ru", "yandex.ru", "proton.me", "protonmail.com",
    "posteo.de", "mailbox.org", "arcor.de", "online.de", "gmx.com",
})

# Endungen, die im Massenversand auffällig überrepräsentiert sind. Kein Beweis — ein
# Zuschlag, mehr nicht.
_BILLIG_TLDS = frozenset({
    "xyz", "top", "click", "link", "work", "loan", "bid", "win", "date", "stream",
    "download", "racing", "party", "review", "country", "kim", "gq", "cf", "tk", "ml",
    "zip", "mov", "rest", "cam", "quest", "sbs", "cfd", "lol",
    # Aus dem echten Spam-Bestand nachgetragen: germasale.auction, declass.business,
    # espanodeal.lat — Endungen, die dort auftauchen und in der Post sonst nirgends.
    "auction", "business", "lat", "beauty", "bond", "makeup", "hair", "skin", "monster",
    "autos", "boats", "yachts", "christmas", "fun", "one", "today", "life", "live",
})

# Kürzungsdienste verbergen das Ziel — für sich genommen üblich (Newsletter nutzen sie),
# in Verbindung mit anderen Signalen aber ein Baustein.
# Klick-Zähler der großen Versanddienste. Sie leiten in jedem Newsletter JEDEN Link um —
# der sichtbare Text nennt die Marke, das Ziel den Dienstleister. Das ist der Normalfall
# und darf nicht als Täuschung gelten, sonst ist jeder Newsletter verdächtig.
_TRACKING_DOMAINS = frozenset({
    "klclick.com", "klclick1.com", "mjt.lu", "mjtrk.com", "sendgrid.net", "sg-links.com",
    "list-manage.com", "mailchimp.com", "awstrack.me", "mailgun.org", "sparkpostmail.com",
    "createsend.com", "cmail19.com", "cmail20.com", "hubspotlinks.com", "pardot.com",
    "exct.net", "rs6.net", "mkt-link.com", "salesforce-communities.com", "braze-links.com",
    "customeriomail.com", "sailthru.com", "cheetahmail.com", "epsl1.com", "icptrack.com",
    "clicks.aweber.com", "getresponse.com", "activehosted.com", "cl.s7.exct.net",
    "click.email.com", "e.customeriomail.com", "trk.klclick.com", "mandrillapp.com",
    "postmarkapp.com", "brevo.com", "sendinblue.com", "inxmail.de", "cleverreach.com",
    "newsletter2go.de", "episerver.net", "emsecure.net", "et.mailings.de",
})

_KUERZER = frozenset({
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "ow.ly", "buff.ly", "cutt.ly",
    "rebrand.ly", "shorturl.at", "rb.gy", "t.ly", "s.id", "tiny.cc", "bl.ink", "lnkd.in",
})

# Dateiendungen, die ausführen oder skripten können — oder eine Anmeldemaske mitbringen.
# `.svg` und `.html` stehen hier, weil beide inzwischen als getarnte Anmeldeseiten
# verschickt werden; `.iso`/`.img`/`.lnk`, weil sie Windows-Herkunftsmarkierungen umgehen.
_GEFAEHRLICHE_ENDUNGEN = frozenset({
    "exe", "scr", "com", "pif", "cpl", "msi", "msp", "bat", "cmd", "ps1", "vbs", "vbe",
    "js", "jse", "wsf", "wsh", "hta", "jar", "reg", "lnk", "inf", "scf", "iso", "img",
    "vhd", "vhdx", "ace", "docm", "xlsm", "xlsb", "pptm", "dotm", "xlam", "chm",
    "appx", "msix", "apk",
})
# Angehängte Webseiten sind ein bekannter Weg für nachgebaute Anmeldemasken — aber auch
# Google hängt seine Nutzungsbedingungen als `.html` an. Deshalb eigener, leichter Posten
# statt gemeinsamer Topf mit ausführbaren Dateien.
_WEBSEITEN_ENDUNGEN = frozenset({"html", "htm", "xht", "xhtml", "shtml", "svg", "mhtml"})
# Endungen, die harmlos aussehen — die erste Hälfte einer Doppelendung.
_HARMLOS_WIRKEND = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "jpg", "jpeg", "png",
    "gif", "rtf", "csv", "odt", "zip",
})
_ARCHIVE = frozenset({"zip", "rar", "7z", "gz", "tar", "cab", "arj"})
_PASSWORT_WORTE = re.compile(r"\b(passwor[dt]|kennwor[dt]|entsperrcode|pin\s*:)\b", re.IGNORECASE)

# Unsichtbare Zeichen: sie brechen Muster für Filter, ohne dass ein Mensch etwas sieht.
# In einer echten Mail haben sie nichts zu suchen (Ausnahme: Emoji-Verbinder, deshalb
# steht U+200D hier nicht drin).
_UNSICHTBAR = re.compile(r"[​‌‎‏⁠-⁤﻿­]")

_ZWEITEILIGE_TLD_LABEL = ("co", "com", "org", "net", "gov", "ac")

# Gewichte der Einzelsignale. Summe wird gedeckelt; keine Einzelregel darf allein
# durchentscheiden — dafür sind die Signale zu unterschiedlich verlässlich, und ein
# Fehlalarm kostet hier mehr als ein durchgerutschter Werbebrief.
_GEWICHT = {
    # Urteil des eigenen Mailservers — an echter Post gemessen der mit Abstand
    # aussagekräftigste Wert, der ohnehin schon im Kopf steht.
    "server_spam_hoch": 0.70,
    "server_spam_mittel": 0.40,
    "server_spam_flag": 0.50,
    "betreff_spam_markiert": 0.50,
    # Echtheit
    "dmarc_fail": 0.45,
    "spf_fail": 0.35,
    "dkim_fail": 0.25,
    "dkim_nicht_ausgerichtet": 0.35,
    "dkim_fehlt": 0.20,
    "auth_fehlt": 0.25,
    "absender_bin_ich": 0.40,
    "returnpath_mismatch": 0.30,
    "replyto_fremd": 0.30,
    # Vorgetäuschter Massenversand: das Layout einer Werbemail, aber kein einziger echter
    # Link. Eine Mail, die sich als Newsletter ausgibt und den Abmeldeweg nur behauptet,
    # hat ihre eigene Bauform nicht verstanden (2026-08-18: Domain-Rechnungs-Phishing mit
    # 0 a-Tags in aufwendigem HTML).
    "abmeldung_nur_behauptet": 0.35,
    "html_ohne_links": 0.25,
    # Geschäftsvorgang an eine Rolle: Rechnungen, Verträge und Kundenkonten gehören zu
    # einer Person, nicht zu einem Postfach wie info@ oder fragen@. Wer an eine öffentliche
    # Sammeladresse „Ihre Rechnung" schreibt, kennt die Beziehung nicht, die er behauptet.
    "geschaeft_an_rollenadresse": 0.35,
    # Keine Heuristik, sondern eine Auskunft des Menschen: über diese Domain läuft
    # nachweislich kein Vertragswesen. Entsprechend schwerer.
    "geschaeft_an_domain_ohne_geschaeft": 0.50,
    # Täuschung am Namen
    "absender_name_taeuscht": 0.30,
    "punycode_absender": 0.35,
    "schriftmischung": 0.40,
    "unsichtbare_zeichen": 0.30,
    "marke_als_subdomain": 0.40,
    # Wird von außen gesetzt (braucht den Kontaktbestand), das Gewicht gehört trotzdem
    # hierher — sonst stünde die halbe Bewertung an einer anderen Stelle.
    #
    # Bewusst NICHT stark: an echter Post gemessen schlägt die Regel auch dann an, wenn ein
    # Bekannter schlicht von seiner zweiten Adresse schreibt (privat statt Arbeit) — der
    # Vault kennt nie alle Adressen eines Menschen. Als Beitrag unter anderen taugt sie,
    # als Urteil nicht; den Rest erledigt das Gedächtnis nach der ersten Rückmeldung.
    "namens_kollision": 0.25,
    # Kopfzeilen
    "msgid_fehlt": 0.20,
    "msgid_kaputt": 0.15,
    "datum_versatz": 0.15,
    "absender_zufaellig": 0.20,
    "betreff_geschrien": 0.10,
    "betreff_gestreckt": 0.25,
    "fake_antwort": 0.25,
    "aufgesetzte_dringlichkeit": 0.10,
    "received_kette_kurz": 0.10,
    "absender_vokallos": 0.20,
    # Verteilung
    "bcc_blast": 0.20,
    "billig_tld": 0.15,
    "kein_unsubscribe_bei_bulk": 0.10,
    # Links
    "link_text_taeuscht": 0.45,
    "link_ip_adresse": 0.35,
    "link_at_trick": 0.35,
    "link_punycode": 0.35,
    "link_billig_tld": 0.15,
    "link_kuerzungsdienst": 0.10,
    # Umleitung in einem Newsletter über einen unbekannten Dienst: erklärbar,
    # aber nicht selbstverständlich — Verdachtspunkt, kein Urteil.
    "link_text_umgeleitet": 0.10,
    # Anhänge
    "anhang_ausfuehrbar": 0.50,
    # Eine angehängte Webseite ist ein bekannter Weg für Anmeldemasken — aber auch
    # Google verschickt seine Nutzungsbedingungen als .html. Eigener, leichter Posten.
    "anhang_webseite": 0.15,
    "anhang_doppelendung": 0.45,
    "anhang_archiv_mit_passwort": 0.35,
}

_MAX_SCORE = 1.0


def ist_meine(adresse: str, meine: frozenset[str]) -> bool:
    """Gehört die Adresse mir? Einträge dürfen `*@meine-domain.de` lauten — wer eine ganze
    Domain empfängt (Catch-all mit Wegwerf-Aliasen), kann seine Adressen nicht aufzählen."""
    adresse = (adresse or "").lower()
    if adresse in meine:
        return True
    domain = _domain(adresse)
    return bool(domain) and f"*@{domain}" in meine


@dataclass
class RuleResult:
    """Urteil der Regeln über eine Mail."""

    score: float = 0.0
    # Menschenlesbare Begründungen — sie stehen später wörtlich in der Telegram-Karte.
    reasons: list[str] = field(default_factory=list)
    # Signalschlüssel (stabil, maschinenlesbar) für Lernen und Auswertung.
    signals: list[str] = field(default_factory=list)
    sender_email: str = ""
    sender_domain: str = ""
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    # Bestellter Massenversand (gültiger Abmeldeweg, Technik sauber): eigene Kategorie.
    # Ein Newsletter ist KEIN Spam — wer das gleichsetzt, verliert Bestellbestätigungen.
    ist_newsletter: bool = False

    def treffer(self, signal: str, text: str) -> None:
        """Ein Signal vermerken (einmalig) und sein Gewicht aufschlagen."""
        if signal in self.signals:
            return
        self.signals.append(signal)
        self.reasons.append(text)
        self.score += _GEWICHT.get(signal, 0.0)


def _addr_list(value) -> list[tuple[str, str]]:
    """Payload-Adressfeld → [(name, adresse)]. Der Watcher liefert [{'name','addr'}];
    ältere Payloads (und Handeingaben) auch mal einen rohen String."""
    out: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                addr = str(item.get("addr") or "").strip().lower()
                if addr:
                    out.append((str(item.get("name") or "").strip(), addr))
            elif isinstance(item, str):
                out.extend((n, a) for n, a in _addr_list(item))
    elif isinstance(value, str):
        for m in _EMAIL_RE.finditer(value):
            out.append(("", m.group(0).lower()))
    return out


def _domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else ""


def _header(headers: dict, name: str) -> str:
    """Header-Wert als ein String (Mehrfach-Header werden verbunden)."""
    v = (headers or {}).get(name)
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v or "")


def _wurzel(domain: str) -> str:
    """`bounce.shop.de` → `shop.de`. Grobe Näherung ohne Public-Suffix-Liste: sie würde
    eine gepflegte Datei brauchen, und für den Vergleich zweier Domains derselben Mail
    reicht die Näherung — im Zweifel entsteht ein Verdachtspunkt, kein Urteil."""
    teile = (domain or "").lower().strip(".").split(".")
    if len(teile) <= 2:
        return ".".join(teile)
    if teile[-2] in _ZWEITEILIGE_TLD_LABEL and len(teile[-1]) == 2:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


# --- Schrift und Sichtbarkeit -------------------------------------------------------

def _schriften(text: str) -> set[str]:
    """Welche Schriftsysteme kommen in diesem Text vor (nur Buchstaben zählen)."""
    out: set[str] = set()
    for zeichen in text or "":
        if not zeichen.isalpha():
            continue
        try:
            name = unicodedata.name(zeichen)
        except ValueError:
            continue
        for schrift in ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "HEBREW"):
            if name.startswith(schrift):
                out.add(schrift)
                break
    return out


def _mischt_schriften(text: str) -> bool:
    """Mischt EIN Wort mehrere Schriftsysteme? Das ist die Homoglyph-Masche: ein
    kyrillisches „о" in einem sonst lateinischen Namen sieht identisch aus, ist aber ein
    anderes Zeichen. Wortweise geprüft — eine Mail darf natürlich griechische Zitate
    neben deutschem Text enthalten, ein einzelnes Wort aber nicht beides mischen."""
    for wort in re.split(r"[\s./@_-]+", text or ""):
        if len(wort) > 1 and len(_schriften(wort)) > 1:
            return True
    return False


def _hat_unsichtbare(text: str) -> bool:
    return bool(_UNSICHTBAR.search(text or ""))


# --- Echtheitsprüfung ----------------------------------------------------------------

def _auth_ergebnisse(headers: dict) -> dict[str, str]:
    """SPF/DKIM/DMARC-Ergebnis aus `Authentication-Results` (+ `Received-SPF`).

    Der Header ist Fließtext (`spf=pass smtp.mailfrom=…; dkim=fail …`), deshalb wird
    gesucht, nicht geparst. Nur der eigene Mailserver darf diesen Header schreiben —
    fremde Kopien sind wertlos, aber auch harmlos, weil sie hier nur zu 'pass' führen
    könnten, was wir nie als Freispruch verwenden.
    """
    roh = " ".join([
        _header(headers, "Authentication-Results"),
        _header(headers, "ARC-Authentication-Results"),
        _header(headers, "Received-SPF"),
    ]).lower()
    out: dict[str, str] = {}
    for mech in ("spf", "dkim", "dmarc"):
        m = re.search(rf"\b{mech}\s*=\s*(\w+)", roh)
        if m:
            out[mech] = m.group(1)
    if "spf" not in out:
        m = re.match(r"\s*(pass|fail|softfail|neutral|none|permerror|temperror)",
                     _header(headers, "Received-SPF").lower())
        if m:
            out["spf"] = m.group(1)
    return out


def _signaturdomains(headers: dict) -> list[str]:
    """Domains, die diese Mail per DKIM unterschrieben haben (`d=`).

    Der Watcher zieht sie aus den `DKIM-Signature`-Kopfzeilen; steht dort nichts, wird
    ersatzweise `header.d=` aus `Authentication-Results` gelesen (das schreiben Google
    und Microsoft mit).
    """
    roh = (headers or {}).get("DKIM-Domains")
    if isinstance(roh, list) and roh:
        return [str(d).lower().strip(". ") for d in roh if d]
    if isinstance(roh, str) and roh:
        return [roh.lower().strip(". ")]
    return [m.group(1).lower().strip(". ") for m in re.finditer(
        r"header\.d\s*=\s*([^\s;]+)",
        " ".join([_header(headers, "Authentication-Results"),
                  _header(headers, "ARC-Authentication-Results")]))]


def _pruefe_serverurteil(res: RuleResult, headers: dict, payload: dict) -> None:
    """Was der eigene Mailserver schon entschieden hat.

    An echter Post gemessen ist das der mit Abstand aussagekräftigste Wert im ganzen Kopf —
    und er steht ohnehin da. Ihn zu ignorieren und stattdessen eigene Heuristiken zu
    bauen, hieße, eine fertige Bewertung wegzuwerfen und schlechter nachzubauen.

    `X-Spam-Level` ist eine Sternenkette: ein Stern je Punkt der spamd-Bewertung.
    """
    sterne = len(re.match(r"^\**", _header(headers, "X-Spam-Level").strip()).group(0))
    punkte = None
    m = re.search(r"score=(-?[\d.]+)", _header(headers, "X-Spam-Status"))
    if m:
        try:
            punkte = float(m.group(1))
        except ValueError:
            punkte = None
    if punkte is None and sterne:
        punkte = float(sterne)
    if punkte is not None:
        if punkte >= 10:
            res.treffer("server_spam_hoch", f"Mailserver bewertet sie mit {punkte:g} Punkten")
        elif punkte >= 5:
            res.treffer("server_spam_mittel", f"Mailserver bewertet sie mit {punkte:g} Punkten")

    flag = _header(headers, "X-Spam-Flag").strip().lower()
    status = _header(headers, "X-Spam-Status").strip().lower()
    if flag.startswith("yes") or status.startswith("yes"):
        res.treffer("server_spam_flag", "Mailserver hat die Mail selbst als Spam markiert")
    # Manche Server schreiben ihr Urteil in den Betreff statt in eine Kopfzeile.
    if re.search(r"^\s*(\*{3}\s*)?spam[\s*]*[:\]]|\*{3}\s*spam\s*\*{3}|\[spam\]",
                 str(payload.get("subject") or ""), re.IGNORECASE):
        res.treffer("betreff_spam_markiert", "Betreff ist vom Mailserver als Spam markiert")


def _pruefe_echtheit(res: RuleResult, headers: dict, payload: dict, *,
                     ist_liste: bool, meine_domains: frozenset[str],
                     meine_adressen: frozenset[str]) -> None:
    """SPF/DKIM/DMARC, Ausrichtung, Rückweg, Antwortadresse."""
    auth = _auth_ergebnisse(headers)
    if auth.get("dmarc") in ("fail", "permerror"):
        res.treffer("dmarc_fail", "DMARC fehlgeschlagen")
    if auth.get("spf") in ("fail", "softfail", "permerror"):
        res.treffer("spf_fail", f"SPF {auth['spf']}")
    if auth.get("dkim") in ("fail", "permerror"):
        res.treffer("dkim_fail", "DKIM fehlgeschlagen")
    if not auth and headers:
        # An echter Post gemessen ein starkes Signal: ehrliche Absender kommen über
        # Server, die prüfen und das Ergebnis hinschreiben. Fehlt der Vermerk ganz,
        # ist die Nachricht an dieser Prüfung vorbeigekommen.
        res.treffer("auth_fehlt", "keine Prüfergebnisse (SPF/DKIM/DMARC) im Kopf")
    elif auth.get("dkim") == "none" and not ist_liste:
        # Praktisch jeder ernsthafte Versender signiert heute. Keine Signatur heißt nicht
        # „gefälscht", aber es unterscheidet erstaunlich gut.
        res.treffer("dkim_fehlt", "gar nicht signiert (kein DKIM)")

    # Ausrichtung: eine gültige Signatur sagt nur, dass IRGENDWER unterschrieben hat.
    # Erst der Abgleich mit der Absenderdomain macht daraus eine Aussage über DIESEN
    # Absender. ABER: genau diese Prüfung ist DMARC. Besteht DMARC, IST etwas ausgerichtet
    # (SPF oder DKIM) — dann hier stillhalten. Sonst schlägt die Regel bei jedem
    # Mailinglisten-Beitrag und jedem Google-Workspace-Absender an, die beide
    # regulär mit fremder Domain gegensignieren.
    domains = _signaturdomains(headers)
    if (domains and res.sender_domain and auth.get("dkim") == "pass"
            and auth.get("dmarc") != "pass" and not ist_liste):
        if all(_wurzel(d) != _wurzel(res.sender_domain) for d in domains):
            res.treffer("dkim_nicht_ausgerichtet",
                        f"DKIM unterschrieben von {domains[0]}, nicht von {res.sender_domain}")

    # Die Nachricht gibt eine MEINER Adressen als Absender an, ohne bestandene Prüfung:
    # der älteste Trick überhaupt („von dir an dich"). Eigene Post, die wirklich von hier
    # kommt, besteht die Prüfung — deshalb nur bei fehlender oder gescheiterter.
    if (res.sender_email and ist_meine(res.sender_email, meine_adressen)
            and auth.get("spf") != "pass" and auth.get("dkim") != "pass"):
        res.treffer("absender_bin_ich",
                    "gibt meine eigene Adresse als Absender an, ohne bestandene Prüfung")

    # Roh lesen statt über die Adress-Regex: die kennt `=` nicht als Adresszeichen und
    # würde `SRS0=…=absender.tld=name@meine-domain.de` genau an der Stelle abschneiden,
    # an der die gesuchte Ursprungsdomain steht.
    rp_roh = _header(headers, "Return-Path").strip().strip("<>").strip()
    rp_domain = _domain(rp_roh)
    # Beim Weiterleiten schreibt der eigene Server den Rückweg auf sich selbst um (SRS:
    # `SRS0=…=absender.tld=name@meine-domain.de`). Dann steht dort IMMER die eigene
    # Domain, und ein Vergleich mit dem Absender liefert bei jeder einzelnen Mail einen
    # Treffer — ein Signal, das immer feuert, ist keins.
    if rp_domain and _wurzel(rp_domain) in meine_domains:
        # Weitergeleitete Post: der eigene Server hat den Rückweg auf sich selbst
        # umgeschrieben (SRS). Die ursprüngliche Domain steckt zwar in der Adresse, taugt
        # aber nicht als Signal — an echter Post gemessen ist es die Bounce-Domain des
        # Versanddienstes (`bounces+…-kickstarter@…`), die bei jedem seriösen Newsletter
        # vom Absender abweicht. Hier ist schlicht nichts zu holen.
        rp_domain = ""

    if rp_domain and res.sender_domain and _wurzel(rp_domain) != _wurzel(res.sender_domain):
        # Getrennte Bounce-Domains sind bei großen Versendern üblich (`bounce.shop.de` zu
        # `shop.de`), deshalb zählt nur ein Unterschied jenseits der gemeinsamen Wurzel.
        res.treffer("returnpath_mismatch",
                    f"Rückweg {rp_domain} ≠ Absender {res.sender_domain}")

    # Mailinglisten und Shops leiten Antworten regulär woandershin — dort sagt eine
    # abweichende Antwortadresse nichts.
    reply_to = _addr_list(payload.get("reply_to")) or _addr_list(_header(headers, "Reply-To"))
    if reply_to and res.sender_domain and not ist_liste:
        rt_domain = _domain(reply_to[0][1])
        if rt_domain and _wurzel(rt_domain) != _wurzel(res.sender_domain):
            res.treffer("replyto_fremd",
                        f"Antwort ginge an {rt_domain}, nicht an {res.sender_domain}")





def _pruefe_namenstaeuschung(res: RuleResult, bekannte_domains: frozenset[str]) -> None:
    """Punycode, Schriftmischung, unsichtbare Zeichen, Marke als fremde Subdomain."""
    if any(label.startswith("xn--") for label in res.sender_domain.split(".")):
        res.treffer("punycode_absender",
                    f"Absender-Domain ist umgeschrieben (Punycode): {res.sender_domain}")
    if _mischt_schriften(res.sender_domain) or _mischt_schriften(res.sender_name):
        res.treffer("schriftmischung",
                    "Absender mischt Schriftsysteme (nachgebaute Zeichen, z. B. kyrillisches „о“)")
    if _hat_unsichtbare(res.sender_name) or _hat_unsichtbare(res.sender_email):
        res.treffer("unsichtbare_zeichen", "unsichtbare Zeichen im Absender")

    # Anzeigename gibt eine Adresse/Marke vor, die Absenderadresse hält sie nicht:
    # „DHL Zustellung <noreply@dhl-tracking-de.xyz>". Nur prüfen, wenn der Name selbst
    # eine Adresse oder Domain nennt — freie Namen ("Sparkasse") sind zu unscharf.
    name_domains = {_domain(a) for _, a in _addr_list(res.sender_name)}
    name_domains |= {d.lower() for d in re.findall(r"\b([\w-]+\.[a-z]{2,})\b", res.sender_name or "")}
    name_domains = {d for d in name_domains if d}
    if name_domains and res.sender_domain:
        if all(_wurzel(d) != _wurzel(res.sender_domain) for d in name_domains):
            res.treffer("absender_name_taeuscht",
                        f"Anzeigename nennt {sorted(name_domains)[0]}, "
                        f"gesendet von {res.sender_domain}")

    # Eine mir bekannte Domain steckt IM Absender, ist aber nicht die Absenderdomain:
    # `sparkasse.de.sicherheit-pruefung.top` oder `sparkasse-de.top`. Das ist der Trick,
    # der eine bekannte Marke in die sichtbare Adresse holt, ohne sie zu besitzen.
    if res.sender_domain and _wurzel(res.sender_domain) not in bekannte_domains:
        for bekannt in bekannte_domains:
            if bekannt in FREEMAIL_DOMAINS or len(bekannt) < 6:
                continue
            if bekannt in res.sender_domain or bekannt.replace(".", "-") in res.sender_domain:
                res.treffer("marke_als_subdomain",
                            f"„{bekannt}“ steckt in {res.sender_domain}, gehört aber nicht dazu")
                break


def _pruefe_kopfhygiene(res: RuleResult, headers: dict, payload: dict) -> None:
    """Message-ID, Datum, Zufallsadressen, Betreffs-Tricks, vorgetäuschte Antworten."""
    msgid = str(payload.get("message_id") or "").strip()
    if not msgid:
        res.treffer("msgid_fehlt", "keine Message-ID (normale Mailprogramme setzen immer eine)")
    elif msgid.count("@") != 1 or not re.match(r"^<?[^<>@\s]+@[^<>@\s]+>?$", msgid):
        res.treffer("msgid_kaputt", "Message-ID ist nicht wohlgeformt")

    # Datumsversatz: Versandwerkzeuge setzen gern ein Datum in der Zukunft, damit die Mail
    # im Postfach oben steht.
    gesendet, empfangen = _zeit(payload.get("date")), _zeit(payload.get("timestamp"))
    if gesendet and empfangen:
        versatz = (gesendet - empfangen).total_seconds()
        # Nur die Zukunft zählt: ein vordatierter Kopf ist ein Trick, damit die Nachricht
        # oben im Postfach steht. Ein altes Datum hat dagegen jede nachgelieferte,
        # weitergeleitete oder aus dem Archiv zugestellte Mail — an echter Post gemessen
        # war das reines Rauschen.
        if versatz > 86400:
            res.treffer("datum_versatz", "Sendedatum liegt in der Zukunft")

    lokal = res.sender_email.split("@", 1)[0] if "@" in res.sender_email else ""
    if re.search(r"\d{11,}", lokal) or re.search(r"[0-9a-f]{16,}", lokal) or re.match(r"^\d{8,}$", lokal):
        res.treffer("absender_zufaellig", "Absenderadresse sieht maschinell erzeugt aus")
    # `yffebnj@…` — gewürfelte Buchstaben ohne einen einzigen Vokal. Kein Mensch vergibt
    # so eine Adresse, aber ein Skript, das für jede Sendung eine neue braucht.
    if re.fullmatch(r"[b-df-hj-np-tv-xz]{6,}", lokal, re.IGNORECASE):
        res.treffer("absender_vokallos", "Absenderadresse ohne jeden Vokal (gewürfelt)")

    betreff = str(payload.get("subject") or "")
    buchstaben = [z for z in betreff if z.isalpha()]
    if len(buchstaben) >= 12 and sum(1 for z in buchstaben if z.isupper()) / len(buchstaben) > 0.7:
        res.treffer("betreff_geschrien", "Betreff komplett in Großbuchstaben")
    if re.search(r"!{3,}", betreff):
        res.treffer("betreff_geschrien", "Betreff mit mehrfachen Ausrufezeichen")
    # G.e.w.i.n.n / G-e-w-i-n-n: Buchstaben werden gestreckt, um Wortfilter zu umgehen.
    if re.search(r"\b\w(?:[.\-_*]\w){4,}\b", betreff):
        res.treffer("betreff_gestreckt", "Betreff mit gestreckten Wörtern (Filter-Umgehung)")
    if _hat_unsichtbare(betreff):
        res.treffer("unsichtbare_zeichen", "unsichtbare Zeichen im Betreff")

    # „Re:" ohne jeden Bezug ist eine vorgetäuschte Antwort auf ein Gespräch, das es nie gab.
    if re.match(r"^\s*(re|aw|antw|fwd?|wg)\s*:", betreff, re.IGNORECASE):
        if not _header(headers, "In-Reply-To").strip() and not _header(headers, "References").strip():
            res.treffer("fake_antwort", "„Re:“ ohne Bezug auf eine frühere Nachricht")

    prio = (_header(headers, "X-Priority") + " " + _header(headers, "X-MSMail-Priority")
            + " " + _header(headers, "Importance")).lower()
    if re.search(r"\b(1|2|high|urgent|hoch)\b", prio):
        res.treffer("aufgesetzte_dringlichkeit", "als besonders dringend markiert")

    if headers and int((headers or {}).get("Received-Count") or 0) == 1:
        res.treffer("received_kette_kurz", "nur eine Zustellstation im Kopf")


def _zeit(roh) -> dt.datetime | None:
    if not roh:
        return None
    try:
        wert = dt.datetime.fromisoformat(str(roh))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=dt.timezone.utc)


# --- Links ---------------------------------------------------------------------------

def _wirt(href: str) -> str:
    try:
        return (urlsplit(href).hostname or "").lower()
    except ValueError:
        return ""


def _pruefe_links(res: RuleResult, payload: dict, *, ist_liste: bool,
                  bekannte_domains: frozenset[str]) -> None:
    """Linkziele gegen ihren sichtbaren Text und gegen sich selbst prüfen.

    Der Unterschied zwischen Text und Ziel ist der verlässlichste Phishing-Anzeiger
    überhaupt — er kommt in ehrlicher Post praktisch nicht vor, denn wer „paypal.de“
    schreibt und woandershin verlinkt, tut das nicht versehentlich.
    """
    links = payload.get("links")
    if not isinstance(links, list):
        return
    for eintrag in links[:40]:
        if not isinstance(eintrag, dict):
            continue
        href = str(eintrag.get("href") or "")
        text = str(eintrag.get("text") or "")
        wirt = _wirt(href)
        if not wirt:
            continue

        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", wirt):
            res.treffer("link_ip_adresse", f"Link führt auf eine nackte IP ({wirt})")
        if wirt.startswith("xn--") or ".xn--" in wirt:
            res.treffer("link_punycode", f"Linkziel ist umgeschrieben (Punycode): {wirt}")
        # `https://paypal.de@boese.tld/` — alles vor dem @ ist Zierde, das Ziel ist boese.tld.
        vor_wirt = href.split("://", 1)[-1].split("/", 1)[0]
        if "@" in vor_wirt:
            res.treffer("link_at_trick",
                        f"Link tarnt sein Ziel mit einem @ ({vor_wirt[:60]})")
        if wirt in _KUERZER:
            res.treffer("link_kuerzungsdienst", f"Ziel hinter einem Kürzungsdienst ({wirt})")
        tld = wirt.rsplit(".", 1)[-1] if "." in wirt else ""
        if tld in _BILLIG_TLDS:
            res.treffer("link_billig_tld", f"Linkziel auf .{tld}")

        # Der sichtbare Text nennt selbst eine Domain — führt der Link woandershin?
        gezeigt = _gezeigte_domain(text)
        if not gezeigt or _wurzel(gezeigt) == _wurzel(wirt) or _marke(gezeigt) == _marke(wirt):
            # Der Markenvergleich fängt den ehrlichen Fall ab: „obi.de“ verlinkt nach
            # `email.obi.com` — verschiedene Domains, dieselbe Firma. Ohne ihn schlägt
            # die Regel bei jedem Versandhaus an, das eine eigene Mail-Domain betreibt.
            continue
        if _wurzel(wirt) == _wurzel(res.sender_domain):
            # `emails.kickstarter.com` bei Absender `kickstarter.com`: derselbe Hof.
            continue
        if _wurzel(wirt) in _TRACKING_DOMAINS or _wurzel(wirt) in bekannte_domains:
            # Klick-Zähler (Klaviyo, Mailjet, Sendgrid …) leiten in JEDEM Newsletter jeden
            # Link um — das ist der Normalfall, nicht die Ausnahme. Und ein Ziel, mit dem
            # ich ohnehin zu tun habe, verbirgt nichts.
            continue
        if ist_liste:
            # In einer Liste bleibt die Umleitung erklärbar, auch wenn der Dienst
            # unbekannt ist — als Verdachtspunkt reicht sie, als Urteil nicht.
            res.treffer("link_text_umgeleitet",
                        f"Link zeigt „{gezeigt}“, führt über {wirt}")
        else:
            res.treffer("link_text_taeuscht",
                        f"Link zeigt „{gezeigt}“, führt aber nach {wirt}")


def _marke(domain: str) -> str:
    """Das Markenlabel einer Domain: `email.obi.com` → `obi`, `obi.de` → `obi`.

    Nur zum Entschärfen gedacht — zwei Domains derselben Firma unter verschiedenen
    Endungen sollen nicht als Täuschung gelten. Als Verdachtsgrund taugt die Gleichheit
    nicht, denn `obi-versand.top` trüge dieselbe Marke.
    """
    wurzel = _wurzel(domain)
    return wurzel.split(".", 1)[0] if wurzel else ""


def _gezeigte_domain(text: str) -> str:
    """Die Domain, die der sichtbare Linktext behauptet — oder ''.

    Nur wenn der Text WIE eine Adresse aussieht. „Hier klicken“ behauptet nichts, und
    ein Fließtext mit einem Punkt darin ist keine Domainangabe.
    """
    text = (text or "").strip().strip("<>()[]").rstrip(".,;:")
    if not text or " " in text:
        return ""
    if text.lower().startswith(("http://", "https://")):
        return _wirt(text)
    if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/.*)?", text, re.IGNORECASE):
        return text.split("/", 1)[0].lower().removeprefix("www.")
    return ""


# --- Anhänge -------------------------------------------------------------------------

# Öffentliche Sammelpostfächer. Bewusst OHNE `vorstand`, `buchhaltung`, `rechnung`,
# `bestellung` — die bekommen sehr wohl Verträge.
_ROLLEN_LOCALPARTS = frozenset({
    "info", "fragen", "kontakt", "contact", "hallo", "hello", "moin", "mail", "email",
    "office", "team", "service", "support", "help", "hilfe", "webmaster", "postmaster",
    "abuse", "noc", "presse", "press", "media", "marketing", "verein", "vorstandschaft",
    "list", "liste", "lists", "newsletter", "no-reply", "noreply", "mailer",
})
# Wörter, die einen persönlichen Geschäftsvorgang behaupten.
_GESCHAEFT_RE = re.compile(
    r"\b(rechnung|zahlung(sinformation)?|mahnung|vertrag|kündigung|kuendigung|abo|"
    r"abonnement|kundenkonto|kundennummer|lastschrift|gebühr|gebuehr|verlängerung|"
    r"verlaengerung|zahlungsziel|faktura|invoice|payment|billing|überweisung|ueberweisung|"
    r"forderung|inkasso|zahlungsaufforderung|handlungsbedarf)\b", re.IGNORECASE)


def _pruefe_rollenadresse(res: RuleResult, subject: str, body: str, *,
                          meine_adressen: frozenset[str],
                          geschaeftsfreie_domains: frozenset[str] = frozenset()) -> None:
    """Geschäftsvorgang an eine Adresse, die keine Verträge hat.

    Eine Rechnung, ein Vertrag, ein Kundenkonto — das hat immer einen Vertragspartner.
    Zwei Stufen, die sich in ihrer Verlässlichkeit unterscheiden:

    * **Sammelpostfach** (`info@`, `fragen@` …): Anlaufstellen für Fremde, keine
      Vertragsadressen. Nur mittleres Gewicht — kleine Vereine führen ihr halbes Leben
      über info@, und `buchhaltung@` ist ausdrücklich ausgenommen.
    * **Geschäftsfreie Domain**: der Mensch hat hinterlegt, dass über diese Domain
      überhaupt kein Vertragswesen läuft (AppSetting `spam_keine_geschaeftsdomains`).
      Dann zählt jede Adresse darunter, nicht nur die generischen — bei
      `mitmachverein.de` etwa gibt es weder Vorstand noch Buchhaltung, Bestellungen
      laufen woanders. Das ist keine Vermutung, sondern eine Auskunft, und wiegt schwerer.
    """
    if not _GESCHAEFT_RE.search(f"{subject}\n{body[:2000]}"):
        return
    # Bewusst ohne Prüfung, ob es MEINE Adresse ist: Sammelpostfächer werden oft
    # weitergeleitet (`fragen@` einer Community landet im privaten Fach). Wer die Mail
    # im Postfach hat, den betrifft sie — und die Rolle bleibt dieselbe.
    for adresse in res.recipients:
        local, _, domain = adresse.lower().partition("@")
        local = local.split("+", 1)[0]
        if domain and _wurzel(domain) in geschaeftsfreie_domains or domain in geschaeftsfreie_domains:
            res.treffer("geschaeft_an_domain_ohne_geschaeft",
                        f"Geschäftsvorgang an {adresse} — über {domain} läuft "
                        f"nachweislich kein Vertragswesen")
            return
        if local in _ROLLEN_LOCALPARTS:
            res.treffer("geschaeft_an_rollenadresse",
                        f"Geschäftsvorgang an die Sammeladresse {adresse} — "
                        f"solche Adressen haben keine Verträge")
            return


def _pruefe_fassade(res: RuleResult, payload: dict, *, hat_unsubscribe: bool) -> None:
    """Werbe-Layout ohne einen einzigen echten Link.

    Massenversand lebt von Links: Angebot, Impressum, Abmeldung. Eine Mail mit aufwendigem
    HTML, aber ohne `<a href>`, hat die Optik nachgebaut und die Funktion weggelassen —
    der eigentliche Weg zum Opfer läuft dann über Antwort oder Telefon.

    **Nur als Verstärker, nie allein.** An echter Post gemessen (2026-08-18) schlug das
    Muster bei Google Play, OpenAI und eQSL an: große Versender melden per One-Click im
    Kopf ab (RFC 8058) und brauchen im Rumpf keinen Link. Wer seine Echtheitsprüfungen
    besteht, darf sein HTML bauen, wie er will — verdächtig wird die Fassade erst, wenn
    die Technik ohnehin nicht stimmt.
    """
    if not (_FAELSCHUNG & set(res.signals)):
        return
    if "links" not in payload:
        return                     # niemand hat nachgesehen — Schweigen ist keine Aussage
    links = payload.get("links")
    if not isinstance(links, list) or links:
        return                     # es gibt Links → nichts zu sagen
    roh = str(payload.get("body_html") or payload.get("html") or "")
    hat_html = bool(roh) or "<table" in str(payload.get("body_text") or "").lower()
    if hat_unsubscribe:
        res.treffer("abmeldung_nur_behauptet",
                    "Abmeldeweg nur im Kopf behauptet — im Text steht kein einziger Link")
    elif hat_html:
        res.treffer("html_ohne_links", "Werbe-Layout ohne einen einzigen Link")


def _pruefe_anhaenge(res: RuleResult, payload: dict, body: str) -> None:
    anhaenge = payload.get("attachments")
    if not isinstance(anhaenge, list):
        return
    for eintrag in anhaenge[:20]:
        name = str((eintrag or {}).get("filename") or "") if isinstance(eintrag, dict) else ""
        if not name:
            continue
        teile = [t.lower() for t in name.split(".") if t]
        endung = teile[-1] if len(teile) > 1 else ""
        vorletzte = teile[-2] if len(teile) > 2 else ""

        if endung in _WEBSEITEN_ENDUNGEN:
            res.treffer("anhang_webseite", f"Anhang „{name[:60]}“ ist eine Webseite")
        elif endung in _GEFAEHRLICHE_ENDUNGEN:
            res.treffer("anhang_ausfuehrbar", f"Anhang „{name[:60]}“ kann ausgeführt werden")
        # `rechnung.pdf.exe` — die harmlose Endung ist Tarnung, die letzte zählt.
        if vorletzte in _HARMLOS_WIRKEND and endung in _GEFAEHRLICHE_ENDUNGEN:
            res.treffer("anhang_doppelendung",
                        f"Anhang „{name[:60]}“ trägt zwei Endungen")
        # Ein Archiv, dessen Passwort im Text steht, ist für jeden Scanner blind — und
        # genau dafür wird es benutzt.
        if endung in _ARCHIVE and _PASSWORT_WORTE.search(body or ""):
            res.treffer("anhang_archiv_mit_passwort",
                        "passwortgeschütztes Archiv (kein Scanner kann hineinsehen)")


# --- Gesamturteil --------------------------------------------------------------------

def evaluate(payload: dict, *, meine_adressen: frozenset[str] = frozenset(),
             bekannte_domains: frozenset[str] = frozenset(),
             geschaeftsfreie_domains: frozenset[str] = frozenset(),
             body: str = "") -> RuleResult:
    """Mail-Payload → Regelurteil.

    `meine_adressen` sind die eigenen Empfangsadressen/Aliase (klein geschrieben, `*@domain`
    erlaubt). `bekannte_domains` sind die Domains meiner Kontakte — sie machen aus einer
    fremden Marke im Absender ein Signal. `geschaeftsfreie_domains` sind eigene Domains,
    über die kein Vertragswesen läuft — dort ist jede Rechnung eine Behauptung. `body` wird
    nur für den Passwort-Archiv-Fall gelesen. Fehlt eins davon, entfällt genau die
    zugehörige Prüfung; alle anderen greifen.
    """
    res = RuleResult()
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}

    von = _addr_list(payload.get("from"))
    if von:
        res.sender_name, res.sender_email = von[0]
        res.sender_domain = _domain(res.sender_email)

    an = _addr_list(payload.get("to"))
    cc = _addr_list(payload.get("cc"))
    # Zustelladresse steht bei Aliasen oft NUR in diesen Kopfzeilen — `To` trägt dann den
    # Verteiler, nicht mich.
    zustell = [a for _, a in _addr_list(_header(headers, "Delivered-To"))]
    zustell += [a for _, a in _addr_list(_header(headers, "X-Original-To"))]
    zustell += [a for _, a in _addr_list(_header(headers, "Envelope-To"))]
    res.recipients = list(dict.fromkeys([a for _, a in an + cc] + zustell))

    # Mailinglisten-Merkmale zuerst: mehrere Prüfungen müssen bei Listen stillhalten,
    # weil dort reguläre Umwege (Gegensignatur, umgeleitete Antwort) die Regel wären.
    unsubscribe = _header(headers, "List-Unsubscribe").strip()
    list_id = _header(headers, "List-Id").strip()
    bulk = _header(headers, "Precedence").strip().lower() in ("bulk", "list", "junk")
    ist_liste = bool(list_id or unsubscribe)
    meine_domains = frozenset(
        _wurzel(a.split("@", 1)[-1]) for a in meine_adressen if "@" in a)

    _pruefe_serverurteil(res, headers, payload)
    _pruefe_echtheit(res, headers, payload, ist_liste=ist_liste,
                     meine_domains=meine_domains, meine_adressen=meine_adressen)
    _pruefe_namenstaeuschung(res, bekannte_domains)
    _pruefe_kopfhygiene(res, headers, payload)
    _pruefe_links(res, payload, ist_liste=ist_liste, bekannte_domains=bekannte_domains)
    _pruefe_anhaenge(res, payload, body)
    _pruefe_fassade(res, payload, hat_unsubscribe=bool(unsubscribe))
    _pruefe_rollenadresse(res, str(payload.get("subject") or ""), body,
                          meine_adressen=meine_adressen,
                          geschaeftsfreie_domains=geschaeftsfreie_domains)

    if meine_adressen and res.recipients and not any(
            ist_meine(a, meine_adressen) for a in res.recipients):
        res.treffer("bcc_blast", "meine Adresse steht in keiner Empfängerzeile (Blindkopie-Versand)")

    tld = res.sender_domain.rsplit(".", 1)[-1] if "." in res.sender_domain else ""
    if tld in _BILLIG_TLDS:
        res.treffer("billig_tld", f"Absender-Endung .{tld}")

    # --- Massenversand: Newsletter oder Werbemüll -------------------------------
    if (unsubscribe or list_id or bulk) and not unsubscribe:
        res.treffer("kein_unsubscribe_bei_bulk", "Massenversand ohne Abmeldeweg")

    # Sauberer, abmeldbarer Massenversand ist ein Newsletter — der Vermerk hält die
    # Bewertung später davon ab, „viel Werbung" mit „Spam" zu verwechseln.
    technik_sauber = not (_FAELSCHUNG & set(res.signals))
    res.ist_newsletter = bool(unsubscribe) and technik_sauber

    res.score = max(0.0, min(_MAX_SCORE, round(res.score, 3)))
    return res


# Signale, die eine Fälschung anzeigen. Wer eins davon trägt, ist kein „Newsletter" mehr
# und wird auch durch einen bekannten Absender nicht freigesprochen — gerade der bekannte
# Name ist das lohnende Ziel.
_FAELSCHUNG = frozenset({
    "dmarc_fail", "spf_fail", "dkim_fail", "dkim_nicht_ausgerichtet", "returnpath_mismatch",
    # Hat der eigene Server schon Spam gesagt, ist es auch dann keine „bestellte Werbung",
    # wenn ein Abmeldeknopf darunter steht — sonst deckelt die Newsletter-Bremse
    # ausgerechnet den erkannten Müll.
    "server_spam_flag", "server_spam_hoch", "server_spam_mittel", "betreff_spam_markiert",
    "punycode_absender", "schriftmischung", "marke_als_subdomain",
    "link_text_taeuscht", "link_at_trick", "anhang_doppelendung", "unsichtbare_zeichen",
    "absender_bin_ich",
    # Ein behaupteter Abmeldeweg, den es im Rumpf nicht gibt, macht aus Müll keinen
    # Newsletter — sonst deckelte die Newsletter-Bremse genau diese Tarnung.
    "abmeldung_nur_behauptet",
})


def mail_text(payload: dict) -> str:
    """Der Mailtext aus dem Payload — egal, welches Feld ihn trägt.

    Der Watcher liefert `body_text` (Nur-Text-Teil) ODER `body_html_as_text` (aus HTML
    umgewandelt), ältere Quellen ein schlichtes `body`. Wer nur eins davon liest, bekommt
    bei der Hälfte aller Mails einen leeren Text zurück — und merkt es nicht, weil eine
    leere Zeichenkette überall widerspruchsfrei durchläuft.
    """
    for feld in ("body_text", "body", "body_html_as_text"):
        wert = payload.get(feld)
        if wert:
            return str(wert)
    return ""


def ist_faelschungsverdacht(signals) -> bool:
    """Trägt dieses Urteil ein Fälschungs-Signal?"""
    return bool(_FAELSCHUNG & set(signals or ()))


_WORT_RE = re.compile(r"[a-zäöüß]{4,}", re.IGNORECASE)
# Betreffe tragen Füllwörter, die in Spam wie in echter Post gleich häufig sind — sie
# würden die Statistik nur verwässern.
_STOPWORTE = frozenset({
    "eine", "einen", "einer", "eines", "ihre", "ihren", "ihrem", "ihrer", "dein", "deine",
    "oder", "aber", "auch", "noch", "sich", "sind", "wird", "werden", "haben", "wurde",
    "nicht", "mehr", "diese", "diesem", "diesen", "dieser", "dieses", "durch", "unter",
    "über", "sehr", "hier", "dass", "wenn", "dann", "with", "your", "from", "this", "that",
    "have", "will", "please",
})


def features(res: RuleResult, subject: str, *, kontakt_treffer: str = "") -> list[str]:
    """Merkmalschlüssel für das Lernen (`spam_learn`).

    Ein Merkmal ist alles, was sich bei einer künftigen Mail wiedererkennen lässt: Absender,
    Domain, angeschriebener Alias, die technischen Signale und markante Betreff-Wörter.
    Absichtlich grob — feinere Merkmale bräuchten mehr Entscheidungen, als ein Mensch je
    trifft, um statistisch etwas zu bedeuten.
    """
    out: list[str] = []
    if res.sender_email:
        out.append(f"from:{res.sender_email}")
    if res.sender_domain and res.sender_domain not in FREEMAIL_DOMAINS:
        out.append(f"dom:{res.sender_domain}")
    for empf in res.recipients:
        out.append(f"to:{empf}")
    out.extend(f"sig:{s}" for s in res.signals)
    if res.ist_newsletter:
        out.append("sig:newsletter")
    if kontakt_treffer:
        out.append(f"sig:kontakt_{kontakt_treffer}")
    woerter = {
        w.lower() for w in _WORT_RE.findall(subject or "")
        if w.lower() not in _STOPWORTE
    }
    out.extend(f"wort:{w}" for w in sorted(woerter)[:12])
    # Reihenfolge stabil halten, Duplikate raus (Zähler dürfen nicht doppelt steigen).
    return list(dict.fromkeys(out))
