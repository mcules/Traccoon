"""Turning a template into runnable code.

A template in this vault mixes text with two kinds of tag: `<% expr %>` puts a
value in, `<%* statements %>` runs code that appends to `tR`. Both are ordinary
JavaScript, and the page's content policy forbids building a function from a
string in the browser — so the code is assembled here and handed back as a real
module from this origin, the same way the query language's script blocks already
are.

It runs in the browser and not here on purpose: half of what these templates do
is ask questions, and a question needs somebody to answer it.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

# `<%` … `%>`, with an optional `*` for a statement block and an optional `-`
# on either side that eats the surrounding whitespace.
TAG = re.compile(r"<%(\*?)-?\s*([\s\S]*?)\s*-?%>")
# Whether anything in it needs a person, for a caller deciding where to run it.
INTERACTIVE = re.compile(r"tp\.system\.(prompt|suggester)")
NOTE_SUFFIX = re.compile(r"\.(md|markdown)$", re.I)

KEPT = 200
KEEP_FOR = 3600.0


@dataclass
class Compiled:
    code: str
    interactive: bool

    @property
    def id(self) -> str:
        return hashlib.sha256(self.code.encode()).hexdigest()[:32]


def _literal(text: str) -> str:
    """A chunk of plain text, safe to drop inside a template string."""
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def compile_template(source: str) -> Compiled:
    parts: list[str] = []
    last = 0
    for m in TAG.finditer(source):
        star, body = m.group(1), m.group(2)
        before = source[last:m.start()]
        if before:
            parts.append(f"tR += `{_literal(before)}`;")
        if star:
            # A statement block writes to tR itself, or returns a value.
            parts.append(body)
        else:
            parts.append(f"tR += String(await ({body}) ?? '');")
        last = m.end()
        # `-%>` swallows the newline that follows the tag.
        if m.group(0).endswith("-%>") and source[last:last + 1] == "\n":
            last += 1
    rest = source[last:]
    if rest:
        parts.append(f"tR += `{_literal(rest)}`;")

    body_lines = "\n".join(f"  {p}" for p in parts)
    code = (
        "// Generated from a vault template.\n"
        # `host` carries the few globals a template reaches for outside `tp` and
        # `app`. A module cannot see the caller's scope, so they are handed in.
        "export default async function render(tp, app, host) {\n"
        "  const { Notice } = host ?? {};\n"
        '  let tR = "";\n'
        f"{body_lines}\n"
        "  return tR;\n"
        "}\n"
    )
    return Compiled(code=code, interactive=bool(INTERACTIVE.search(source)))


@dataclass
class Modules:
    """What has been compiled, waiting to be fetched back.

    Only ever code this server generated from a template in the vault, and only
    for as long as the browser plausibly still needs it: the page compiles, then
    imports, and after that the entry is dead weight.
    """

    kept: dict[str, tuple[str, float]] = field(default_factory=dict)

    def put(self, compiled: Compiled) -> str:
        self.kept[compiled.id] = (compiled.code, time.time())
        self._forget_old()
        return compiled.id

    def get(self, module_id: str) -> str | None:
        found = self.kept.get(module_id)
        if found is None:
            return None
        self.kept[module_id] = (found[0], time.time())
        return found[0]

    def _forget_old(self) -> None:
        now = time.time()
        for key in [k for k, (_, at) in self.kept.items() if now - at > KEEP_FOR]:
            self.kept.pop(key, None)
        if len(self.kept) > KEPT:
            oldest = sorted(self.kept, key=lambda k: self.kept[k][1])
            for key in oldest[:len(self.kept) - KEPT]:
                self.kept.pop(key, None)


def in_folder(paths: list[str], folder: str) -> list[dict]:
    """The notes in the template folder, as things to insert.

    Sorted the way somebody reading them would sort them, which is not the way
    bytes sort: see `notes/dv/js.py` for what that means and why.
    """
    if not folder:
        return []
    from .dv.js import collate_key

    prefix = f"{folder}/"
    found = [{"path": p, "name": NOTE_SUFFIX.sub("", p[len(prefix):])}
             for p in paths if p.startswith(prefix)]
    return sorted(found, key=lambda t: collate_key(t["name"]))


# ------------------------------------------------------- filling one in, here
#
# Two dialects live in these files. The core `{{date}}`, `{{time}}`, `{{title}}`
# — plain substitutions. And the `<% … %>` of the other one, of which only a
# handful of calls appear here; what is covered is what a note can need while
# being *created* with nobody watching, so no questions and no pickers. Anything
# else is left standing verbatim rather than mangled, so a template that needs a
# dialog still says so instead of quietly losing its content.

CORE_TOKEN = re.compile(r"\{\{\s*(date|time|title)\s*(?::\s*([^}]+?))?\s*\}\}", re.I)
TEMPLATER_TAG = re.compile(r"<%\*?\s*([\s\S]*?)\s*-?%>")
DATE_NOW = re.compile(r"^tp\.date\.now\s*\(([\s\S]*)\)$")


@dataclass
class Filled:
    text: str
    # Expressions left as they are because they need a person to answer them.
    unresolved: list[str] = field(default_factory=list)


def _split_args(raw: str) -> list[str]:
    """A call's arguments, honouring quotes and nested calls."""
    out: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
            else:
                current += ch
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        out.append(current.strip())
    return out


def _date_now(args: list[str], now) -> str | None:
    """`tp.date.now(format, offset, reference, referenceFormat)`.

    The four-argument form says "so many days after the day this note is named
    for" — the reference being the note's own title.
    """
    from . import dates

    fmt = args[0] if args else "YYYY-MM-DD"
    try:
        offset = int(float(args[1])) if len(args) > 1 and args[1] != "" else 0
    except ValueError:
        return None
    base = now
    if len(args) > 2:
        reference = dates.parse_date(args[2], args[3] if len(args) > 3 else "YYYY-MM-DD")
        if reference is None:
            return None                  # reference unreadable: leave the call alone
        base = reference
    return dates.format_date(dates.add_days(base, offset), fmt)


def _templater_value(expr: str, title: str, now) -> str | None:
    """What this side can answer; None means "not ours"."""
    src = expr.strip()
    if src == "tp.file.title":
        return title
    call = DATE_NOW.match(src)
    if call:
        args = [title if a == "tp.file.title" else a for a in _split_args(call.group(1))]
        return _date_now(args, now)
    return None


def fill(template: str, *, title: str, now=None,
         date_format: str = "YYYY-MM-DD", time_format: str = "HH:mm") -> Filled:
    import datetime as _dt

    from . import dates

    when = now or _dt.datetime.now()
    unresolved: list[str] = []

    # The `<% … %>` first: such an expression may itself contain braces.
    def one_tag(m: re.Match) -> str:
        value = _templater_value(m.group(1), title, when)
        if value is None:
            unresolved.append(m.group(1).strip())
            return m.group(0)
        return value

    text = TEMPLATER_TAG.sub(one_tag, template)

    def one_core(m: re.Match) -> str:
        kind = m.group(1).lower()
        if kind == "title":
            return title
        pattern = (m.group(2) or "").strip() or (date_format if kind == "date" else time_format)
        return dates.format_date(when, pattern)

    return Filled(text=CORE_TOKEN.sub(one_core, text), unresolved=unresolved)
