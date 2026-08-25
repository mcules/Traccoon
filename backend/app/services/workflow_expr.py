"""Expressions in templates: `{{ path | filter:argument }}`.

`{{…}}` used to do exactly one thing: insert a context value. That builds a text but does
nothing with it. No formatting a date, no shortening a subject, no fallback when the field
is empty. That is where "without programming" fails: you have the data but cannot get it
into the shape the target system wants.

**Deliberately not a programming language.** No `eval`, no template engine, no loops inside
the text. A closed list of filters applied from left to right. That stays readable for
people who are not developers, and by construction it cannot do damage: what is not listed
here does not happen.

    {{ mail.subject | truncate:40 }}
    {{ spam.score | times:100 | round:1 }} %
    {{ classification.category | default:"sonstiges" | upper }}
    {{ now | date:"%d.%m.%Y" }}
    {{ tool.json.items | count }}
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

log = logging.getLogger("workflow_expr")

# {{ quelle | filter:arg,arg | filter }}
EXPRESSION_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _dig(data, path: str):
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _number(value, fallback=0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def _ts(value) -> dt.datetime | None:
    """Text or number to a point in time. Understands ISO timestamps and Unix seconds."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Filter ───────────────────────────────────────────────────────────────────
# Each one takes (value, *arguments) and returns a value. An error is not fatal: a template
# that does not fit in one spot must not bring down the whole run.

def _f_short(value, length="40", extension="…"):
    text = "" if value is None else str(value)
    n = int(_number(length, 40))
    return text if len(text) <= n else text[: max(0, n - len(extension))] + extension


def _f_default(value, replacement=""):
    empty = value is None or value == "" or value == [] or value == {}
    return replacement if empty else value


def _f_date(value, form="%d.%m.%Y"):
    ts = _ts(value)
    return ts.strftime(form) if ts else ""


def _f_plus_ts(value, amount="0", unit="t"):
    ts = _ts(value) or dt.datetime.now(tz=dt.timezone.utc)
    n = _number(amount, 0)
    delta = {"t": dt.timedelta(days=n), "h": dt.timedelta(hours=n),
             "m": dt.timedelta(minutes=n)}.get(str(unit)[:1].lower(), dt.timedelta(days=n))
    return (ts + delta).isoformat()


def _f_join(value, separator=", "):
    if isinstance(value, (list, tuple)):
        return separator.join("" if x is None else str(x) for x in value)
    return "" if value is None else str(value)


def _f_count(value):
    if isinstance(value, (list, tuple, dict, str)):
        return len(value)
    return 0 if value is None else 1


def _f_field(value, name=""):
    """Pull one field out of a list of objects: `{{ treffer | field:"filename" }}`.

    The counterpart to `verbinde`, and the piece that was missing to get from a search
    result to a sentence. A single object gives a single value, so the filter also works
    where a path happens to hold one result rather than many.
    """
    if isinstance(value, dict):
        return value.get(name)
    if isinstance(value, (list, tuple)):
        return [e.get(name) for e in value if isinstance(e, dict) and e.get(name) is not None]
    return value


def _f_filename(value):
    """Path to note name: `03 Bereiche/Fahrzeuge/VW T5.md` becomes `VW T5`.

    Element wise over a list, because that is where paths usually arrive. What is wanted in
    a sentence is the name, not the shelf it stands on.
    """
    def one(x):
        t = "" if x is None else str(x)
        return t.rsplit("/", 1)[-1].removesuffix(".md")
    if isinstance(value, (list, tuple)):
        return [one(x) for x in value]
    return one(value)


def _f_max(value):
    """Largest number in a list, 0 when there is nothing to compare.

    A guard cannot walk a list (JSONLogic knows no `some` here on purpose), so the question
    "does any day bring snow" is answered by turning the list into one number first.
    """
    if isinstance(value, (list, tuple)):
        numbers = [_number(x) for x in value if x is not None]
        return max(numbers) if numbers else 0
    return _number(value)


def _f_min(value):
    """Smallest number in a list, the counterpart to `max`."""
    if isinstance(value, (list, tuple)):
        numbers = [_number(x) for x in value if x is not None]
        return min(numbers) if numbers else 0
    return _number(value)


def _f_lines_with(value, pattern=""):
    """The lines of a text that contain `muster`, as a list.

    Not every tool answers in JSON. The vault server, for instance, renders its search hits
    as markdown, and its paths stand in the heading lines. Without this the answer would only
    be usable whole, and a flow would have to paste a page of markdown where one sentence
    belongs. Without a pattern: every non empty line.
    """
    text = "" if value is None else str(value)
    lines = [z.strip() for z in text.splitlines() if z.strip()]
    return [z for z in lines if pattern in z] if pattern else lines


