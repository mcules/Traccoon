"""Expressions in templates: `{{ path | filter:argument }}`.

`{{…}}` used to do exactly one thing: insert a context value. That builds a text but does
nothing with it. No formatting a date, no shortening a subject, no fallback when the field
is empty. That is where "without programming" fails: you have the data but cannot get it
into the shape the target system wants.

**Deliberately not a programming language.** No `eval`, no template engine, no loops inside
the text. A closed list of filters applied from left to right. That stays readable for
people who are not developers, and by construction it cannot do damage: what is not listed
here does not happen.

    {{ mail.subject | kurz:40 }}
    {{ spam.score | mal:100 | rund:1 }} %
    {{ klasse.category | default:"sonstiges" | gross }}
    {{ jetzt | datum:"%d.%m.%Y" }}
    {{ tool.json.items | anzahl }}
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

log = logging.getLogger("workflow_expr")

# {{ quelle | filter:arg,arg | filter }}
AUSDRUCK_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _dig(data, pfad: str):
    cur = data
    for teil in str(pfad).split("."):
        if isinstance(cur, dict) and teil in cur:
            cur = cur[teil]
        elif isinstance(cur, list) and teil.isdigit() and int(teil) < len(cur):
            cur = cur[int(teil)]
        else:
            return None
    return cur


def _zahl(wert, fallback=0.0) -> float:
    try:
        return float(str(wert).replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def _zeit(wert) -> dt.datetime | None:
    """Text/Zahl → Zeitpunkt. Versteht ISO-Zeitstempel und Unix-Sekunden."""
    if isinstance(wert, dt.datetime):
        return wert
    if isinstance(wert, (int, float)):
        return dt.datetime.fromtimestamp(float(wert), tz=dt.timezone.utc)
    text = str(wert or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Filter ───────────────────────────────────────────────────────────────────
# Each one takes (value, *arguments) and returns a value. An error is not fatal: a template
# that does not fit in one spot must not bring down the whole run.

def _f_kurz(wert, laenge="40", endung="…"):
    text = "" if wert is None else str(wert)
    n = int(_zahl(laenge, 40))
    return text if len(text) <= n else text[: max(0, n - len(endung))] + endung


def _f_default(wert, ersatz=""):
    leer = wert is None or wert == "" or wert == [] or wert == {}
    return ersatz if leer else wert


def _f_datum(wert, form="%d.%m.%Y"):
    zeit = _zeit(wert)
    return zeit.strftime(form) if zeit else ""


def _f_plus_zeit(wert, menge="0", einheit="t"):
    zeit = _zeit(wert) or dt.datetime.now(tz=dt.timezone.utc)
    n = _zahl(menge, 0)
    delta = {"t": dt.timedelta(days=n), "h": dt.timedelta(hours=n),
             "m": dt.timedelta(minutes=n)}.get(str(einheit)[:1].lower(), dt.timedelta(days=n))
    return (zeit + delta).isoformat()


def _f_verbinde(wert, trenner=", "):
    if isinstance(wert, (list, tuple)):
        return trenner.join("" if x is None else str(x) for x in wert)
    return "" if wert is None else str(wert)


def _f_anzahl(wert):
    if isinstance(wert, (list, tuple, dict, str)):
        return len(wert)
    return 0 if wert is None else 1


def _f_json(wert):
    try:
        return json.dumps(wert, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(wert)


def _f_ersetze(wert, alt="", neu=""):
    return ("" if wert is None else str(wert)).replace(alt, neu)


def _f_rund(wert, stellen="0"):
    n = int(_zahl(stellen, 0))
    z = round(_zahl(wert), n)
    return int(z) if n <= 0 else z


FILTER = {
    # Text
    "gross": (lambda w: ("" if w is None else str(w)).upper(), "Text in Großbuchstaben"),
    "klein": (lambda w: ("" if w is None else str(w)).lower(), "Text in Kleinbuchstaben"),
    "trimmen": (lambda w: ("" if w is None else str(w)).strip(), "Leerzeichen außen weg"),
    "kurz": (_f_kurz, "Auf n Zeichen kürzen — kurz:40"),
    "ersetze": (_f_ersetze, "Textteil austauschen — ersetze:\"alt\",\"neu\""),
    # Zahlen
    "mal": (lambda w, f="1": _zahl(w) * _zahl(f, 1), "Multiplizieren — mal:100"),
    "plus": (lambda w, f="0": _zahl(w) + _zahl(f), "Addieren — plus:1"),
    "minus": (lambda w, f="0": _zahl(w) - _zahl(f), "Subtrahieren — minus:1"),
    "rund": (_f_rund, "Runden — rund:1 (Nachkommastellen)"),
    # "loses -1.95 % per day" reads wrong: the sign is already in the verb.
    "betrag": (lambda w: abs(_zahl(w)), "Vorzeichen weglassen"),
    # Listen
    "anzahl": (_f_anzahl, "Wie viele Einträge (bzw. Zeichen)"),
    "erstes": (lambda w: w[0] if isinstance(w, (list, tuple)) and w else "", "Erster Eintrag"),
    "letztes": (lambda w: w[-1] if isinstance(w, (list, tuple)) and w else "", "Letzter Eintrag"),
    "verbinde": (_f_verbinde, "Liste zu Text — verbinde:\", \""),
    # Zeit
    "datum": (_f_datum, "Zeit formatieren — datum:\"%d.%m.%Y\""),
    "plus_zeit": (_f_plus_zeit, "Zeit verschieben — plus_zeit:2,\"h\" (t=Tage, h=Stunden, m=Minuten)"),
    # Allgemein
    "default": (_f_default,
                "Ersatz, wenn leer — default:\"sonstiges\" (in Anführungszeichen wörtlich, "
                "ohne Anführungszeichen ein Kontextpfad: default:event.type)"),
    "json": (_f_json, "Als JSON-Text"),
    "text": (lambda w: "" if w is None else str(w), "Als Text"),
}

# Sources that do not come from the context.
SONDERQUELLEN = {
    "jetzt": lambda: dt.datetime.now(tz=dt.timezone.utc).isoformat(),
    "heute": lambda: dt.date.today().isoformat(),
}


def _teile(ausdruck: str) -> list[str]:
    """Split on `|`, but not inside quotes."""
    teile, aktuell, quote = [], "", ""
    for zeichen in ausdruck:
        if quote:
            aktuell += zeichen
            if zeichen == quote:
                quote = ""
        elif zeichen in "\"'":
            quote = zeichen
            aktuell += zeichen
        elif zeichen == "|":
            teile.append(aktuell.strip())
            aktuell = ""
        else:
            aktuell += zeichen
    teile.append(aktuell.strip())
    return [t for t in teile if t]


def _argumente(roh: str) -> list[tuple[str, bool]]:
    """`kurz:40,"…"` becomes [("40", False), ("…", True)]. Comma splits, quotes protect.

    The second field says whether the argument was quoted, and that is more than cosmetics:
    quoted means literal, unquoted may be a context path (see `auswerten`).

    An explicitly empty argument survives (`kurz:11,""` means "shorten, but without an
    ellipsis"). Only what is not there at all gets dropped. Without that distinction the
    parser swallows the intent and silently applies the default.
    """
    out: list[tuple[str, bool]] = []
    aktuell, quote, zitiert = "", "", False
    for zeichen in roh:
        if quote:
            if zeichen == quote:
                quote = ""
            else:
                aktuell += zeichen
        elif zeichen in "\"'":
            quote, zitiert = zeichen, True
        elif zeichen == ",":
            if zitiert or aktuell.strip() != "":
                out.append((aktuell.strip() if not zitiert else aktuell, zitiert))
            aktuell, zitiert = "", False
        else:
            aktuell += zeichen
    if zitiert or aktuell.strip() != "":
        out.append((aktuell.strip() if not zitiert else aktuell, zitiert))
    return out


def _quelle(text: str, ctx: dict):
    """First link of a chain: a literal, a special source, or a context path."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in SONDERQUELLEN:
        return SONDERQUELLEN[text]()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        pass
    return _dig(ctx, text)


def auswerten(ausdruck: str, ctx: dict):
    """Evaluate one expression (without the braces), returns a value or None."""
    glieder = _teile(ausdruck)
    if not glieder:
        return None
    wert = _quelle(glieder[0], ctx)
    for glied in glieder[1:]:
        name, _, roh = glied.partition(":")
        name = name.strip()
        eintrag = FILTER.get(name)
        if eintrag is None:
            log.debug("Unbekannter Filter %r in %r", name, ausdruck)
            continue
        fn = eintrag[0]
        try:
            wert = fn(wert, *_filterargumente(roh, ctx)) if roh else fn(wert)
        except Exception:  # noqa: BLE001, a broken template must not fail the run
            log.info("Filter %r auf %r fehlgeschlagen", name, wert)
    return wert


def _filterargumente(roh: str, ctx: dict) -> list[str]:
    """Arguments of a filter: quoted is literal, unquoted may come from the context.

    `default:"-"` is a text, `default:event.type` is the value found there. Without that
    rule `{{ event.attributes.alarm | default:event.type }}` wrote the literal text
    "event.type" into the message, and into the throttle key, where every kind of fault
    would then have been the same case. If the path resolves to nothing, the text stays, so
    a fallback like `default:unknown` behaves as before.
    """
    fertig: list[str] = []
    for text, zitiert in _argumente(roh):
        if not zitiert and text and not text.replace(".", "", 1).lstrip("-").isdigit():
            aus_ctx = _dig(ctx, text)
            if aus_ctx is not None:
                fertig.append(aus_ctx if isinstance(aus_ctx, str) else str(aus_ctx))
                continue
        fertig.append(text)
    return fertig


def fuellen(text: str, ctx: dict) -> str:
    """Replace every `{{…}}` inside a text."""
    def ersetzen(m: re.Match) -> str:
        wert = auswerten(m.group(1), ctx)
        if wert is None:
            return ""
        if isinstance(wert, (dict, list)):
            return _f_json(wert)
        if isinstance(wert, bool):
            return "true" if wert else "false"
        return str(wert)
    return AUSDRUCK_RE.sub(ersetzen, text)


def katalog() -> list[dict]:
    """The filters as the editor shows them in its help."""
    return [{"name": n, "hilfe": h} for n, (_, h) in sorted(FILTER.items())]
