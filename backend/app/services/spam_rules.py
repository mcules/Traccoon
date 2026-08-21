"""Rule based spam signals from addresses, headers, links and attachments, without a model.

Why rules at all when there is a model: the most reliable indicators of spam are technical,
not linguistic. Whether DKIM was signed by a foreign domain, or a link leads somewhere other
than its text claims, is a fact. A language model can only retell it and may get it wrong.
The text decides only where the technique is clean (and that is exactly why good fraud today
is technically clean).

The signals follow what mail filtering and phishing research show to be solid:

* **Authenticity** — SPF/DKIM/DMARC *and their alignment* with the sender domain. A valid
  signature of a foreign domain is the most common way to forge with "DKIM pass".
* **Deception in the name** — punycode/IDN, mixed scripts (a Cyrillic "о" in an otherwise
  Latin word), invisible characters, a known brand as the subdomain of a foreign one.
* **Header hygiene** — a missing or broken Message-ID, a date offset, random addresses,
  faked replies (`Re:` without a reference), manufactured urgency (a SpamAssassin legacy).
* **Links** — text against target, IP addresses instead of names, the `@` trick, shorteners.
* **Attachments** — executable and scriptable formats, double extensions, password protected
  archives (which no scanner can open).

The second job of this module: split out the **features** that are learned from
(`spam_learn`). Verdict and teaching material thus come from one source and cannot drift.

Pure functions, no database, no I/O, so testable without any setup. Everything that needs the
contact store (known domains, matching names) is handed in by the caller.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Addresses at these providers say nothing about the domain: an honest neighbour and a
# fraudster both hang on gmx.de. Domain signals (whitelist as well as suspicion) have to pause
# here, otherwise one rule acquits half the countryside.
FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch",
    "web.de", "t-online.de", "freenet.de", "yahoo.com", "yahoo.de", "outlook.com",
    "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "aol.com",
    "icloud.com", "me.com", "mail.ru", "yandex.ru", "proton.me", "protonmail.com",
    "posteo.de", "mailbox.org", "arcor.de", "online.de", "gmx.com",
})

# Brands worth forging. Whoever writes under one of these names and does not send from the
# matching domain claims a relationship they cannot keep.
#
# This list is the fallback, not the mechanism. The mechanism is `identitaet_ohne_deckung`
# further down: it takes the comparison value out of the mail itself and therefore also works
# for a house nobody wrote down here. The list catches what is left over, the mail that names
# the brand only in its display name and nowhere else ("DPD Logistik
# <info@fremde-firma.example>", 2026-08-18). It stays short on purpose, and ambiguous
# tokens stay out ("ing" would fire on every "Dipl.-Ing.", "ups" on every "Ups!", "wise" and
# "booking" are ordinary English words).
BRANDS = frozenset({
    # Geld
    "n26", "paypal", "klarna", "sparkasse", "volksbank", "raiffeisen", "commerzbank",
    "postbank", "dkb", "comdirect", "targobank", "santander", "revolut", "mastercard",
    "consorsbank", "norisbank", "hypovereinsbank", "unicredit", "apobank", "sparda", "diba",
    "bunq", "amex", "giropay", "paydirekt", "payoneer", "skrill", "moneygram",
    "binance", "coinbase", "bitpanda", "metamask",
    # Pakete
    "dhl", "predecessor", "dpd", "gls", "fedex",
    # Konten und Abos
    "amazon", "apple", "microsoft", "google", "youtube", "netflix", "ebay", "telekom",
    "vodafone", "congstar", "whatsapp", "spotify", "instagram", "facebook", "linkedin",
    "tiktok", "adobe", "dropbox", "github", "playstation", "nintendo", "disney", "dazn",
    "ionos", "strato", "godaddy", "gmx",
    # Handel und Reise
    "zalando", "mediamarkt", "lidl", "aldi", "rewe", "edeka", "ikea", "hornbach", "shein",
    "temu", "alibaba", "aliexpress", "etsy", "airbnb", "expedia", "ryanair", "lufthansa",
    "payback",
    # Security software and public authorities
    "mcafee", "norton", "kaspersky", "avast", "elster",
})

# Hits in DNS blacklists, as SpamAssassin names them in `tests=`. Only the hard lists: the
# similar looking RCVD_IN_DNSWL_* is the opposite, a whitelist.
SPERRLISTEN = frozenset({
    "URIBL_BLACK", "URIBL_RED", "URIBL_ABUSE_SURBL", "URIBL_DBL_SPAM", "URIBL_SBL",
    "URIBL_ZEN_BLOCKED", "RCVD_IN_SBL", "RCVD_IN_SBL_CSS", "RCVD_IN_XBL", "RCVD_IN_PBL",
    "RCVD_IN_BL_SPAMCOP_NET",
})

# Endings that are noticeably overrepresented in bulk mail. No proof, a surcharge and no more.
# a surcharge and no more.
_BILLIG_TLDS = frozenset({
    "xyz", "top", "click", "link", "work", "loan", "bid", "win", "date", "stream",
    "download", "racing", "party", "review", "country", "kim", "gq", "cf", "tk", "ml",
    "zip", "mov", "rest", "cam", "quest", "sbs", "cfd", "lol",
    # Added from the real spam store: germasale.auction, declass.business, espanodeal.lat,
    # endings that show up there and nowhere else in the post.
    "auction", "business", "lat", "beauty", "bond", "makeup", "hair", "skin", "monster",
    "autos", "boats", "yachts", "christmas", "fun", "one", "today", "life", "live",
})

# Shorteners hide the target. On their own that is common (newsletters use them), but combined
# with other signals it is a building block.
# Click counters of the large sending services. They redirect EVERY link in every newsletter:
# the visible text names the brand, the target names the service provider. That is the normal
# case and must not count as deception, otherwise every newsletter is suspicious.
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

# File extensions that can execute or script, or that bring a sign in mask along. `.svg` and
# `.html` stand here because both are now sent as disguised sign in pages; `.iso`/`.img`/`.lnk`
# because they bypass the Windows mark of origin.
_GEFAEHRLICHE_ENDUNGEN = frozenset({
    "exe", "scr", "com", "pif", "cpl", "msi", "msp", "bat", "cmd", "ps1", "vbs", "vbe",
    "js", "jse", "wsf", "wsh", "hta", "jar", "reg", "lnk", "inf", "scf", "iso", "img",
    "vhd", "vhdx", "ace", "docm", "xlsm", "xlsb", "pptm", "dotm", "xlam", "chm",
    "appx", "msix", "apk",
})
# Attached web pages are a known route for rebuilt sign in masks, but Google also attaches its
# terms of service as `.html`. Hence an entry of its own with little weight instead of one pot
# together with executables.
_WEBSEITEN_ENDUNGEN = frozenset({"html", "htm", "xht", "xhtml", "shtml", "svg", "mhtml"})
# Endings that look harmless, the first half of a double extension.
_HARMLOS_WIRKEND = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "jpg", "jpeg", "png",
    "gif", "rtf", "csv", "odt", "zip",
})
_ARCHIVE = frozenset({"zip", "rar", "7z", "gz", "tar", "cab", "arj"})
_PASSWORD_WORDS = re.compile(r"\b(passwor[dt]|kennwor[dt]|entsperrcode|pin\s*:)\b", re.IGNORECASE)

# Invisible characters: they break patterns for filters without a person seeing anything. In a
# real mail they have no business being there (the exception is the emoji joiner, which is why
# U+200D does not stand here).
_UNSICHTBAR = re.compile(r"[​‌‎‏⁠-⁤﻿­]")

_ZWEITEILIGE_TLD_LABEL = ("co", "com", "org", "net", "gov", "ac")

# Weights of the individual signals. The sum is capped; no single rule may decide on its own,
# because the signals differ too much in reliability, and a false alarm costs more here than an
# advertisement that slips through.
_WEIGHT = {
    # The verdict of our own mail server: measured against real post by far the most meaningful
    # value, and it stands in the header anyway.
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
    # Faked bulk mail: the layout of an advertisement, but not a single real link. A mail that
    # presents itself as a newsletter and only claims to have an unsubscribe path has not
    # understood its own form (2026-08-18: domain invoice phishing with
    # 0 a-Tags in aufwendigem HTML).
    "abmeldung_nur_behauptet": 0.35,
    "html_ohne_links": 0.25,
    # A business matter sent to a role: invoices, contracts and customer accounts belong to a
    # person, not to a mailbox like info@ or fragen@. Whoever writes "your invoice" to a public
    # collective address does not know the relationship they claim.
    "geschaeft_an_rollenadresse": 0.35,
    # Not a heuristic but a statement by the person: this domain demonstrably runs no contracts.
    # Accordingly heavier.
    "geschaeft_an_domain_ohne_geschaeft": 0.50,
    # Deception in the name
    "absender_name_taeuscht": 0.30,
    "punycode_absender": 0.35,
    "schriftmischung": 0.40,
    "unsichtbare_zeichen": 0.30,
    "marke_als_subdomain": 0.40,
    # The same trick one field further forward: the brand stands in the display name, which is
    # all most mail programs show. Weighted like the address variant, because it works the same
    # way and is seen more often.
    "marke_im_anzeigenamen": 0.40,
    # The mail names the house it claims to belong to, and nothing about it belongs there.
    # That is not a guess but a comparison the mail supplies itself, which is why it weighs
    # more than the brand list next to it: it works for a house nobody put on a list.
    "identitaet_ohne_deckung": 0.45,
    # An entry in a DNS blacklist is a fact somebody else established, not an estimate of ours.
    # It travels in the header of every mail and used to be read only through the total score.
    "server_blockliste": 0.35,
    # Set from outside (it needs the contact store), but the weight belongs here anyway,
    # otherwise half the scoring would sit somewhere else.
    #
    # Deliberately NOT strong: measured against real post the rule also fires when an
    # acquaintance simply writes from their second address (private instead of work), because
    # the vault never knows all addresses of a person. As one contribution among others it is
    # useful, as a verdict it is not; the memory does the rest after the first feedback.
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
    # A redirect in a newsletter through an unknown service: explicable, but not a matter of
    # course. A point of suspicion, not a verdict.
    "link_text_umgeleitet": 0.10,
    # Attachments
    "anhang_ausfuehrbar": 0.50,
    # An attached web page is a known route for sign in masks, but Google also sends its terms
    # of service as .html. An entry of its own, with little weight.
    "anhang_webseite": 0.15,
    "anhang_doppelendung": 0.45,
    "anhang_archiv_mit_passwort": 0.35,
}

_MAX_SCORE = 1.0


def is_my(address: str, my: frozenset[str]) -> bool:
    """Does the address belong to me? Entries may read `*@my-domain.de`: whoever receives a
    whole domain (catch all with throwaway aliases) cannot enumerate their addresses."""
    address = (address or "").lower()
    if address in my:
        return True
    domain = _domain(address)
    return bool(domain) and f"*@{domain}" in my


@dataclass
class RuleResult:
    """The verdict of the rules about one mail."""

    score: float = 0.0
    # Human readable reasons: they later stand verbatim in the chat card.
    reasons: list[str] = field(default_factory=list)
    # Signal keys (stable, machine readable) for learning and evaluation.
    signals: list[str] = field(default_factory=list)
    sender_email: str = ""
    sender_domain: str = ""
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    # Requested bulk mail (a valid unsubscribe path, clean technique) is a category of its own.
    # A newsletter is NOT spam, and whoever equates the two loses order confirmations.
    is_newsletter: bool = False

    def hits(self, signal: str, text: str) -> None:
        """Record a signal (once) and add its weight."""
        if signal in self.signals:
            return
        self.signals.append(signal)
        self.reasons.append(text)
        self.score += _WEIGHT.get(signal, 0.0)


def _addr_list(value) -> list[tuple[str, str]]:
    """An address field of the payload turned into [(name, address)]. The watcher delivers
    older payloads (and hand written input) sometimes a raw string."""
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
    """A header value as one string (repeated headers are joined)."""
    v = (headers or {}).get(name)
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v or "")


def _root(domain: str) -> str:
    """`bounce.shop.de` becomes `shop.de`. A rough approximation without a public suffix list:
    that would need a maintained file, and for comparing two domains of the same mail the
    approximation is enough. In doubt it yields a point of suspicion, not a verdict."""
    parts = (domain or "").lower().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in _ZWEITEILIGE_TLD_LABEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# --- Script and visibility ----------------------------------------------------------

def _fonts(text: str) -> set[str]:
    """Which writing systems appear in this text (letters only)."""
    out: set[str] = set()
    for chars in text or "":
        if not chars.isalpha():
            continue
        try:
            name = unicodedata.name(chars)
        except ValueError:
            continue
        for font in ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "HEBREW"):
            if name.startswith(font):
                out.add(font)
                break
    return out


def _mixes_fonts(text: str) -> bool:
    """Does ONE word mix several writing systems? That is the homoglyph trick: a Cyrillic "о"
    in an otherwise Latin name looks identical but is a different character. Checked word by
    word, because a mail may of course contain Greek quotations next to German text, while a
    single word must not mix the two."""
    for word in re.split(r"[\s./@_-]+", text or ""):
        if len(word) > 1 and len(_fonts(word)) > 1:
            return True
    return False


def _has_invisible(text: str) -> bool:
    return bool(_UNSICHTBAR.search(text or ""))


# --- Authenticity ---------------------------------------------------------------------

def _auth_results(headers: dict) -> dict[str, str]:
    """SPF/DKIM/DMARC result from `Authentication-Results` (plus `Received-SPF`).

    The header is running text (`spf=pass smtp.mailfrom=…; dkim=fail …`), so it is searched,
    not parsed. Only our own mail server may write this header; foreign copies are worthless
    but also harmless, because they could only lead to 'pass' here, which we never use as an
    acquittal.
    """
    raw = " ".join([
        _header(headers, "Authentication-Results"),
        _header(headers, "ARC-Authentication-Results"),
        _header(headers, "Received-SPF"),
    ]).lower()
    out: dict[str, str] = {}
    for mech in ("spf", "dkim", "dmarc"):
        m = re.search(rf"\b{mech}\s*=\s*(\w+)", raw)
        if m:
            out[mech] = m.group(1)
    if "spf" not in out:
        m = re.match(r"\s*(pass|fail|softfail|neutral|none|permerror|temperror)",
                     _header(headers, "Received-SPF").lower())
        if m:
            out["spf"] = m.group(1)
    return out


def _signaturedomains(headers: dict) -> list[str]:
    """Domains that signed this mail with DKIM (`d=`).

    The watcher pulls them from the `DKIM-Signature` headers; when nothing is there,
    `header.d=` from `Authentication-Results` is read instead (Google and Microsoft write it
    along).
    """
    raw = (headers or {}).get("DKIM-Domains")
    if isinstance(raw, list) and raw:
        return [str(d).lower().strip(". ") for d in raw if d]
    if isinstance(raw, str) and raw:
        return [raw.lower().strip(". ")]
    return [m.group(1).lower().strip(". ") for m in re.finditer(
        r"header\.d\s*=\s*([^\s;]+)",
        " ".join([_header(headers, "Authentication-Results"),
                  _header(headers, "ARC-Authentication-Results")]))]


def _check_serververdict(res: RuleResult, headers: dict, payload: dict) -> None:
    """What our own mail server has already decided.

    Measured against real post this is by far the most meaningful value in the whole header,
    and it is there anyway. Ignoring it and building heuristics of our own would mean throwing
    away a finished assessment and rebuilding it worse.

    `X-Spam-Level` is a chain of stars: one star per point of the spamd score.
    """
    stars = len(re.match(r"^\**", _header(headers, "X-Spam-Level").strip()).group(0))
    points = None
    m = re.search(r"score=(-?[\d.]+)", _header(headers, "X-Spam-Status"))
    if m:
        try:
            points = float(m.group(1))
        except ValueError:
            points = None
    if points is None and stars:
        points = float(stars)
    if points is not None:
        if points >= 10:
            res.hits("server_spam_hoch", f"Mailserver bewertet sie mit {points:g} Punkten")
        elif points >= 5:
            res.hits("server_spam_mittel", f"Mailserver bewertet sie mit {points:g} Punkten")

    # Beside the score the server notes WHICH tests hit. A blacklist entry among them says
    # more than the sum: the total can stay far below the threshold while the link target is
    # demonstrably listed (2026-08-19, N26 phishing: 1.6 of 7 points, URIBL_BLACK among them).
    m = re.search(r"tests=([\w,\s]+)", _header(headers, "X-Spam-Status"))
    hit = sorted(set(re.split(r"[,\s]+", (m.group(1) if m else "").upper())) & SPERRLISTEN)
    if hit:
        res.hits("server_blockliste",
                    f"Mailserver fand einen Eintrag in einer Sperrliste ({hit[0]})")

    flag = _header(headers, "X-Spam-Flag").strip().lower()
    status = _header(headers, "X-Spam-Status").strip().lower()
    if flag.startswith("yes") or status.startswith("yes"):
        res.hits("server_spam_flag", "Mailserver hat die Mail selbst als Spam markiert")
    # Some servers write their verdict into the subject instead of into a header.
    if re.search(r"^\s*(\*{3}\s*)?spam[\s*]*[:\]]|\*{3}\s*spam\s*\*{3}|\[spam\]",
                 str(payload.get("subject") or ""), re.IGNORECASE):
        res.hits("betreff_spam_markiert", "Betreff ist vom Mailserver als Spam markiert")


def _check_authenticity(res: RuleResult, headers: dict, payload: dict, *,
                     is_listing: bool, my_domains: frozenset[str],
                     my_addresses: frozenset[str]) -> None:
    """SPF/DKIM/DMARC, alignment, return path, reply address."""
    auth = _auth_results(headers)
    if auth.get("dmarc") in ("fail", "permerror"):
        res.hits("dmarc_fail", "DMARC fehlgeschlagen")
    if auth.get("spf") in ("fail", "softfail", "permerror"):
        res.hits("spf_fail", f"SPF {auth['spf']}")
    if auth.get("dkim") in ("fail", "permerror"):
        res.hits("dkim_fail", "DKIM fehlgeschlagen")
    if not auth and headers:
        # Measured against real post a strong signal: honest senders come through servers that
        # check and write the result down. When the note is missing entirely, the message got
        # past this check.
        res.hits("auth_fehlt", "keine Prüfergebnisse (SPF/DKIM/DMARC) im Kopf")
    elif auth.get("dkim") == "none" and not is_listing:
        # Practically every serious sender signs today. No signature does not mean "forged",
        # but it separates surprisingly well.
        res.hits("dkim_fehlt", "gar nicht signiert (kein DKIM)")

    # Alignment: a valid signature only says that SOMEBODY signed. Only the comparison with the
    # sender domain turns that into a statement about THIS sender. BUT: exactly that check is
    # DMARC. If DMARC passes, something IS aligned (SPF or DKIM), and then this rule stays
    # quiet. Otherwise it fires on every mailing list post and every Google Workspace sender,
    # both of which regularly countersign with a foreign domain.
    # both of which regularly countersign with a foreign domain.
    domains = _signaturedomains(headers)
    if (domains and res.sender_domain and auth.get("dkim") == "pass"
            and auth.get("dmarc") != "pass" and not is_listing):
        if all(_root(d) != _root(res.sender_domain) for d in domains):
            res.hits("dkim_nicht_ausgerichtet",
                        f"DKIM unterschrieben von {domains[0]}, nicht von {res.sender_domain}")

    # The message names one of MY addresses as the sender without passing a check: the oldest
    # trick there is ("from you to you"). Own post that really comes from here passes the
    # check, which is why this only applies when the check is missing or failed.
    if (res.sender_email and is_my(res.sender_email, my_addresses)
            and auth.get("spf") != "pass" and auth.get("dkim") != "pass"):
        res.hits("absender_bin_ich",
                    "gibt meine eigene Adresse als Absender an, ohne bestandene Prüfung")

    # Read raw instead of through the address regex: that one does not know `=` as an address
    # character and would cut `SRS0=…=sender.tld=name@my-domain.de` exactly where the origin
    # domain we are looking for stands.
    rp_raw = _header(headers, "Return-Path").strip().strip("<>").strip()
    rp_domain = _domain(rp_raw)
    # When forwarding, our own server rewrites the return path onto itself (SRS:
    # `SRS0=…=sender.tld=name@my-domain.de`). Then our own domain ALWAYS stands there, and a
    # comparison with the sender yields a hit on every single mail. A signal that always fires
    # is no signal.
    if rp_domain and _root(rp_domain) in my_domains:
        # Forwarded post: our own server rewrote the return path onto itself (SRS). The
        # original domain is in the address, but it is useless as a signal: measured against
        # real post it is the bounce domain of the sending service
        # (`bounces+…-kickstarter@…`), which differs from the sender in every reputable
        # newsletter. There is simply nothing to gain here.
        rp_domain = ""

    if rp_domain and res.sender_domain and _root(rp_domain) != _root(res.sender_domain):
        # Separate bounce domains are common with large senders (`bounce.shop.de` for
        # `shop.de`), so only a difference beyond the shared root counts.
        res.hits("returnpath_mismatch",
                    f"Rückweg {rp_domain} ≠ Absender {res.sender_domain}")

    # Mailing lists and shops regularly direct replies elsewhere, so a
    # abweichende Antwortadresse nichts.
    reply_to = _addr_list(payload.get("reply_to")) or _addr_list(_header(headers, "Reply-To"))
    if reply_to and res.sender_domain and not is_listing:
        rt_domain = _domain(reply_to[0][1])
        if rt_domain and _root(rt_domain) != _root(res.sender_domain):
            res.hits("replyto_fremd",
                        f"Antwort ginge an {rt_domain}, nicht an {res.sender_domain}")





_TEXT_DOMAIN_RE = re.compile(r"\b(?:https?://|www\.)?([a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9-]{2,63})+)",
                             re.IGNORECASE)


def _named_domains(text: str) -> set[str]:
    """Every domain the mail writes out itself: in an address, a URL or as bare text."""
    out = set()
    for hits in _TEXT_DOMAIN_RE.finditer(text or ""):
        d = hits.group(1).lower().strip(".")
        if "." in d and not d.rsplit(".", 1)[-1].isdigit():
            out.add(d)
    return out


def _identity_without_backing(res: RuleResult, subject: str, body: str, targets: set[str],
                             my_domains: frozenset[str]) -> tuple[str, str]:
    """Which identity does the mail claim, and does anything about it belong to that identity?

    This needs no list of brands, and that is the point. A forged mail names its victim
    itself: in the signature, in the imprint, in "write to us at support@…". So the mail
    delivers the comparison value, and the comparison is a fact — either the sender or a link
    belongs to the named house, or nothing does.

    Three things have to come together, and each one alone would be normal:

    1. The mail **names a foreign domain** in its text (`n26.com`).
    2. It **presents itself under that name**: the name of the domain stands in the display
       name or in the subject ("Support-N26", "… – N26 Sicherheitsteam").
    3. **Nothing leads there**: neither the sender domain nor a single link target carries the
       name.

    An honest mail from that house fails at 3, because it comes from its own domain or at
    least links there. Whoever mentions a partner in passing fails at 2. Only the forgery
    fulfils all three.
    """
    claim = f"{res.sender_name} {subject}".lower()
    words = set(re.findall(r"[a-z0-9]+", claim))
    compact = re.sub(r"[^a-z0-9]", "", claim)
    source = f"{subject}\n{body[:4000]}"
    own = set(my_domains) | {_root(a.rpartition("@")[2]) for a in res.recipients}
    for named in sorted(_named_domains(source)):
        root = _root(named)
        mark = root.split(".", 1)[0]
        if len(mark) < 3 or root in FREEMAIL_DOMAINS or mark in _NO_MARK:
            continue
        # My own domain is not a claimed identity: it stands in the mail because I am the
        # recipient ("delivered to …"), and a forged mail quotes it as readily as an honest one.
        if root in own:
            continue
        # A name is written with a space, a domain with a hyphen or with nothing at all
        # ("Stadtwerke Hintertupfing" / stadtwerke-hintertupfing.de). So compare the letters,
        # not the spelling. As a whole word always, run together only from six letters on:
        # "verti" would otherwise be found inside "konvertieren".
        eng = mark.replace("-", "")
        if mark not in words and not (len(eng) >= 6 and eng in compact):
            continue
        backed = [res.sender_domain, *targets]
        if any(mark in d or eng in d.replace("-", "") for d in backed):
            continue
        return mark, named
    return "", ""


# Words that stand in every second domain and say nothing about who is writing.
_NO_MARK = frozenset({
    "www", "mail", "email", "web", "news", "info", "shop", "service", "support", "kunde",
    "kunden", "portal", "online", "login", "account", "konto", "sicherheit", "security",
    "bank", "post", "cloud", "server", "host", "site", "page", "link", "click", "track",
})


def _mark_without_backing(name: str, domain: str) -> str:
    """The brand from the display name that the sender domain does not carry, or ''.

    Contained is enough, not equal: `amazonses.com` sends for Amazon and
    `sparkasse-musterstadt.de` is one. Only a domain that does not carry the name at all lies.
    """
    if not name or not domain:
        return ""
    for word in re.findall(r"[a-z0-9]{2,}", name.lower()):
        if word in BRANDS and word not in domain.lower():
            return word
    return ""


def _check_namespoofing(res: RuleResult, known_domains: frozenset[str]) -> None:
    """Punycode, mixed scripts, invisible characters, a brand as a foreign subdomain."""
    if any(label.startswith("xn--") for label in res.sender_domain.split(".")):
        res.hits("punycode_absender",
                    f"Absender-Domain ist umgeschrieben (Punycode): {res.sender_domain}")
    if _mixes_fonts(res.sender_domain) or _mixes_fonts(res.sender_name):
        res.hits("schriftmischung",
                    "Absender mischt Schriftsysteme (nachgebaute Zeichen, z. B. kyrillisches „о“)")
    if _has_invisible(res.sender_name) or _has_invisible(res.sender_email):
        res.hits("unsichtbare_zeichen", "unsichtbare Zeichen im Absender")

    # The display name claims an address or brand the sender address does not keep: "DHL
    # delivery <noreply@dhl-tracking-de.xyz>". Only checked when the name itself names an
    # address or a domain: free names ("Sparkasse") are too vague.
    name_domains = {_domain(a) for _, a in _addr_list(res.sender_name)}
    name_domains |= {d.lower() for d in re.findall(r"\b([\w-]+\.[a-z]{2,})\b", res.sender_name or "")}
    name_domains = {d for d in name_domains if d}
    if name_domains and res.sender_domain:
        if all(_root(d) != _root(res.sender_domain) for d in name_domains):
            res.hits("absender_name_taeuscht",
                        f"Anzeigename nennt {sorted(name_domains)[0]}, "
                        f"gesendet von {res.sender_domain}")

    # A brand in the display name that the address does not keep: "Support-N26
    # <support@fremde-firma.example>". Mail programs show the name and hide the address, which
    # is why this is the cheapest disguise there is, and why it needs no technical flaw: the
    # sender owns their throwaway domain and signs it properly.
    mark = _mark_without_backing(res.sender_name, res.sender_domain)
    if mark:
        res.hits("marke_im_anzeigenamen",
                    f"Anzeigename nennt „{mark}“, gesendet von {res.sender_domain}")

    # A domain known to me is IN the sender but is not the sender domain:
    # `sparkasse.de.sicherheit-pruefung.top` or `sparkasse-de.top`. That is the trick which
    # brings a known brand into the visible address without owning it.
    if res.sender_domain and _root(res.sender_domain) not in known_domains:
        for known in known_domains:
            if known in FREEMAIL_DOMAINS or len(known) < 6:
                continue
            if known in res.sender_domain or known.replace(".", "-") in res.sender_domain:
                res.hits("marke_als_subdomain",
                            f"„{known}“ steckt in {res.sender_domain}, gehört aber nicht dazu")
                break


def _check_headerhygiene(res: RuleResult, headers: dict, payload: dict) -> None:
    """Message id, date, random addresses, subject tricks, faked replies."""
    msgid = str(payload.get("message_id") or "").strip()
    if not msgid:
        res.hits("msgid_fehlt", "keine Message-ID (normale Mailprogramme setzen immer eine)")
    elif msgid.count("@") != 1 or not re.match(r"^<?[^<>@\s]+@[^<>@\s]+>?$", msgid):
        res.hits("msgid_kaputt", "Message-ID ist nicht wohlgeformt")

    # Date offset: sending tools like to set a date in the future so the mail stands at the top
    # of the mailbox.
    sent, received = _ts(payload.get("date")), _ts(payload.get("timestamp"))
    if sent and received:
        offset = (sent - received).total_seconds()
        # Only the future counts: a post dated header is a trick to put the message at the top
        # of the mailbox. An old date on the other hand belongs to every mail delivered late,
        # forwarded or restored from an archive, and measured against real post that was pure
        # noise.
        if offset > 86400:
            res.hits("datum_versatz", "Sendedatum liegt in der Zukunft")

    local = res.sender_email.split("@", 1)[0] if "@" in res.sender_email else ""
    if re.search(r"\d{11,}", local) or re.search(r"[0-9a-f]{16,}", local) or re.match(r"^\d{8,}$", local):
        res.hits("absender_zufaellig", "Absenderadresse sieht maschinell erzeugt aus")
    # `yffebnj@…`, thrown together letters without a single vowel. No person hands out such an
    # address, but a script that needs a new one for every send does.
    if re.fullmatch(r"[b-df-hj-np-tv-xz]{6,}", local, re.IGNORECASE):
        res.hits("absender_vokallos", "Absenderadresse ohne jeden Vokal (gewürfelt)")

    subject = str(payload.get("subject") or "")
    letters = [z for z in subject if z.isalpha()]
    if len(letters) >= 12 and sum(1 for z in letters if z.isupper()) / len(letters) > 0.7:
        res.hits("betreff_geschrien", "Betreff komplett in Großbuchstaben")
    if re.search(r"!{3,}", subject):
        res.hits("betreff_geschrien", "Betreff mit mehrfachen Ausrufezeichen")
    # G.e.w.i.n.n / G-e-w-i-n-n: letters are stretched to get past word filters.
    if re.search(r"\b\w(?:[.\-_*]\w){4,}\b", subject):
        res.hits("betreff_gestreckt", "Betreff mit gestreckten Wörtern (Filter-Umgehung)")
    if _has_invisible(subject):
        res.hits("unsichtbare_zeichen", "unsichtbare Zeichen im Betreff")

    # "Re:" without any reference is a faked reply to a conversation that never happened.
    if re.match(r"^\s*(re|aw|antw|fwd?|wg)\s*:", subject, re.IGNORECASE):
        if not _header(headers, "In-Reply-To").strip() and not _header(headers, "References").strip():
            res.hits("fake_antwort", "„Re:“ ohne Bezug auf eine frühere Nachricht")

    prio = (_header(headers, "X-Priority") + " " + _header(headers, "X-MSMail-Priority")
            + " " + _header(headers, "Importance")).lower()
    if re.search(r"\b(1|2|high|urgent|hoch)\b", prio):
        res.hits("aufgesetzte_dringlichkeit", "als besonders dringend markiert")

    if headers and int((headers or {}).get("Received-Count") or 0) == 1:
        res.hits("received_kette_kurz", "nur eine Zustellstation im Kopf")


def _ts(raw) -> dt.datetime | None:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


# --- Links ---------------------------------------------------------------------------

def _host(href: str) -> str:
    try:
        return (urlsplit(href).hostname or "").lower()
    except ValueError:
        return ""


def _check_links(res: RuleResult, payload: dict, *, is_listing: bool,
                  known_domains: frozenset[str]) -> None:
    """Check link targets against their visible text and against themselves.

    The difference between text and target is the most reliable indicator of phishing there is:
    it practically never occurs in honest post, because whoever writes "paypal.de" and links
    somewhere else does not do that by accident.
    """
    links = payload.get("links")
    if not isinstance(links, list):
        return
    for entry in links[:40]:
        if not isinstance(entry, dict):
            continue
        href = str(entry.get("href") or "")
        text = str(entry.get("text") or "")
        host = _host(href)
        if not host:
            continue

        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            res.hits("link_ip_adresse", f"Link führt auf eine nackte IP ({host})")
        if host.startswith("xn--") or ".xn--" in host:
            res.hits("link_punycode", f"Linkziel ist umgeschrieben (Punycode): {host}")
        # `https://paypal.de@boese.tld/`: everything before the @ is decoration, the target is boese.tld.
        before_host = href.split("://", 1)[-1].split("/", 1)[0]
        if "@" in before_host:
            res.hits("link_at_trick",
                        f"Link tarnt sein Ziel mit einem @ ({before_host[:60]})")
        if host in _KUERZER:
            res.hits("link_kuerzungsdienst", f"Ziel hinter einem Kürzungsdienst ({host})")
        tld = host.rsplit(".", 1)[-1] if "." in host else ""
        if tld in _BILLIG_TLDS:
            res.hits("link_billig_tld", f"Linkziel auf .{tld}")

        # The visible text names a domain itself: does the link lead somewhere else?
        shown = _shown_domain(text)
        if not shown or _root(shown) == _root(host) or _mark(shown) == _mark(host):
            # The brand comparison catches the honest case: "obi.de" links to `email.obi.com`,
            # different domains, same company. Without it the rule fires on every mail order
            # house that runs a mail domain of its own.
            continue
        if _root(host) == _root(res.sender_domain):
            # `emails.kickstarter.com` with sender `kickstarter.com`: the same yard.
            continue
        if _root(host) in _TRACKING_DOMAINS or _root(host) in known_domains:
            # Click counters (Klaviyo, Mailjet, Sendgrid …) redirect every link in EVERY
            # newsletter: that is the normal case, not the exception. And a target that
            # ich ohnehin zu tun habe, verbirgt nichts.
            continue
        if is_listing:
            # Inside a list the redirect stays explicable even when the service is unknown:
            # enough for a point of suspicion, not for a verdict.
            res.hits("link_text_umgeleitet",
                        f"Link zeigt „{shown}“, führt über {host}")
        else:
            res.hits("link_text_taeuscht",
                        f"Link zeigt „{shown}“, führt aber nach {host}")


def _mark(domain: str) -> str:
    """The brand label of a domain: `email.obi.com` becomes `obi`, `obi.de` becomes `obi`.

    Meant only for defusing: two domains of the same company under different endings should
    not count as deception. As a reason for suspicion the equality is useless, because
    `obi-versand.top` would carry the same brand.
    """
    root = _root(domain)
    return root.split(".", 1)[0] if root else ""


def _shown_domain(text: str) -> str:
    """The domain the visible link text claims, or ''.

    Only when the text looks LIKE an address. "Click here" claims nothing, and running text
    with a dot in it is not a statement of a domain.
    """
    text = (text or "").strip().strip("<>()[]").rstrip(".,;:")
    if not text or " " in text:
        return ""
    if text.lower().startswith(("http://", "https://")):
        return _host(text)
    if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/.*)?", text, re.IGNORECASE):
        return text.split("/", 1)[0].lower().removeprefix("www.")
    return ""


# --- Attachments ---------------------------------------------------------------------

# Public collective mailboxes. Deliberately WITHOUT `vorstand`, `buchhaltung`, `rechnung`,
# `bestellung`: those really do receive contracts.
_ROLES_LOCALPARTS = frozenset({
    "info", "fragen", "kontakt", "contact", "hallo", "hello", "moin", "mail", "email",
    "office", "team", "service", "support", "help", "hilfe", "webmaster", "postmaster",
    "abuse", "noc", "presse", "press", "media", "marketing", "verein", "vorstandschaft",
    "list", "liste", "lists", "newsletter", "no-reply", "noreply", "mailer",
})
# Words that claim a personal business matter.
_BUSINESS_RE = re.compile(
    r"\b(rechnung|zahlung(sinformation)?|mahnung|vertrag|kündigung|kuendigung|abo|"
    r"abonnement|kundenkonto|kundennummer|lastschrift|gebühr|gebuehr|verlängerung|"
    r"verlaengerung|zahlungsziel|faktura|invoice|payment|billing|überweisung|ueberweisung|"
    r"forderung|inkasso|zahlungsaufforderung|handlungsbedarf)\b", re.IGNORECASE)


def _check_roleaddress(res: RuleResult, subject: str, body: str, *,
                          my_addresses: frozenset[str],
                          nonbusiness_domains: frozenset[str] = frozenset()) -> None:
    """A business matter to an address that has no contracts.

    An invoice, a contract, a customer account: there is always a contracting party. Two
    levels that differ in their reliability:

    * **Collective mailbox** (`info@`, `fragen@` …): points of contact for strangers, not
      contract addresses. Only medium weight, because small clubs run half their life through
      info@, and `buchhaltung@` is explicitly excluded.
    * **Domain without business**: the person recorded that no contracts run over this domain
      at all (AppSetting `spam_keine_geschaeftsdomains`). Then every address under it counts,
      not only the generic ones: at `mitmachverein.de` for instance there is neither a board
      nor an accounting department, and orders run elsewhere. That is not a guess but a
      statement, and it weighs more.
    """
    if not _BUSINESS_RE.search(f"{subject}\n{body[:2000]}"):
        return
    # Deliberately without checking whether it is MY address: collective mailboxes are often
    # forwarded (the `fragen@` of a community lands in a private box). Whoever has the mail in
    # their box is concerned by it, and the role stays the same.
    for address in res.recipients:
        local, _, domain = address.lower().partition("@")
        local = local.split("+", 1)[0]
        if domain and _root(domain) in nonbusiness_domains or domain in nonbusiness_domains:
            res.hits("geschaeft_an_domain_ohne_geschaeft",
                        f"Geschäftsvorgang an {address} — über {domain} läuft "
                        f"nachweislich kein Vertragswesen")
            return
        if local in _ROLES_LOCALPARTS:
            res.hits("geschaeft_an_rollenadresse",
                        f"Geschäftsvorgang an die Sammeladresse {address} — "
                        f"solche Adressen haben keine Verträge")
            return


def _check_facade(res: RuleResult, payload: dict, *, has_unsubscribe: bool) -> None:
    """Advertising layout without a single real link.

    Bulk mail lives on links: the offer, the imprint, the unsubscribe. A mail with elaborate
    HTML but without an `<a href>` has rebuilt the look and left out the function, and the real
    path to the victim then runs through a reply or a phone call.

    **Only as an amplifier, never alone.** Measured against real post (2026-08-18) the pattern
    fired on Google Play, OpenAI and eQSL: large senders unsubscribe by one click in the header
    (RFC 8058) and need no link in the body. Whoever passes their authenticity checks may build
    their HTML as they like; the facade only becomes suspicious once the technique is off
    anyway.
    """
    if not (_FAELSCHUNG & set(res.signals)):
        return
    if "links" not in payload:
        return                     # nobody looked, and silence is not a statement
    links = payload.get("links")
    if not isinstance(links, list) or links:
        return                     # there are links, so nothing to say
    raw = str(payload.get("body_html") or payload.get("html") or "")
    has_html = bool(raw) or "<table" in str(payload.get("body_text") or "").lower()
    if has_unsubscribe:
        res.hits("abmeldung_nur_behauptet",
                    "Abmeldeweg nur im Kopf behauptet — im Text steht kein einziger Link")
    elif has_html:
        res.hits("html_ohne_links", "Werbe-Layout ohne einen einzigen Link")


def _check_attachments(res: RuleResult, payload: dict, body: str) -> None:
    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        return
    for entry in attachments[:20]:
        name = str((entry or {}).get("filename") or "") if isinstance(entry, dict) else ""
        if not name:
            continue
        parts = [t.lower() for t in name.split(".") if t]
        extension = parts[-1] if len(parts) > 1 else ""
        penultimate = parts[-2] if len(parts) > 2 else ""

        if extension in _WEBSEITEN_ENDUNGEN:
            res.hits("anhang_webseite", f"Anhang „{name[:60]}“ ist eine Webseite")
        elif extension in _GEFAEHRLICHE_ENDUNGEN:
            res.hits("anhang_ausfuehrbar", f"Anhang „{name[:60]}“ kann ausgeführt werden")
        # `rechnung.pdf.exe`: the harmless ending is camouflage, the last one counts.
        if penultimate in _HARMLOS_WIRKEND and extension in _GEFAEHRLICHE_ENDUNGEN:
            res.hits("anhang_doppelendung",
                        f"Anhang „{name[:60]}“ trägt zwei Endungen")
        # An archive whose password stands in the text is blind to every scanner, and that is
        # exactly what it is used for.
        if extension in _ARCHIVE and _PASSWORD_WORDS.search(body or ""):
            res.hits("anhang_archiv_mit_passwort",
                        "passwortgeschütztes Archiv (kein Scanner kann hineinsehen)")


# --- Gesamturteil --------------------------------------------------------------------

def evaluate(payload: dict, *, my_addresses: frozenset[str] = frozenset(),
             known_domains: frozenset[str] = frozenset(),
             nonbusiness_domains: frozenset[str] = frozenset(),
             body: str = "") -> RuleResult:
    """Mail-Payload → Regelurteil.

    `meine_adressen` are our own receiving addresses and aliases (lower case, `*@domain`
    allowed). `bekannte_domains` are the domains of my contacts: they turn a foreign brand in
    the sender into a signal. `geschaeftsfreie_domains` are own domains over which no contracts
    run, where every invoice is a claim. `body` is the text of the mail: without it the business
    matter and the password protected archive are not recognised, because both stand in the
    body and not in the header. If one of them is missing, exactly the corresponding check falls away and all others
    still apply.
    """
    res = RuleResult()
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}

    von = _addr_list(payload.get("from"))
    if von:
        res.sender_name, res.sender_email = von[0]
        res.sender_domain = _domain(res.sender_email)

    an = _addr_list(payload.get("to"))
    cc = _addr_list(payload.get("cc"))
    # With aliases the delivery address often stands ONLY in these headers: `To` then carries
    # the distribution list, not me.
    delivery = [a for _, a in _addr_list(_header(headers, "Delivered-To"))]
    delivery += [a for _, a in _addr_list(_header(headers, "X-Original-To"))]
    delivery += [a for _, a in _addr_list(_header(headers, "Envelope-To"))]
    res.recipients = list(dict.fromkeys([a for _, a in an + cc] + delivery))

    # Mailing list characteristics first: several checks have to stay quiet on lists, because
    # regular detours there (countersigning, redirected replies) would otherwise be the rule.
    unsubscribe = _header(headers, "List-Unsubscribe").strip()
    list_id = _header(headers, "List-Id").strip()
    bulk = _header(headers, "Precedence").strip().lower() in ("bulk", "list", "junk")
    is_listing = bool(list_id or unsubscribe)
    my_domains = frozenset(
        _root(a.split("@", 1)[-1]) for a in my_addresses if "@" in a)

    _check_serververdict(res, headers, payload)
    _check_authenticity(res, headers, payload, is_listing=is_listing,
                     my_domains=my_domains, my_addresses=my_addresses)
    _check_namespoofing(res, known_domains)
    _check_headerhygiene(res, headers, payload)
    _check_links(res, payload, is_listing=is_listing, known_domains=known_domains)
    targets = {_host(l.get("href")) for l in (payload.get("links") or [])
             if isinstance(l, dict) and l.get("href")}
    mark, named = _identity_without_backing(res, str(payload.get("subject") or ""), body,
                                              {z for z in targets if z}, my_domains)
    if mark:
        res.hits("identitaet_ohne_deckung",
                    f"gibt sich als „{mark}“ aus und nennt {named}, aber weder der Absender "
                    f"({res.sender_domain}) noch ein Link führt dorthin")
    _check_attachments(res, payload, body)
    _check_facade(res, payload, has_unsubscribe=bool(unsubscribe))
    _check_roleaddress(res, str(payload.get("subject") or ""), body,
                          my_addresses=my_addresses,
                          nonbusiness_domains=nonbusiness_domains)

    if my_addresses and res.recipients and not any(
            is_my(a, my_addresses) for a in res.recipients):
        res.hits("bcc_blast", "meine Adresse steht in keiner Empfängerzeile (Blindkopie-Versand)")

    tld = res.sender_domain.rsplit(".", 1)[-1] if "." in res.sender_domain else ""
    if tld in _BILLIG_TLDS:
        res.hits("billig_tld", f"Absender-Endung .{tld}")

    # --- Bulk mail: newsletter or advertising junk ------------------------------
    if (unsubscribe or list_id or bulk) and not unsubscribe:
        res.hits("kein_unsubscribe_bei_bulk", "Massenversand ohne Abmeldeweg")

    # Clean bulk mail one can unsubscribe from is a newsletter, and the note keeps the scoring
    # from confusing "a lot of advertising" with "spam" later.
    technical_clean = not (_FAELSCHUNG & set(res.signals))
    res.is_newsletter = bool(unsubscribe) and technical_clean

    res.score = max(0.0, min(_MAX_SCORE, round(res.score, 3)))
    return res


# Signals that indicate a forgery. Whatever carries one of them is no longer a "newsletter" and
# is not acquitted by a known sender either: the known name is precisely the worthwhile target.
_FAELSCHUNG = frozenset({
    "dmarc_fail", "spf_fail", "dkim_fail", "dkim_nicht_ausgerichtet", "returnpath_mismatch",
    # If our own server has already said spam, it is not "requested advertising" even when an
    # unsubscribe button stands underneath, otherwise the newsletter brake caps exactly the junk
    # that was recognised.
    "server_spam_flag", "server_spam_hoch", "server_spam_mittel", "betreff_spam_markiert",
    "punycode_absender", "schriftmischung", "marke_als_subdomain", "marke_im_anzeigenamen",
    "identitaet_ohne_deckung",
    # A listed link target is not "requested advertising" either, however tidy the unsubscribe
    # footer underneath looks.
    "server_blockliste",
    "link_text_taeuscht", "link_at_trick", "anhang_doppelendung", "unsichtbare_zeichen",
    "absender_bin_ich",
    # A claimed unsubscribe path that does not exist in the body does not turn junk into a
    # newsletter, otherwise the newsletter brake would cap exactly that camouflage.
    "abmeldung_nur_behauptet",
})


def mail_text(payload: dict) -> str:
    """The mail text from the payload, whichever field carries it.

    The watcher delivers `body_text` (the plain text part) OR `body_html_as_text` (converted
    from HTML), older sources a plain `body`. Whoever reads only one of them gets an empty text
    for half of all mails, and does not notice, because an empty string passes everywhere
    without contradiction.
    """
    for field in ("body_text", "body", "body_html_as_text"):
        value = payload.get(field)
        if value:
            return str(value)
    return ""


def is_forgery_suspicion(signals) -> bool:
    """Does this verdict carry a signal of forgery?"""
    return bool(_FAELSCHUNG & set(signals or ()))


_WORD_RE = re.compile(r"[a-zäöüß]{4,}", re.IGNORECASE)
# Subjects carry filler words that are equally common in spam and in real post: they would only
# dilute the statistics.
_STOPWORTE = frozenset({
    "eine", "einen", "einer", "eines", "ihre", "ihren", "ihrem", "ihrer", "dein", "deine",
    "oder", "aber", "auch", "noch", "sich", "sind", "wird", "werden", "haben", "wurde",
    "nicht", "mehr", "diese", "diesem", "diesen", "dieser", "dieses", "durch", "unter",
    "über", "sehr", "hier", "dass", "wenn", "dann", "with", "your", "from", "this", "that",
    "have", "will", "please",
})


def features(res: RuleResult, subject: str, *, contact_hits: str = "") -> list[str]:
    """Feature keys for learning (`spam_learn`).

    A feature is anything that can be recognised again in a future mail: sender, domain, the
    alias written to, the technical signals and distinctive subject words. Deliberately coarse,
    because finer features would need more decisions than a person ever
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
    if res.is_newsletter:
        out.append("sig:newsletter")
    if contact_hits:
        out.append(f"sig:kontakt_{contact_hits}")
    words = {
        w.lower() for w in _WORD_RE.findall(subject or "")
        if w.lower() not in _STOPWORTE
    }
    out.extend(f"wort:{w}" for w in sorted(words)[:12])
    # Keep the order stable, drop duplicates (counters must not rise twice).
    return list(dict.fromkeys(out))
