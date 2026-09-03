"""The other language's arithmetic, written out.

Everything in this package is a translation of an implementation the notes were
written against, and the two languages disagree in a handful of small ways that
never raise anything. They come out as a number that is one off, a string cut in
the middle of an emoji, a list in a different order. Each of those is a wrong
answer that looks like a right one, so the differences live here, named, instead
of being rediscovered in each module.

  * **A string is counted in UTF-16 there and in characters here.** An emoji is
    two units and one character. The vault's tasks are full of them (`📅`, `⏫`),
    so `length()` and every cut through a string go through `js_len` and
    `js_slice` rather than through Python's own.
  * **There is one number type there.** `2.0` prints as `2`, and so does the
    result of `4 / 2`. Anything that is written out or compared as text goes
    through `js_num_str`.
  * **Rounding goes up at the half, not to the even number.** `round(2.5)` is 3
    there and 2 here, and `round(-2.5)` is -2 there and -2 here for a different
    reason.
  * **Two ways of reading a number out of text**, and the implementations use
    both: `parseFloat` takes the longest prefix that is a number, `Number` wants
    the whole string and calls empty zero.
  * **Sorting text is a collation, not a comparison of code points.** The other
    side asks for German with numbers read as numbers and accents ignored, which
    puts `Ärger` next to `Arger` and `Datei 10` after `Datei 9`.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

# --------------------------------------------------------------- text length

def js_len(s: str) -> int:
    """How long that language thinks this string is.

    It counts UTF-16 code units, so everything outside the basic plane counts
    twice. Astral characters are rare enough in prose that a loop over them is
    cheaper than encoding the whole string.
    """
    extra = 0
    for ch in s:
        if ord(ch) > 0xFFFF:
            extra += 1
    return len(s) + extra


def js_slice(s: str, start: int, end: int | None = None) -> str:
    """`String.prototype.slice`, with the offsets counted in UTF-16 units.

    Cutting between the halves of a surrogate pair is possible there and would
    produce half an emoji; here it takes the whole character, which is the
    closest thing to that and never produces broken text.
    """
    if not any(ord(ch) > 0xFFFF for ch in s):
        n = len(s)
        return s[_clamp(start, n):(n if end is None else _clamp(end, n))]
    # Walk once, remembering where each UTF-16 offset falls in the string.
    offsets = [0]
    at = 0
    for ch in s:
        at += 2 if ord(ch) > 0xFFFF else 1
        offsets.append(at)
    total = at
    a = _clamp(start, total)
    b = total if end is None else _clamp(end, total)
    # The first character whose start is at or after the offset.
    def index_of(off: int) -> int:
        for i, o in enumerate(offsets):
            if o >= off:
                return i
        return len(s)
    return s[index_of(a):index_of(b)] if b > a else ""


def _clamp(i: int, n: int) -> int:
    if i < 0:
        i += n
    return max(0, min(n, i))


def js_pad(s: str, width: int, fill: str, *, start: bool) -> str:
    """`padStart` / `padEnd`: the width is in UTF-16 units and the filler repeats."""
    have = js_len(s)
    if not fill or have >= width:
        return s
    need = width - have
    grown = (fill * (need // js_len(fill) + 1))
    grown = js_slice(grown, 0, need)
    return grown + s if start else s + grown


# ------------------------------------------------------------------ numbers

def js_num_str(n: float | int) -> str:
    """What that language prints for a number.

    Whole numbers have no decimal point there, however they arose. A `4 / 2`
    that comes out as `2.0` and is then used as a table cell or compared as text
    would show up as a different answer everywhere it appears.
    """
    if isinstance(n, bool):
        return "true" if n else "false"
    if isinstance(n, int):
        return str(n)
    if math.isnan(n):
        return "NaN"
    if math.isinf(n):
        return "Infinity" if n > 0 else "-Infinity"
    if n.is_integer() and abs(n) < 1e21:
        return str(int(n))
    return repr(n)


def js_round(x: float) -> int:
    """`Math.round`: the half goes up, towards positive, not to the even number.

    So 2.5 is 3 and -2.5 is -2. Python's own `round` would give 2 and -2.
    """
    return math.floor(x + 0.5)


def js_round_to(x: float, digits: int) -> float:
    """`Math.round(x * 10**d) / 10**d`, as the implementations spell it."""
    f = 10.0 ** digits
    return js_round(x * f) / f


def to_fixed(x: float, digits: int) -> str:
    """`Number.prototype.toFixed`.

    The half goes away from zero on the exact value of the double, which is not
    what Python's formatting does — it goes to the even digit. They differ on
    exact halves, which is where a price ending in 5 lives.
    """
    digits = max(0, min(20, digits))
    if math.isnan(x):
        return "NaN"
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(x).quantize(quantum, rounding=ROUND_HALF_UP))


_FLOAT_PREFIX = re.compile(r"^[+-]?(?:Infinity|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")


def parse_float(s: str) -> float:
    """`parseFloat`: the longest prefix that is a number, else not-a-number.

    `parseFloat("12 Stück")` is 12 there and an exception here, and a field
    written as `2 Tage` is a number to half the vault's queries.
    """
    m = _FLOAT_PREFIX.match(s.strip())
    if not m:
        return math.nan
    text = m.group(0)
    if text.lstrip("+-") == "Infinity":
        return -math.inf if text.startswith("-") else math.inf
    try:
        return float(text)
    except ValueError:
        return math.nan


_NUMBER = re.compile(r"""(?x)
    ^ [+-]? (?:
        Infinity
      | 0[xX][0-9a-fA-F]+
      | 0[oO][0-7]+
      | 0[bB][01]+
      | (?: \d+ \.? \d* | \. \d+ ) (?: [eE][+-]?\d+ )?
    ) $""")


def js_number(s: str) -> float:
    """`Number(string)`: all of it or nothing, and empty is zero.

    The other reading of a number, used where the implementations use `Number`.
    The difference from `parseFloat` is not cosmetic: `Number("12abc")` is
    not-a-number, so a filter on it is false, where `parseFloat` would say 12
    and let the row through.

    The grammar is written out rather than handed to Python's own `float`,
    which also takes `1_000`, `nan` and `inf` — none of which are numbers there.
    """
    text = s.strip()
    if text == "":
        return 0.0
    if not _NUMBER.match(text):
        return math.nan
    sign = -1.0 if text.startswith("-") else 1.0
    body = text.lstrip("+-")
    if body == "Infinity":
        return sign * math.inf
    if len(body) > 1 and body[0] == "0" and body[1] in "xXoObB":
        base = {"x": 16, "o": 8, "b": 2}[body[1].lower()]
        return sign * float(int(body[2:], base))
    return float(text)


# ---------------------------------------------------------------- collation

_FOLD = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ł": "l"})
_DIGITS = re.compile(r"(\d+)")

# What a character counts as when two strings are compared. The order is the
# collation's, not the code point's: a space sorts before a dash, a dash before
# a digit, a digit before a letter. Sorting by code point instead would put
# `V´Kult` after `Valentina` (the accent is code point 180, past `a`) and
# `3D-Teile` after `Büro`, and both are visibly the wrong way round in a list.
_SPACE, _PUNCT, _DIGIT, _LETTER = 0, 1, 2, 3


def _class_of(ch: str) -> int:
    if ch.isspace():
        return _SPACE
    if ch.isdigit():
        return _DIGIT
    if ch.isalpha() or ch == "_":
        return _LETTER
    return _PUNCT


@lru_cache(maxsize=200_000)
def collate_key(s: str) -> tuple:
    """The sort key for German text with numbers read as numbers.

    The other side asks its collator for German, `numeric: true` and the lowest
    sensitivity, which means case and accents do not separate two words: `Ärger`
    and `Arger` are the same key and keep the order they were already in. Runs of
    digits compare as numbers, so `Datei 10` comes after `Datei 9`.

    This is a rebuild, not a call into a collation library. Two things have to
    hold: strings the other side calls equal must be equal here too — a
    comparator answering 0 leaves the existing order alone in both languages, and
    any difference there reshuffles a whole table — and the coarse order of
    character kinds has to be the collation's rather than Unicode's.
    """
    folded = unicodedata.normalize("NFD", s.translate(_FOLD).casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    out: list = []
    for part in _DIGITS.split(plain):
        if not part:
            continue
        if part.isdigit():
            out.append((_DIGIT, int(part), ""))
        else:
            for ch in part:
                out.append((_class_of(ch), 0, ch))
    return tuple(out)


def collate(a: str, b: str) -> int:
    ka, kb = collate_key(a), collate_key(b)
    return -1 if ka < kb else (1 if ka > kb else 0)


def collate_plain(a: str, b: str) -> int:
    """`localeCompare` with no options: digits are characters, not numbers.

    Used where the other side calls it without any — the headings of the groups
    of a table file, where `10` before `9` is what it does.
    """
    return _plain_compare(a, b)


@lru_cache(maxsize=100_000)
def _plain_key(s: str) -> tuple:
    folded = unicodedata.normalize("NFD", s.translate(_FOLD).casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return tuple((_class_of(c), c) for c in plain)


def _plain_compare(a: str, b: str) -> int:
    ka, kb = _plain_key(a), _plain_key(b)
    if ka == kb:
        # The same once folded: the other side then separates them by case and
        # accent rather than calling them one and the same.
        return -1 if a < b else (1 if a > b else 0)
    return -1 if ka < kb else 1


# --------------------------------------------------------- JSON, their way

def json_key(value: Any) -> str:
    """`JSON.stringify`, used where the implementations use it as an identity.

    Only the shape matters, not the prettiness: two values that stringify the
    same are one group. Separators without spaces and integral floats as
    integers are what makes this agree with the other side.
    """
    return json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"))


def jsonable(value: Any) -> Any:
    """Turn a value into what that language would have put on the wire.

    The one thing that needs doing is numbers: a float that happens to be whole
    is written without its decimal point there. Everything a comparison against
    the recorded answers looks at goes through here.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None                       # `JSON.stringify(NaN)` is `null`
        return int(value) if value.is_integer() and abs(value) < 1e15 else value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


