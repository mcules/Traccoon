"""Text that came from outside the process, made sendable.

`\\udcc5` is not a character. It is what Python leaves behind when it decodes a byte it cannot
make sense of with `errors="surrogateescape"` — the scheme that lets an undecodable byte
survive a round trip through `str`. Linux hands filenames, environment variables and directory
listings over exactly that way, and a foreign MCP server may have done the same with a mail
body or a note before it ever reached us. `0xC5` is `Å` in cp1252, so one file on a mounted
volume with a name in the wrong encoding is enough.

Such a string looks completely normal. It survives being concatenated, cut, logged in the
terminal and written into JSON. It falls over in exactly one place: `str.encode("utf-8")`
refuses a lone surrogate by design — at the HTTP boundary to the provider, and on the way into
Postgres. That is why the error names a position in a 75 kB prompt and says nothing at all
about where the byte came from, and why the run dies AFTER the work is done: context gathered,
tools run, model time paid for, everything thrown away at the encode.

So it is caught at the seams instead: where a tool result becomes a message, and where an MCP
answer becomes a string. `backslashreplace` keeps the evidence readable (`\\udcc5` stays
visible as text) rather than swallowing the byte — a mojibake filename can still be recognised
afterwards in the log and in the answer.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("traccoon.worker.text")

# A scan without an allocation. `text.encode(...)` in a try block would copy 75 kB on every
# single message just to find out that nothing is wrong.
_SURROGATE = re.compile("[\ud800-\udfff]")

# How much of the offending text goes into the log. Enough to recognise a filename, not enough
# to put the content of a mail into the log file.
_EXCERPT = 120


def has_surrogates(text: str) -> bool:
    """Is there anything in here that `encode("utf-8")` would refuse?"""
    return bool(_SURROGATE.search(text))


def scrub_surrogates(text: str) -> str:
    """Lone surrogates cannot be encoded as UTF-8 and kill the request at the provider.

    They come in from foreign tool output and from filesystem paths, which Python decodes with
    `surrogateescape`. `backslashreplace` keeps the evidence readable (`\\udcc5`) instead of
    silently swallowing bytes, so a mojibake filename can still be recognised in the log.

    Text without surrogates comes back unchanged — the same object, not a copy.
    """
    if not text or not has_surrogates(text):
        return text
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _excerpt(text: str) -> str:
    """The part around the first surrogate — the beginning of a 75 kB string says nothing."""
    hit = _SURROGATE.search(text)
    start = max(0, (hit.start() if hit else 0) - _EXCERPT // 2)
    return repr(text[start:start + _EXCERPT])


class MojibakeWatch:
    """Scrubs, and says ONCE per run which tool it came from.

    One tool called twenty times must not write twenty identical lines; but without the name
    the next occurrence is the same detective work from scratch — a codec error at position
    75839 points at the encode, never at the origin. So: once per source, and the source is
    the tool name.

    One of these belongs to one run. It holds no global state, so two runs side by side do not
    silence each other.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def clean(self, text: str, source: str) -> str:
        if not isinstance(text, str) or not text or not has_surrogates(text):
            return text
        if source not in self._seen:
            self._seen.add(source)
            log.warning("Undecodable bytes from %s — scrubbed (otherwise the run dies at the "
                        "encode). Excerpt: %s", source, _excerpt(text))
        return scrub_surrogates(text)

    def clean_deep(self, value, source: str):
        """The same for a block result: a list of dicts with text in them somewhere."""
        if isinstance(value, str):
            return self.clean(value, source)
        if isinstance(value, list):
            return [self.clean_deep(v, source) for v in value]
        if isinstance(value, dict):
            return {k: self.clean_deep(v, source) for k, v in value.items()}
        return value


def repair_messages(messages) -> str:
    """Last line of defence right before the request: scrub the assembled message list.

    Deliberately IN PLACE. The caller (`runtime.run_agent`) keeps this list as the history of
    the run and sends it again every round; a copy would be clean once and the original would
    carry the bad byte into the next round, and into the round after that. Repaired here, it
    is repaired for good — one warning instead of forty.

    Returns a short description of the first spot it had to touch, or "" when there was
    nothing to do (the normal case, and the cheap one: a scan without an allocation).
    """
    found = ""

    def fix(value, where: str):
        nonlocal found
        if isinstance(value, str):
            if has_surrogates(value):
                if not found:
                    found = f"{where} {_excerpt(value)}"
                return scrub_surrogates(value)
            return value
        if isinstance(value, list):
            return [fix(v, where) for v in value]
        if isinstance(value, dict):
            for k, v in value.items():
                value[k] = fix(v, where)
            return value
        return value

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        where = str(msg.get("name") or msg.get("role") or "?")
        for key, value in list(msg.items()):
            msg[key] = fix(value, where)
    return found