def _f_json(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _f_replace(value, old="", new=""):
    return ("" if value is None else str(value)).replace(old, new)


def _f_round(value, spots="0"):
    n = int(_number(spots, 0))
    z = round(_number(value), n)
    return int(z) if n <= 0 else z


FILTER = {
    # Text
    "upper": (lambda w: ("" if w is None else str(w)).upper(), "the text in capitals"),
    "lower": (lambda w: ("" if w is None else str(w)).lower(), "Text in Kleinbuchstaben"),
    "trim": (lambda w: ("" if w is None else str(w)).strip(), "spaces on the outside removed"),
    "truncate": (_f_short, "shorten to n characters — truncate:40"),
    "replace": (_f_replace, "Textteil austauschen — replace:\"alt\",\"neu\""),
    # Zahlen
    "times": (lambda w, f="1": _number(w) * _number(f, 1), "Multiplizieren — times:100"),
    "plus": (lambda w, f="0": _number(w) + _number(f), "Addieren — plus:1"),
    "minus": (lambda w, f="0": _number(w) - _number(f), "Subtrahieren — minus:1"),
    "round": (_f_round, "Runden — round:1 (Nachkommastellen)"),
    # "loses -1.95 % per day" reads wrong: the sign is already in the verb.
    "abs": (lambda w: abs(_number(w)), "Vorzeichen weglassen"),
    # Listen
    "count": (_f_count, "how many entries (or characters)"),
    "first": (lambda w: w[0] if isinstance(w, (list, tuple)) and w else "", "Erster Eintrag"),
    "last": (lambda w: w[-1] if isinstance(w, (list, tuple)) and w else "", "Letzter Eintrag"),
    "join": (_f_join, "Liste zu Text — join:\", \""),
    "field": (_f_field, "One field out of a list of objects, field:\"name\""),
    "basename": (_f_filename, "the note name out of the path (without folder and .md)"),
    "max": (_f_max, "the largest number in a list"),
    "lines_with": (_f_lines_with, "the lines of a text that contain something — lines_with:\"### \""),
    "min": (_f_min, "Kleinster Zahlwert einer Liste"),
    # Zeit
    "date": (_f_date, "Zeit formatieren — date:\"%d.%m.%Y\""),
    "add_time": (_f_plus_ts, "Zeit verschieben — add_time:2,\"h\" (t=Tage, h=Stunden, m=Minuten)"),
    # Allgemein
    "default": (_f_default,
                "a stand-in when empty — default:\"other\" (in quotes taken literally, "
                "without quotes a context path: default:event.type)"),
    "json": (_f_json, "Als JSON-Text"),
    "text": (lambda w: "" if w is None else str(w), "Als Text"),
}

# Sources that do not come from the context.
SPECIAL_SOURCES = {
    "now": lambda: dt.datetime.now(tz=dt.timezone.utc).isoformat(),
    "today": lambda: dt.date.today().isoformat(),
}


def _parts(expression: str) -> list[str]:
    """Split on `|`, but not inside quotes."""
    parts, current, quote = [], "", ""
    for chars in expression:
        if quote:
            current += chars
            if chars == quote:
                quote = ""
        elif chars in "\"'":
            quote = chars
            current += chars
        elif chars == "|":
            parts.append(current.strip())
            current = ""
        else:
            current += chars
    parts.append(current.strip())
    return [t for t in parts if t]


def _arguments(raw: str) -> list[tuple[str, bool]]:
    """`kurz:40,"…"` becomes [("40", False), ("…", True)]. Comma splits, quotes protect.

    The second field says whether the argument was quoted, and that is more than cosmetics:
    quoted means literal, unquoted may be a context path (see `auswerten`).

    An explicitly empty argument survives (`kurz:11,""` means "shorten, but without an
    ellipsis"). Only what is not there at all gets dropped. Without that distinction the
    parser swallows the intent and silently applies the default.
    """
    out: list[tuple[str, bool]] = []
    current, quote, quoted = "", "", False
    for chars in raw:
        if quote:
            if chars == quote:
                quote = ""
            else:
                current += chars
        elif chars in "\"'":
            quote, quoted = chars, True
        elif chars == ",":
            if quoted or current.strip() != "":
                out.append((current.strip() if not quoted else current, quoted))
            current, quoted = "", False
        else:
            current += chars
    if quoted or current.strip() != "":
        out.append((current.strip() if not quoted else current, quoted))
    return out


def _source(text: str, ctx: dict):
    """First link of a chain: a literal, a special source, or a context path."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in SPECIAL_SOURCES:
        return SPECIAL_SOURCES[text]()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        pass
    return _dig(ctx, text)


def evaluate(expression: str, ctx: dict):
    """Evaluate one expression (without the braces), returns a value or None."""
    parts = _parts(expression)
    if not parts:
        return None
    value = _source(parts[0], ctx)
    for part in parts[1:]:
        name, _, raw = part.partition(":")
        name = name.strip()
        entry = FILTER.get(name)
        if entry is None:
            log.debug("Unknown filter %r in %r", name, expression)
            continue
        fn = entry[0]
        try:
            value = fn(value, *_filterargs(raw, ctx)) if raw else fn(value)
        except Exception:  # noqa: BLE001, a broken template must not fail the run
            log.info("Filter %r on %r failed", name, value)
    return value


def _filterargs(raw: str, ctx: dict) -> list[str]:
    """Arguments of a filter: quoted is literal, unquoted may come from the context.

    `default:"-"` is a text, `default:event.type` is the value found there. Without that
    rule `{{ event.attributes.alarm | default:event.type }}` wrote the literal text
    "event.type" into the message, and into the throttle key, where every kind of fault
    would then have been the same case. If the path resolves to nothing, the text stays, so
    a fallback like `default:unknown` behaves as before.
    """
    done: list[str] = []
    for text, quoted in _arguments(raw):
        if not quoted and text and not text.replace(".", "", 1).lstrip("-").isdigit():
            from_ctx = _dig(ctx, text)
            if from_ctx is not None:
                done.append(from_ctx if isinstance(from_ctx, str) else str(from_ctx))
                continue
        done.append(text)
    return done


def fill(text: str, ctx: dict) -> str:
    """Replace every `{{…}}` inside a text."""
    def replace(m: re.Match) -> str:
        value = evaluate(m.group(1), ctx)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return _f_json(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    return EXPRESSION_RE.sub(replace, text)


def catalog() -> list[dict]:
    """The filters as the editor shows them in its help."""
    return [{"name": n, "hilfe": h} for n, (_, h) in sorted(FILTER.items())]