# ------------------------------------------------------------------ regexes

# What may follow a backslash when the pattern is read in unicode mode. Outside
# that mode the engine is forgiving and a backslash before anything at all means
# that character; in it, an escape it does not know is refused.
_ESCAPE_OK = set("^$\\.*+?()[]{}|/dDsSwWbBfnrtv0123456789cxupPk")


def js_unicode_ok(pattern: str) -> bool:
    """Would that engine take this pattern with the unicode flag on?

    It would not, for two spellings that this engine reads happily, and the
    difference is not academic: a `]` on its own is refused there, the call it
    was written for catches the refusal, and the text comes back unchanged. A
    pattern like `[^]]*]` — which is what a query in this vault ends up with
    once the string escapes are taken off — therefore does nothing there and
    would quietly start doing something here.

    Not a full check of that grammar. It looks for the two things that actually
    occur, an unmatched closing bracket or brace and an escape the mode refuses,
    and leaves the rest to this engine's own reading.
    """
    i, n = 0, len(pattern)
    in_class = False
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                return False
            if pattern[i + 1] not in _ESCAPE_OK:
                return False
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class = True
            # `[^]` and `[]` are a complete class there and not the start of one,
            # so what follows them is outside the class again.
            if pattern.startswith("[^]", i):
                in_class = False
                i += 3
                continue
            if pattern.startswith("[]", i):
                in_class = False
                i += 2
                continue
            i += 1
            continue
        if c == "]":
            return False
        if c == "{":
            end = pattern.find("}", i)
            if end < 0 or not re.fullmatch(r"\d+(,\d*)?", pattern[i + 1:end]):
                return False
            i = end + 1
            continue
        if c == "}":
            return False
        i += 1
    return not in_class


def compile_js(pattern: str, *, ignore_case: bool = False,
               unicode_mode: bool = True) -> re.Pattern | None:
    """A regular expression written for the other engine, compiled for this one.

    `\\d`, `\\w` and `\\b` are ASCII there even with the unicode flag on, and
    Unicode here — so `\\w+` would match `Müller` here and stop at the umlaut
    there. `re.ASCII` is exactly that set of escapes, which is why it is on.

    A pattern this engine will not take is not an error: it matches nothing, the
    same as an invalid one does there. `\\p{L}` is the usual case; it is written
    with the unicode flag there and has no spelling here. The check runs the
    other way as well — see `js_unicode_ok` — because a pattern that engine
    refuses must not quietly start working here.
    """
    if unicode_mode and not js_unicode_ok(pattern):
        return None
    flags = re.ASCII | (re.IGNORECASE if ignore_case else 0)
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None
