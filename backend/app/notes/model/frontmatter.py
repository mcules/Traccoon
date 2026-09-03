"""The YAML block at the top of a note, read the way the other side reads it.

PyYAML speaks YAML 1.1. The service being replaced uses a parser that speaks 1.2,
and between those two versions sit three differences that a vault runs into on
the first day:

  * `yes`, `no`, `on`, `off` are booleans in 1.1 and plain words in 1.2. A note
    with `fertig: no` would come back as `False` here and as `"no"` there.
  * `09:00` is a sexagesimal number in 1.1 — nine times sixty, so 540. In 1.2 it
    is the string it looks like. A vault full of times would quietly turn into a
    vault full of large integers.
  * `0777` is octal in 1.1 and a decimal number with a leading zero in 1.2. A
    phone number with a leading zero has been eaten by this before.

None of the three raises anything. They read as a value of the wrong type, and
the query languages above then compare a string against a number and quietly find
nothing. So the resolvers are narrowed here rather than the differences being
carried up.

Only reading. Writing a note is not this module's business, and when it becomes
somebody's, whatever writes has to preserve what it did not touch.
"""
from __future__ import annotations

import re
from typing import Any

import yaml


class Loader(yaml.SafeLoader):
    """A safe loader with the three 1.1-isms taken out."""


def _narrow(loader: type[yaml.SafeLoader]) -> None:
    """Replace the implicit resolvers that differ between the two versions.

    PyYAML keeps its resolvers in a dict from first character to a list of
    (tag, pattern). Rebuilding that dict is the only way in: the entries are
    class level and shared, so a copy has to be made before anything is dropped
    or every other user of SafeLoader in this process would be changed too.
    """
    loader.yaml_implicit_resolvers = {
        key: list(value) for key, value in loader.yaml_implicit_resolvers.items()
    }
    entfernen = {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:int",
                 "tag:yaml.org,2002:float"}
    for key, value in loader.yaml_implicit_resolvers.items():
        loader.yaml_implicit_resolvers[key] = [
            (tag, pattern) for tag, pattern in value if tag not in entfernen
        ]

    # true/false only, in the three spellings 1.2 allows.
    loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"))
    # Decimal, hex and 1.2 octal. No sexagesimal, and no bare leading zero as
    # octal: `0777` stays the number it looks like.
    loader.add_implicit_resolver(
        "tag:yaml.org,2002:int",
        re.compile(r"^[-+]?(?:[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"),
        list("-+0123456789"))
    loader.add_implicit_resolver(
        "tag:yaml.org,2002:float",
        re.compile(r"""^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$
                     |^[-+]?\.(?:inf|Inf|INF)$
                     |^\.(?:nan|NaN|NAN)$""", re.X),
        list("-+0123456789."))


def _int_12(loader: yaml.SafeLoader, node: yaml.Node) -> int:
    """Read an integer the way 1.2 does.

    Narrowing the resolver is only half of it: the resolver picks the tag, and a
    separate constructor turns the text into a value. PyYAML's still reads a
    leading zero as octal, so `0170123456` came back as 31500078 with the
    resolver already fixed. That is a phone number turning into a different
    number, silently, and it is why this is here.
    """
    text = loader.construct_scalar(node).replace("+", "", 1)
    if text.startswith("-0x") or text.startswith("0x"):
        return int(text, 16)
    if text.startswith("-0o") or text.startswith("0o"):
        return int(text, 8)
    return int(text, 10)


def _float_12(loader: yaml.SafeLoader, node: yaml.Node) -> float:
    text = loader.construct_scalar(node)
    lowered = text.lower()
    if lowered.endswith(".inf"):
        return float("-inf") if text.startswith("-") else float("inf")
    if lowered.endswith(".nan"):
        return float("nan")
    return float(text)


Loader.add_constructor("tag:yaml.org,2002:int", _int_12)
Loader.add_constructor("tag:yaml.org,2002:float", _float_12)

_narrow(Loader)

# The fence: three or more dashes on their own line, then the block, then a line
# of dashes again. Nothing before it, or it is not frontmatter but a horizontal
# rule in the middle of a note.
FENCE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def split(raw: str) -> tuple[dict[str, Any], str]:
    """Frontmatter and body. A note without one gives an empty dict and itself.

    A block that does not parse is not an error either: it is somebody's note,
    written by hand, and half a broken colon must not make it unreadable. The
    body is then everything, including the block, which is what shows the person
    what is wrong.
    """
    # A byte order mark sits before the first `---` in files that came from an
    # editor on Windows, and a fence anchored to the start of the string then
    # does not match: the block is invisible, its tags and its title are gone,
    # and nothing says why. Found on one note in this vault, which cost it three
    # tags. Dropped from the body as well, so a `# Heading` on the first line
    # stays a heading.
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    m = FENCE.match(raw)
    if not m:
        return {}, raw
    try:
        data = yaml.load(m.group(1), Loader=Loader)
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(data, dict):
        return {}, raw
    return data, raw[m.end():]
