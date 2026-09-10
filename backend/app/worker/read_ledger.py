"""Hand a note's text to a run once, not seven times.

Run 2511 read `ADHS-Abklärung.md` (13.650 characters) seven times and the day's note seven
times, in eight and a half minutes of work on a one line change. Nothing had changed in
between: every one of those reads came back with the same `hash`. The cost is paid twice —
once as the round trip that fetches it, once as the copy that stays in the history and
pushes the context towards the compaction threshold, which then costs a summarising run and
the whole prompt cache.

So the run keeps a note of which version of which note it has already been given in full.
A repeat gets the same answer without the `content`: the identity, the length, the
properties and a sentence saying where the text already stands. Everything a caller
legitimately wants from a second read (has it changed? how long is it? what is on top?) is
in that answer; only the copy is gone.

Two things this must not do, and both are the reason it is a class and not a dict:

1. **It must not lie after a compaction.** Once the middle of the history has been replaced
   by a summary, the text really IS gone, and pointing at it would send the run looking for
   something that is not there. `forget_all` is called at exactly that moment.
2. **It must not swallow a bigger read.** A first read cut off at 40.000 characters and a
   second one asking for 120.000 are not the same answer. Only a repeat that carries no more
   than what was already delivered is shortened.
"""
from __future__ import annotations

import json

# What the shortened answer keeps of the text. Nothing: the point is to not send the copy.
# The fields around it (hash, chars, properties, tags) stay, because they are small and are
# what a second read is legitimately after.
_SENTENCE = ("You already have this note in full and unchanged (same hash) from round "
             "{round} of this run. Read it there instead of asking for it again.")


class ReadLedger:
    """Which note versions this run has already been given, and how much of each."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], tuple[int, int]] = {}   # (path, hash) -> (chars, round)
        self.saved_chars = 0
        self.repeats = 0

    def forget_all(self) -> None:
        """After a compaction: what the run was given may no longer be in its history."""
        self._seen.clear()

    def filter(self, tool_name: str, result: str, round_no: int, cap: int = 0) -> str:
        """The tool answer, with the text taken out when the run already has that version.

        `cap` is what the runtime will let through into the context afterwards. An answer
        that does not fit is noted as NOT delivered: the run never saw all of it, so a later
        read of the same version has to come through in full rather than be pointed at a
        copy that was cut short.
        """
        if not _is_read(tool_name) or not result.startswith("{"):
            return result
        if cap and len(result) > cap:
            return result
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            return result
        if not isinstance(data, dict):
            return result
        path, digest = data.get("path"), data.get("hash")
        content = data.get("content")
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(content, str):
            return result
        got = int(data.get("chars_returned") or len(content))
        key = (path, digest)
        had = self._seen.get(key)
        if had is None or got > had[0]:
            self._seen[key] = (got, round_no)
            return result
        self.repeats += 1
        self.saved_chars += len(content)
        data["content"] = ""
        data["already_delivered"] = _SENTENCE.format(round=had[1])
        return json.dumps(data, ensure_ascii=False)


def _is_read(tool_name: str) -> bool:
    """`vault__notes_read`, `notes_read` — the prefix belongs to the server, not the tool."""
    return tool_name.rsplit("__", 1)[-1] == "notes_read"
