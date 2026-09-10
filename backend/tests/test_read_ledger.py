"""One copy of a note per run, not seven.

The occasion is run 2511: a one line change to a day's note took eight and a half minutes,
48 model rounds and 29.044 output tokens. The tools themselves took 3,5 seconds of that.
What made it long was the same note arriving in the history over and over — seven times for
`ADHS-Abklärung.md`, seven for the day's note — until the context hit the compaction
threshold, which cost another 78 seconds and the whole prompt cache.
"""
from __future__ import annotations

import json

from app.worker.read_ledger import ReadLedger


def answer(path: str, digest: str, text: str, *, chars: int | None = None) -> str:
    return json.dumps({"path": path, "content": text, "hash": digest,
                       "chars": chars if chars is not None else len(text),
                       "chars_returned": len(text), "truncated": False,
                       "properties": {"status": "aktiv"}, "tags": ["#x"]})


def test_the_first_read_comes_through_whole() -> None:
    led = ReadLedger()
    out = led.filter("vault__notes_read", answer("A.md", "h1", "Zwiebelkuchen"), 1)
    assert json.loads(out)["content"] == "Zwiebelkuchen"
    assert led.repeats == 0


def test_the_same_version_again_arrives_without_its_text() -> None:
    led = ReadLedger()
    led.filter("vault__notes_read", answer("A.md", "h1", "Zwiebelkuchen"), 1)
    out = json.loads(led.filter("vault__notes_read", answer("A.md", "h1", "Zwiebelkuchen"), 5))

    assert out["content"] == ""
    assert "round 1" in out["already_delivered"]
    # What a second read is legitimately after stays: the identity, the size, the top of it.
    assert out["hash"] == "h1"
    assert out["chars"] == len("Zwiebelkuchen")
    assert out["properties"]["status"] == "aktiv"
    assert led.repeats == 1


def test_a_note_that_changed_is_a_different_version_and_comes_through() -> None:
    """The identity is the key, so a write in between simply produces a new one. Nobody has
    to remember to tell the ledger about it."""
    led = ReadLedger()
    led.filter("vault__notes_read", answer("A.md", "h1", "alt"), 1)
    out = led.filter("vault__notes_read", answer("A.md", "h2", "neu"), 2)
    assert json.loads(out)["content"] == "neu"


def test_a_bigger_read_of_the_same_version_is_not_swallowed() -> None:
    """A first read cut off at 40.000 characters and a second asking for 120.000 are not
    the same answer. Only a repeat that carries no more than what was delivered is short."""
    led = ReadLedger()
    led.filter("vault__notes_read", answer("A.md", "h1", "x" * 100, chars=500), 1)
    out = led.filter("vault__notes_read", answer("A.md", "h1", "x" * 500, chars=500), 2)
    assert len(json.loads(out)["content"]) == 500
    # And after that the bigger one counts as what the run holds.
    again = led.filter("vault__notes_read", answer("A.md", "h1", "x" * 500, chars=500), 3)
    assert json.loads(again)["content"] == ""


def test_after_a_compaction_the_run_may_be_told_again() -> None:
    """The dangerous case. Once the middle of the history is a summary, the text really is
    gone — pointing the run back at it would send it looking for something that is not
    there."""
    led = ReadLedger()
    led.filter("vault__notes_read", answer("A.md", "h1", "Zwiebelkuchen"), 1)
    led.forget_all()
    out = led.filter("vault__notes_read", answer("A.md", "h1", "Zwiebelkuchen"), 9)
    assert json.loads(out)["content"] == "Zwiebelkuchen"


def test_every_other_tool_is_left_alone() -> None:
    led = ReadLedger()
    same = answer("A.md", "h1", "Zwiebelkuchen")
    assert led.filter("vault__notes_search", same, 1) == same
    assert led.filter("vault__notes_search", same, 2) == same
    assert led.filter("some__other_tool", "no json at all", 1) == "no json at all"
    assert led.filter("vault__notes_read", "{kaputt", 1) == "{kaputt"
    assert led.repeats == 0


# ------------------------------------------------------- the cut in the runtime

def test_the_note_tools_get_the_frame_their_own_limit_needs() -> None:
    """The blanket 8.000 cut straight through a note answer.

    `notes_read` bounds itself at 40.000 characters and wraps that in JSON. Cut at 8.000, a
    note of 13.650 characters arrived severed mid-string while its own envelope went on
    saying `chars_returned: 13650`. Asking again with a larger `max_chars` changed nothing,
    because the cut was never the tool's.
    """
    from app.worker.runtime import _cap_for, MAX_HTTP_TOOL_CHARS, MAX_NOTE_TOOL_CHARS

    assert _cap_for("vault__notes_read") == MAX_NOTE_TOOL_CHARS
    assert _cap_for("notes_diff") == MAX_NOTE_TOOL_CHARS
    assert _cap_for("traccoon_http_call") == MAX_HTTP_TOOL_CHARS
    assert _cap_for("imap__search_emails") == 8000
    # Room for what the tool promises plus its envelope.
    from app.services.notes_mcp import DEFAULT_MAX_CHARS
    assert MAX_NOTE_TOOL_CHARS > DEFAULT_MAX_CHARS


def test_a_cut_says_that_it_happened() -> None:
    """A silent cut is read as the tool's doing and answered with another call."""
    from app.worker.runtime import _cut

    assert _cut("kurz", 100) == "kurz"
    out = _cut("x" * 200, 100)
    assert out.startswith("x" * 100)
    assert "cut off here by the runtime" in out
    assert "100 of 200 characters" in out


def test_an_answer_that_gets_cut_counts_as_not_delivered() -> None:
    """The two shortenings must not work against each other.

    If the runtime cuts the answer afterwards, the run never held all of it — and pointing a
    later read at that copy would send it to a note that stops mid-sentence.
    """
    led = ReadLedger()
    big = answer("A.md", "h1", "x" * 5_000)
    led.filter("vault__notes_read", big, 1, cap=1_000)
    out = led.filter("vault__notes_read", big, 2, cap=48_000)
    assert len(json.loads(out)["content"]) == 5_000
    assert led.repeats == 0


# ------------------------------------------------------- writing a query back out

def test_a_part_of_a_query_can_be_quoted_back_to_whoever_wrote_it() -> None:
    """`why_nothing` names a part, and "part 2 of your query" helps nobody. The parser keeps
    no source text, so the part is written back out of what was parsed."""
    from app.notes.query.search import parse_query, render

    for text in ['path:"05 Daily Notes"', '"a phrase"', "/^- \\[ \\]/", "-nope",
                 'tag:#idea path:"02 Projekte"', "a OR b"]:
        assert render(parse_query(text)) == text
